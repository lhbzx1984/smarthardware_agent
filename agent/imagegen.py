"""产品图像生成：渲染图与爆炸图。

服务端按 base_url 判定（Agnes / SiliconFlow），端点与密钥必须同源 ——
拿 A 家的 key 打 B 家的端点，网关只会回 401 Invalid token，看不出是配错了。

已实测（2026-10-15）：
  Agnes        POST https://apihub.agnes-ai.com/v1/images/generations
               model = agnes-image-2.5-flash，约 14-17s / 约 680KB，返回 data[0].url
               （网关注册 2.0 / 2.1 / 2.5 三代，三代实测都能出图；默认取最新一代）
  SiliconFlow  POST https://api.siliconflow.cn/v1/images/generations
               model = black-forest-labs/FLUX.1-schnell，返回 images[0].url
"""
from __future__ import annotations

import base64
import io
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import config

DEFAULT_MODEL = config.AGNES_IMAGE_MODEL or "agnes-image-2.5-flash"
TIMEOUT = 180
MAX_CONCURRENCY = 2           # 同一时刻最多 2 张，避免网关把突发当成异常流量
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
RETRY_TIMES = 2               # 网络/限流类失败最多重试 2 次；鉴权类一次都不试

# 产品设计的画质基线：视角、光照、背景一次性钉死，模型只需补主体
STYLE_RENDER = (
    "产品级工业设计渲染图，等轴测三点透视，柔和三点布光，"
    "纯色无缝背景，材质细节清晰，边缘锐利，8K 质感，写实风格"
)
STYLE_EXPLODED = (
    "产品爆炸视图（exploded view），各零件沿装配轴分离排列并带虚线引导线，"
    "零件编号清晰，等轴测视角，白色背景，技术插图风格，线条干净"
)
# 图生图模式的一致性锚：参考图已经定义了这个产品，后续所有图都只能改视角/构图，
# 不能改产品本身。实测不加这句，模型会把同一产品画出两代不同的外形。
STYLE_CONSISTENT = (
    "严格保持参考图中的同一个产品：外形、比例、材质、配色、表面细节、结构特征完全一致，"
    "不得改变产品设计；只允许按下面的要求改变视角、构图与背景"
)

# 参考图经 base64 后体积翻 1.37 倍，网关对超大 body 很敏感 —— 先压到这张上限内
REF_MAX_EDGE = 1024
REF_JPEG_QUALITY = 82
REF_MAX_BYTES = 2 * 1024 * 1024

_gate = threading.BoundedSemaphore(MAX_CONCURRENCY)


def _ssl_ctx():
    """默认 None（走系统证书）；配了 CA_BUNDLE / 关校验时才自建 context。"""
    import ssl

    v = config.ssl_verify()
    if v is True:
        return None
    if isinstance(v, str):
        return ssl.create_default_context(cafile=v)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _ssl_chain() -> list[tuple[Any, str]]:
    """下载出图结果时依次尝试的 TLS 策略。

    实测：出图接口（apihub.agnes-ai.com）能过，但 CDN 直链
    （platform-outputs.agnes-ai.space）报 `self-signed certificate` ——
    本机链路上有 HTTPS 拦截（代理 / 杀毒 / 上网行为管理），系统证书链不认它。
    图已经生成好了，只因为下载这一跳把整张图作废太亏，所以逐级放宽：
    系统证书 → certifi 证书 → 关闭校验。
    """
    import ssl

    chain: list[tuple[Any, str]] = []
    v = config.ssl_verify()
    if isinstance(v, str):
        chain.append((ssl.create_default_context(cafile=v), f"自定义 CA {v}"))
        return chain
    if v is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        chain.append((ctx, "已按配置关闭证书校验"))
        return chain

    chain.append((None, "系统证书"))
    try:
        import certifi

        chain.append((ssl.create_default_context(cafile=certifi.where()), "certifi 证书"))
    except Exception:  # noqa: BLE001  certifi 没装就跳过这一档
        pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    chain.append((ctx, "关闭证书校验"))
    return chain


def _is_cert_error(e: BaseException) -> bool:
    s = f"{type(e).__name__}: {e}"
    return "CERTIFICATE_VERIFY_FAILED" in s or "SSL" in s.upper() and "VERIFY" in s.upper()


def _post(url: str, payload: dict[str, Any], api_key: str) -> tuple[int, Any, float]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_ctx()) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw), time.time() - t0
            except json.JSONDecodeError:
                return r.status, raw[:300], time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400], time.time() - t0
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"[:300], time.time() - t0


def _extract(data: Any) -> tuple[str, str]:
    """从各家响应里抠出图片。返回 (url, b64)；都没有就是空。"""
    if isinstance(data, dict):
        for k in ("data", "images", "results", "output"):
            v = data.get(k)
            if isinstance(v, list) and v:
                item = v[0]
                if isinstance(item, dict):
                    url = item.get("url") or item.get("image_url") or ""
                    b64 = _clean_b64(item.get("b64_json") or item.get("image") or "")
                    if url or b64:
                        return url, b64
        # 有些网关直接把 url 摊平在顶层
        if isinstance(data.get("url"), str):
            return data["url"], ""
        if isinstance(data.get("b64_json"), str):
            return "", _clean_b64(data["b64_json"])
    return "", ""


def _clean_b64(raw: Any) -> str:
    """抠出纯 base64：有些网关会带 `data:image/png;base64,` 前缀。"""
    s = str(raw or "")
    if "," in s[:64] and s[:5].lower() == "data:":
        s = s.split(",", 1)[1]
    return s.strip()


def _download(url: str, dest: Path, tries: int = 3) -> tuple[int, str]:
    """下载出图结果。返回 (字节数, TLS 策略说明)。

    网关返回的是 CDN 直链，取图和调接口是两次独立的网络往返 —— DNS 抖动 /
    连接被重置在这里是常态。实测出现过「图已生成但 getaddrinfo failed」，
    一次抖动就把整张图作废，代价太大（十几秒生成 + 一致性链断掉），所以重试。

    证书校验失败不算失败：逐级换 TLS 策略（详见 `_ssl_chain`）立刻重试，
    而不是干等退避 —— 自签证书那一档重试多少次都不会变好。
    """
    last: Exception | None = None
    chain = _ssl_chain()
    for i in range(tries):
        ctx, note = chain[min(i, len(chain) - 1)]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
                data = r.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return len(data), note
        except Exception as e:  # noqa: BLE001
            last = e
            # 证书问题：换下一档 TLS 策略马上再试，别把重试次数浪费在同一档上
            if _is_cert_error(e) and min(i, len(chain) - 1) + 1 < len(chain):
                continue
            if i < tries - 1:
                time.sleep(2 ** i * 2)
    host = url.split("//")[-1].split("/")[0]
    raise RuntimeError(f"下载失败（{host}）：{type(last).__name__}: {last}") from last


def _fail(host: str, model: str, key: str, status: Any, detail: Any, hint: str = "") -> dict[str, Any]:
    msg = f"[{host}] {model} 返回 {status}：{detail}"
    if hint:
        msg += f"\n{hint}"
    return {
        "ok": False,
        "retryable": False,
        "error": msg,
        "host": host,
        "model": model,
        "key": config.mask(key),
        "status": status,
    }


def encode_reference(src: str | Path) -> tuple[str, str]:
    """把参考图编成 data URI。返回 (data_uri, 说明)；失败时 data_uri 为空。

    有 Pillow 就先缩放 + 转 JPEG（同样清晰度下体积通常降一个数量级），
    没有 Pillow 就直接原样 base64 —— 只要不超限就能用。
    """
    p = Path(src)
    if not p.exists() or p.stat().st_size == 0:
        return "", f"参考图不存在或为空：{p}"
    try:
        from PIL import Image  # noqa: PLC0415  可选依赖，缺了走原图直传

        with Image.open(p) as im:
            im = im.convert("RGB")
            if max(im.size) > REF_MAX_EDGE:
                ratio = REF_MAX_EDGE / max(im.size)
                im = im.resize(
                    (max(1, int(im.width * ratio)), max(1, int(im.height * ratio))), Image.LANCZOS
                )
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=REF_JPEG_QUALITY, optimize=True)
            raw = buf.getvalue()
            # 高频细节多的图 JPEG 可能反而更大；原图更小就用原图
            orig = p.read_bytes()
            if len(orig) < len(raw) and len(orig) <= REF_MAX_BYTES:
                raw = orig
                note = f"原图更小，直传 {len(raw)//1024}KB"
            else:
                note = f"已压缩为 {max(im.size)}px JPEG {len(raw)//1024}KB"
            if len(raw) > REF_MAX_BYTES:
                # 还是太大：降质量再压一次，宁可糊一点也要塞进网关
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=60, optimize=True)
                raw = buf.getvalue()
                note = f"二次压缩 {max(im.size)}px JPEG {len(raw)//1024}KB"
    except Exception:  # noqa: BLE001  没装 Pillow / 图坏了：退回原图，网关能认就行
        raw = p.read_bytes()
        if len(raw) > REF_MAX_BYTES:
            return "", f"参考图过大（{len(raw)//1024}KB）且无法压缩，改用文生图"
        note = f"原图直传 {len(raw)//1024}KB"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}", note


def generate(
    prompt: str,
    dest: Path,
    api_key: str = "",
    kind: str = "render",
    size: str = "1024x1024",
    model: str = "",
    base_url: str = "",
    reference: str | Path = "",
) -> dict[str, Any]:
    """生成一张图并落盘。返回 {ok, path, url, bytes, error, elapsed, key, referenced}。

    reference 非空时走图生图：网关会切到 i2i 队列（返回路径含 /images/i2i/），
    产品外观以参考图为准，prompt 只描述「要改成什么样」。
    """
    endpoint, model_name = config.resolve_image(base_url, model)
    key = (api_key or "").strip() or config.fallback_key(base_url or config.DEFAULT_BASE_URL)
    if not key:
        return {
            "ok": False,
            "retryable": False,
            "error": "未配置 API Key：在页面「模型设置」里填，或在 .env 里设 AGNES_API_KEY",
        }

    host = endpoint.split("//")[-1].split("/")[0]
    style = STYLE_EXPLODED if kind == "exploded" else STYLE_RENDER
    full = f"{prompt}。{style}"
    if reference:
        # 一致性锚必须排在风格锚之前：先锁产品，再锁画风
        full = f"{STYLE_CONSISTENT}。{prompt}。{style}"
    # 审核较严，避开容易被拒的词
    for bad, good in (("赛博朋克", "未来感"), ("科幻", "现代科技感")):
        full = full.replace(bad, good)

    url = endpoint.rstrip("/") + "/images/generations"
    payload: dict[str, Any] = {"model": model_name, "prompt": full, "size": size}
    # 直接要 base64：省掉「再去 CDN 下载一次」这一跳。
    # 实测 CDN 直链（platform-outputs.agnes-ai.space）在本机链路上会被 TLS 拦截 /
    # 连接重置搞挂 —— 图明明生成好了，却只因为下载失败整张作废，太亏。
    if config.IMAGE_B64:
        payload["response_format"] = "b64_json"
    ref_note = ""
    if reference:
        data_uri, ref_note = encode_reference(reference)
        if not data_uri:
            # 参考图不可用：与其编张不像的图，不如退回文生图并如实说明
            return {
                "ok": False,
                "retryable": True,
                "error": f"图生图已跳过（{ref_note}），本次改为文生图，产品一致性无法保证",
                "host": host,
                "model": model_name,
            }
        # 实测：只有 image 字段会被路由到 i2i 队列，images/reference_image/image_url 一律 400
        payload["image"] = data_uri

    t0 = time.time()
    status, data, _dt = 0, None, 0.0
    ref_fallback = False
    with _gate:  # 限并发，网关对突发流量很敏感
        for attempt in range(RETRY_TIMES + 1):
            status, data, _dt = _post(url, payload, key)
            if status == 200:
                break
            detail = json.dumps(data, ensure_ascii=False)[:400].lower()
            # 网关/模型若不接受 image 字段，退回文生图再试一次，别整轮失败
            if (
                payload.get("image")
                and status == 400
                and "image" in detail
                and "not supported" in detail
            ):
                payload.pop("image", None)
                ref_fallback = True
                status, data, _dt = _post(url, payload, key)
                if status == 200:
                    break
            # 网关不认 response_format：退回下载直链模式（下载自带 TLS 降级）
            if payload.get("response_format") and status == 400 and "response_format" in detail:
                payload.pop("response_format", None)
                status, data, _dt = _post(url, payload, key)
                if status == 200:
                    break
            if attempt >= RETRY_TIMES or status in (401, 403):
                break
            if status not in RETRY_STATUS and status != -1:
                break
            time.sleep(2 ** attempt * 2)  # 2s / 4s

    if status != 200:
        if status in (401, 403):
            return _fail(
                host, model_name, key, status, data,
                hint=(
                    f"鉴权失败，重试无意义。当前使用的密钥指纹 {config.mask(key)}，"
                    f"目标网关 {host}。请确认这把 key 是 {host} 签发的，"
                    "并检查 .env 是否被同名环境变量遮蔽（本项目已改为 .env 优先）。"
                ),
            )
        return _fail(host, model_name, key, status, data,
                     hint="服务端临时故障/限流，稍后可重试。" if status in RETRY_STATUS else "")

    img_url, b64 = _extract(data)
    if not img_url and not b64:
        return _fail(host, model_name, key, status,
                     json.dumps(data, ensure_ascii=False)[:200] if not isinstance(data, str) else data,
                     hint="网关没返回图片地址，可能是该模型不支持文生图。")

    dest = Path(dest)
    tls = ""
    try:
        if b64:
            # 网关直出 base64：不经过 CDN，本机 TLS 链路的问题影响不到它
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(b64))
            nbytes = len(dest.read_bytes())
            tls = "网关直出 base64（未走 CDN 下载）"
        else:
            nbytes, tls = _download(img_url, dest)
    except Exception as e:  # noqa: BLE001  下载失败也把远端地址回给用户，不丢成果
        return {
            "ok": False,
            "retryable": True,
            "error": f"图片已生成但落盘失败：{e}",
            "url": img_url,
            "key": config.mask(key),
        }

    return {
        "ok": True,
        "path": str(dest),
        "url": img_url,
        "tls": tls,
        "bytes": nbytes,
        "elapsed": round(time.time() - t0, 1),
        "prompt": full,
        "model": model_name,
        "key": config.mask(key),
        "referenced": ("" if ref_fallback else str(reference or "")),
        "ref_note": (("参考图未被网关接受，已退回文生图" if ref_fallback else ref_note) if reference else ""),
    }
