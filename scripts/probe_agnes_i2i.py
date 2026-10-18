"""探测 Agnes 网关的图生图（image-to-image）能力。

候选通道：
  A. POST /v1/images/edits      multipart/form-data（OpenAI 风格，image[] + prompt + model）
  B. POST /v1/images/generations 带 image / images / reference_image / image_url 字段
  C. POST /v1/images/generations 带 input_image（b64 data URI）

参考图用项目里已有的一张 PNG，避免额外出图开销。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import config  # noqa: E402


def ssl_ctx():
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


def base() -> str:
    b, _m, _p = config.resolve_image_provider()
    return b.rstrip("/")


def key() -> str:
    b, _m, _p = config.resolve_image_provider()
    return config.fallback_key(b)


def post_json(path: str, payload: dict, label: str) -> None:
    url = base() + path
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180, context=ssl_ctx()) as r:
            raw = r.read().decode("utf-8", "replace")
        print(f"[{label}] {r.status} {round(time.time()-t0,1)}s {raw[:260]}")
    except urllib.error.HTTPError as e:
        print(f"[{label}] HTTP {e.code} {round(time.time()-t0,1)}s {e.read().decode('utf-8','replace')[:260]}")
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] ERR {type(e).__name__}: {str(e)[:200]}")


def post_multipart(path: str, fields: dict, files: dict, label: str) -> None:
    url = base() + path
    boundary = "----probe" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
    for k, p in files.items():
        p = Path(p)
        mt = mimetypes.guess_type(p.name)[0] or "image/png"
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{p.name}\"\r\n"
            f"Content-Type: {mt}\r\n\r\n"
        ).encode("utf-8") + p.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key()}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180, context=ssl_ctx()) as r:
            raw = r.read().decode("utf-8", "replace")
        print(f"[{label}] {r.status} {round(time.time()-t0,1)}s {raw[:260]}")
    except urllib.error.HTTPError as e:
        print(f"[{label}] HTTP {e.code} {round(time.time()-t0,1)}s {e.read().decode('utf-8','replace')[:260]}")
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] ERR {type(e).__name__}: {str(e)[:200]}")


def main() -> int:
    ref = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("web/public/home/flow-appearance.png")
    if not ref.exists():
        print("参考图不存在:", ref)
        return 2
    print("参考图:", ref, ref.stat().st_size, "B")
    print("网关:", base(), "model:", config.resolve_image_provider()[1])

    model = config.resolve_image_provider()[1]
    b64 = base64.b64encode(ref.read_bytes()).decode()
    data_uri = f"data:image/png;base64,{b64}"

    # A. multipart /images/edits
    post_multipart(
        "/images/edits",
        {"model": model, "prompt": "保持该产品的外形、材质、配色与比例完全一致，只把视角换成 45 度侧视，背景改为浅灰"},
        {"image": ref},
        "A-edits-multipart",
    )

    # B. generations 带各种参考图字段
    for field in ("image", "images", "reference_image", "image_url", "input_image"):
        val: object = data_uri if field in ("image", "reference_image", "input_image") else [data_uri]
        post_json(
            "/images/generations",
            {
                "model": model,
                "prompt": "保持该产品的外形、材质、配色与比例完全一致，只把视角换成 45 度侧视",
                "size": "1024x1024",
                field: val,
            },
            f"B-gen-{field}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
