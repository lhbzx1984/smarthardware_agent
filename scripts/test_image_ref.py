"""产品图一致性自检：第二张起基于前一张做图生图。

钉死四件事：
1. 参考图能被压成网关吃得下的 data URI（大图不能把 body 撑爆）。
2. 命名自动顺延、已存在的文件不被覆盖 —— 覆盖会直接毁掉参考图链。
3. 参考图挑选规则：渲染图跟上一张，爆炸图跟第一张（产品基线），第一张走文生图。
4. 端到端：三次串行调用形成一条 reference 链，并写进 prompts.json。

用法：python scripts/test_image_ref.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import imagegen  # noqa: E402
import tools  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def fake_png(path: Path, w: int = 1600, h: int = 1600) -> Path:
    """造一张足够大的真 PNG，验证压缩真的生效。"""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 4096)
        return path
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            px[x, y] = (x % 255, y % 255, (x + y) % 255)
    im.save(path, format="PNG")
    return path


def test_encode_reference() -> None:
    print("[1] 参考图编码：压缩到网关吃得下")
    tmp = Path(tempfile.mkdtemp())
    src = fake_png(tmp / "big.png")
    uri, note = imagegen.encode_reference(src)
    if not uri:
        check("能编出 data URI", False, note)
        return
    check("data URI 前缀正确", uri.startswith("data:image/jpeg;base64,"), uri[:40])
    raw = src.stat().st_size
    import base64

    body = len(uri) - len("data:image/jpeg;base64,")
    check("base64 后仍在上限内", body <= imagegen.REF_MAX_BYTES * 1.4, f"{body//1024}KB / 原图 {raw//1024}KB")
    print(f"       原图 {raw//1024}KB → {note}")

    empty = tmp / "empty.png"
    empty.write_bytes(b"")
    check("空文件被拒", imagegen.encode_reference(empty)[0] == "")
    check("不存在的文件被拒", imagegen.encode_reference(tmp / "nope.png")[0] == "")


def test_naming() -> None:
    print("[2] 命名：自动顺延 + 覆盖保护")
    tmp = Path(tempfile.mkdtemp())
    events: list[dict] = []
    tools.bind_context(tmp, events.append, {})
    (tmp / "10_appearance").mkdir(parents=True, exist_ok=True)

    check("首张 render 命名 01", tools._auto_filename("render") == "10_appearance/render-01.png",
          tools._auto_filename("render"))
    check("首张 exploded 命名 01", tools._auto_filename("exploded") == "20_structure/exploded-01.png")

    (tmp / "10_appearance" / "render-01.png").write_bytes(b"x" * 32)
    check("已有 01 则顺延到 02", tools._auto_filename("render") == "10_appearance/render-02.png",
          tools._auto_filename("render"))
    check("显式指定已存在的路径会顺延",
          tools._dedupe_rel("10_appearance/render-01.png") == "10_appearance/render-01-02.png",
          tools._dedupe_rel("10_appearance/render-01.png"))
    check("未存在的路径原样返回",
          tools._dedupe_rel("10_appearance/render-05.png") == "10_appearance/render-05.png")


def test_pick_reference() -> None:
    print("[3] 参考图挑选：渲染跟上一张，爆炸跟第一张")
    tmp = Path(tempfile.mkdtemp())
    tools.bind_context(tmp, lambda e: None, {})
    check("无存档 → 无参考图", tools._pick_reference("render")[0] is None)

    (tmp / "10_appearance").mkdir(parents=True, exist_ok=True)
    (tmp / "10_appearance" / "render-01.png").write_bytes(b"x" * 32)
    (tmp / "10_appearance" / "render-02.png").write_bytes(b"x" * 32)
    (tmp / "10_appearance" / "prompts.json").write_text(
        '{"items":[{"kind":"render","file":"10_appearance/render-01.png"},'
        '{"kind":"render","file":"10_appearance/render-02.png"}]}',
        encoding="utf-8",
    )
    p, rel = tools._pick_reference("render")
    check("渲染图跟上一张", rel == "10_appearance/render-02.png", rel)
    p2, rel2 = tools._pick_reference("exploded")
    check("爆炸图跟第一张（产品基线）", rel2 == "10_appearance/render-01.png", rel2)
    check("参考图文件真实存在", p is not None and p.exists() and p2 is not None and p2.exists())

    # 记录里写了路径但文件没落盘（上一张其实失败了）→ 不能拿空气当参考
    (tmp / "10_appearance" / "prompts.json").write_text(
        '{"items":[{"kind":"render","file":"10_appearance/render-09.png"}]}', encoding="utf-8"
    )
    check("记录里文件不存在 → 不用作参考", tools._pick_reference("render")[0] is None)


def test_resolve_reference() -> None:
    print("[4] reference 参数解析")
    tmp = Path(tempfile.mkdtemp())
    tools.bind_context(tmp, lambda e: None, {})
    (tmp / "10_appearance").mkdir(parents=True, exist_ok=True)
    (tmp / "10_appearance" / "render-01.png").write_bytes(b"x" * 32)
    (tmp / "10_appearance" / "prompts.json").write_text(
        '{"items":[{"kind":"render","file":"10_appearance/render-01.png"}]}', encoding="utf-8"
    )

    p, _rel, note = tools._resolve_reference("", "render")
    check("默认 auto 会挑到参考图", p is not None and "基于已有产品图" in note, note)
    p, _rel, note = tools._resolve_reference("none", "render")
    check("none 强制文生图", p is None and "关闭参考图" in note, note)
    p, _rel, note = tools._resolve_reference("10_appearance/render-01.png", "render")
    check("指定路径可用", p is not None and "按指定参考图" in note, note)
    p, _rel, note = tools._resolve_reference("10_appearance/nope.png", "render")
    check("指定路径不存在 → 退回文生图", p is None and "改为文生图" in note, note)


def test_chain() -> None:
    print("[5] 端到端：三次串行调用形成 reference 链")
    tmp = Path(tempfile.mkdtemp())
    events: list[dict] = []
    tools.bind_context(tmp, events.append, {})

    # 模拟真实情况：模型先手写了一份更详细的 prompts.json（结构阶段靠它做外形约束）
    import json

    (tmp / "10_appearance").mkdir(parents=True, exist_ok=True)
    (tmp / "10_appearance" / "prompts.json").write_text(
        json.dumps({
            "product": "圆柱小夜灯",
            "prompts": [{"id": 1, "purpose": "基线", "prompt": "哑光米白圆柱，90×120mm"}],
            "structure_notes": "外壳外径 90mm、总高 120mm，顶盖内嵌 1.3 寸 OLED",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    calls: list[dict[str, Any]] = []

    def fake_generate(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        dest = Path(kw["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        return {
            "ok": True, "bytes": dest.stat().st_size, "elapsed": 1.0,
            "model": "agnes-image-2.1-flash", "prompt": kw["prompt"],
            "referenced": kw.get("reference") or "", "ref_note": "已压缩为 1024px JPEG 120KB",
        }

    imagegen.generate = fake_generate  # type: ignore[assignment]

    out1 = tools.generate_product_image.invoke({
        "prompt": "圆柱形桌面小夜灯，直径 90mm 高 120mm，哑光米白外壳",
        "kind": "render",
    })
    check("第一张成功", "已生成" in out1, out1[:120])
    check("第一张无参考（文生图基线）", not (calls[-1].get("reference")), str(calls[-1].get("reference")))
    check("第一张落在 render-01", "render-01.png" in out1, out1[:160])

    out2 = tools.generate_product_image.invoke({
        "prompt": "换成 45 度侧视，放到木质桌面上",
        "kind": "render",
    })
    check("第二张成功", "已生成" in out2, out2[:120])
    check("第二张带参考图", bool(calls[-1].get("reference")), str(calls[-1].get("reference")))
    check("第二张参考的是第一张", "render-01.png" in str(calls[-1].get("reference")), str(calls[-1].get("reference")))
    check("第二张不覆盖第一张", "render-02.png" in out2, out2[:160])
    check("回执说明了一致性来源", "基于已有产品图" in out2, out2[:200])

    out3 = tools.generate_product_image.invoke({
        "prompt": "外壳、顶盖、主控板、电池沿装配轴分离",
        "kind": "exploded",
    })
    check("爆炸图成功", "已生成" in out3, out3[:120])
    check("爆炸图参考产品基线（第一张）", "render-01.png" in str(calls[-1].get("reference")), str(calls[-1].get("reference")))

    import json

    data = json.loads((tmp / "10_appearance" / "prompts.json").read_text(encoding="utf-8"))
    items = data.get("items") or []
    check("存档三条记录", len(items) == 3, str(len(items)))
    check("第一条 reference 为空", items[0].get("reference") in ("", None), str(items[0].get("reference")))
    check("第二条记录了参考图", "render-01.png" in str(items[1].get("reference")), str(items[1].get("reference")))
    check("第三条记录了参考图", "render-01.png" in str(items[2].get("reference")), str(items[2].get("reference")))
    # 模型手写的 prompts/structure_notes 是结构阶段唯一的外形约束来源，出图存档不能把它冲掉
    check("出图存档保住了模型手写的 prompts", len(data.get("prompts") or []) == 1, str(data.get("prompts"))[:80])
    check("出图存档保住了 structure_notes", bool(data.get("structure_notes")), str(data.get("structure_notes"))[:60])


def test_payload() -> None:
    print("[6] 网关请求体：带 image 走 i2i，且一致性锚进 prompt")
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, payload: dict[str, Any], api_key: str) -> tuple[int, Any, float]:
        sent.append(payload)
        return 200, {"data": [{"url": "https://example.com/out.png"}]}, 0.1

    imagegen._post = fake_post  # type: ignore[assignment]
    # 签名是 (字节数, TLS 策略说明)
    imagegen._download = lambda url, dest: (1024, "系统证书")  # type: ignore[assignment]

    tmp = Path(tempfile.mkdtemp())
    ref = fake_png(tmp / "ref.png")
    r = imagegen.generate(prompt="换个视角", dest=tmp / "out.png", kind="render", reference=ref)
    check("带参考图出图成功", bool(r.get("ok")), str(r.get("error"))[:160])
    if sent:
        check("请求体带 image 字段", "image" in sent[0], str(list(sent[0].keys())))
        check("image 是 data URI", str(sent[0].get("image", "")).startswith("data:image/"))
        check("prompt 含一致性锚", imagegen.STYLE_CONSISTENT.split("。")[0] in sent[0].get("prompt", ""))
        check("结果回带 referenced", "ref.png" in str(r.get("referenced")), str(r.get("referenced")))

    sent.clear()
    r2 = imagegen.generate(prompt="第一张", dest=tmp / "out2.png", kind="render")
    check("无参考图时不带 image", sent and "image" not in sent[0], str(list(sent[0].keys())) if sent else "")
    check("无参考图时无一致性锚", imagegen.STYLE_CONSISTENT.split("。")[0] not in (sent[0].get("prompt", "") if sent else ""))


def test_download_retry() -> None:
    print("[7] 出图下载：DNS/连接抖动不能让整张图作废")
    import importlib
    import urllib.request

    import imagegen

    # 前面的用例把 _download 换成了桩，这里要拿回真身
    imagegen = importlib.reload(imagegen)

    tmp = Path(tempfile.mkdtemp())
    dest = tmp / "a" / "b.png"
    calls = {"n": 0}

    def flaky(req: urllib.request.Request, *a: Any, **kw: Any) -> object:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("[Errno 11002] getaddrinfo failed")

        class R:
            def read(self) -> bytes:
                return b"PNG-BYTES"

            def __enter__(self) -> "R":
                return self

            def __exit__(self, *x: Any) -> bool:
                return False

        return R()

    orig = urllib.request.urlopen
    saved = getattr(imagegen, "time", time)
    urllib.request.urlopen = flaky  # type: ignore[assignment]
    try:
        imagegen.time = type("T", (), {"sleep": staticmethod(lambda s: None)})()  # type: ignore[assignment]
        n, _tls = imagegen._download("https://cdn.example.com/x.png", dest)
    finally:
        urllib.request.urlopen = orig  # type: ignore[assignment]
        imagegen.time = saved  # type: ignore[assignment]

    check("抖动两次后仍下载成功", n == len(b"PNG-BYTES"), str(n))
    check("确实重试了 3 次", calls["n"] == 3, str(calls["n"]))
    check("内容已落盘", dest.exists() and dest.read_bytes() == b"PNG-BYTES")

    # 重试耗尽：错误信息要能看出是哪个域名，否则没法排障
    calls["n"] = 0

    def always(req: urllib.request.Request, *a: Any, **kw: Any) -> object:
        calls["n"] += 1
        raise OSError("[Errno 11002] getaddrinfo failed")

    urllib.request.urlopen = always  # type: ignore[assignment]
    msg = ""
    try:
        imagegen.time = type("T", (), {"sleep": staticmethod(lambda s: None)})()  # type: ignore[assignment]
        imagegen._download("https://cdn.example.com/x.png", dest, tries=2)
    except RuntimeError as e:
        msg = str(e)
    finally:
        urllib.request.urlopen = orig  # type: ignore[assignment]
        imagegen.time = saved  # type: ignore[assignment]

    check("重试耗尽抛出", bool(msg))
    check("错误信息带域名（可排障）", "cdn.example.com" in msg, msg)


def main() -> int:
    test_encode_reference()
    test_naming()
    test_pick_reference()
    test_resolve_reference()
    test_payload()
    test_chain()
    test_download_retry()
    print(f"\n{'ALL PASS' if not FAIL else 'HAS FAILURE'}  pass={PASS} fail={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
