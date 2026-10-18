"""Agnes 图像并发定性探测：验证 401 是否由并行请求触发。

用法：python scripts/probe_agnes_parallel.py [并发数]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://apihub.agnes-ai.com/v1"
MODEL = os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.5-flash")


def keys() -> dict[str, str]:
    out = {}
    env = os.getenv("AGNES_API_KEY")
    if env:
        out["环境变量 sk-RcbEDg…"] = env.strip()
    for line in open(".env", encoding="utf-8"):
        line = line.strip()
        if line.startswith("AGNES_API_KEY="):
            v = line.split("=", 1)[1].strip()
            if v:
                out.setdefault(".env sk-ZBiXZ…", v)
    return out


def one(key: str, idx: int) -> tuple[int, str]:
    payload = json.dumps(
        {"model": MODEL, "prompt": f"一个白色圆柱形桌面小夜灯 #{idx}", "size": "1024x1024"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/images/generations",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read().decode("utf-8"))
            url = ((d.get("data") or [{}])[0]).get("url", "")
            return r.status, f"{time.time()-t0:.1f}s url={url[:48]}"
    except urllib.error.HTTPError as e:
        return e.code, f"{time.time()-t0:.1f}s {e.read().decode('utf-8','replace')[:120]}"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"[:120]


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    for label, key in keys().items():
        print(f"\n===== {label}  并发 {n} =====")
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(one, key, i) for i in range(n)]
            for i, f in enumerate(futs):
                code, detail = f.result()
                flag = "OK " if code == 200 else "!! "
                print(f"  {flag}#{i} -> {code}  {detail}")


if __name__ == "__main__":
    main()
