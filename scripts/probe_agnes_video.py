"""探测 Agnes 视频生成协议：端点 / payload 形态 / 同步异步。"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

import config  # noqa: E402

base, _model, prov = config.resolve_image_provider()
key = config.fallback_key(base)


def post(path: str, payload: dict, ctype: str = "application/json") -> tuple[int, str]:
    if ctype == "application/json":
        data = json.dumps(payload).encode("utf-8")
    else:
        from urllib.parse import urlencode

        data = urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": ctype},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read().decode("utf-8", "replace")[:800]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:800]
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"[:400]


print("== A. /videos （OpenAI Sora 风格），重试 3 次 ==")
for i in range(3):
    code, body = post("/videos", {"model": "agnes-video-2.5-flash", "prompt": "a cube rotating slowly on white ground"})
    print(f"  [{i}] {code}: {body[:260]}")
    if code not in (429,):
        break
    time.sleep(6)

print("\n== B. /video/generations 换 payload 形态 ==")
for label, payload, ctype in [
    ("model+prompt(form)", {"model": "agnes-video-2.5-flash", "prompt": "a cube rotating"}, "application/x-www-form-urlencoded"),
    ("无 model 字段", {"prompt": "a cube rotating slowly", "seconds": "4"}, "application/json"),
    ("size+duration", {"model": "agnes-video-2.5-flash", "prompt": "a cube rotating", "size": "1280x720", "duration": 4}, "application/json"),
]:
    code, body = post("/video/generations", payload, ctype)
    print(f"  [{label}] {code}: {body[:260]}")

print("\n== C. GET /videos 列表（看是否有任务型接口）==")
req = urllib.request.Request(base.rstrip("/") + "/videos", headers={"Authorization": f"Bearer {key}"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print("  GET", r.status, r.read().decode()[:400])
except Exception as e:  # noqa: BLE001
    print("  GET ERR", type(e).__name__, str(e)[:200])
