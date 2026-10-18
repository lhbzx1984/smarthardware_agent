# -*- coding: utf-8 -*-
"""实测各模型 × 思考开关的 TTFT 与总耗时，用于选默认模型。

用法：python scripts/bench_model.py
输出：模型 / thinking / 首字节 / 总耗时 / 输出字数
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
KEY = os.getenv("AGNES_API_KEY", "")
PROMPT = "用一句话说明什么是 DFMEA。只回答一句话。"


def call(model: str, thinking: bool) -> tuple[float, float, str]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": 300,
    }
    if thinking:
        body["enable_thinking"] = True
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + KEY,
        },
        method="POST",
    )
    t0 = time.time()
    ttft = None
    text = ""
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                if ttft is None:
                    ttft = time.time() - t0
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    d = json.loads(data)
                    delta = d["choices"][0].get("delta", {})
                    text += delta.get("content") or ""
                except Exception:
                    pass
    except Exception as e:
        return (time.time() - t0), -1.0, f"ERR {type(e).__name__}: {e}"
    return (ttft or 0.0), (time.time() - t0), text.strip()


def main() -> int:
    if not KEY:
        print("缺少 AGNES_API_KEY", file=sys.stderr)
        return 1
    models = ["agnes-2.5-flash", "agnes-2.5-pro-alpha", "agnes-2.5-pro"]
    print(f"{'模型':<22}{'thinking':<10}{'首字节':>10}{'总耗时':>10}  输出")
    for m in models:
        for th in (True, False):
            ttft, total, text = call(m, th)
            flag = "on" if th else "off"
            print(f"{m:<22}{flag:<10}{ttft:>9.1f}s{total:>9.1f}s  {text[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
