"""诊断 /chat/completions：区分「网关不通」与「请求排队不返回」，并对比 thinking 开关的影响。"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent import config  # noqa: E402

BASE = config.DEFAULT_BASE_URL.rstrip("/")
KEY = config.AGNES_API_KEY
MODELS = ["agnes-2.5-flash", "agnes-2.5-pro-alpha", "deepseek-ai/DeepSeek-V3.2"]


def line(t: str) -> None:
    print(f"[chat] {t}", flush=True)


with httpx.Client(timeout=30.0) as c:
    r = c.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"})
ids = [m["id"] for m in r.json().get("data", [])]
line(f"models total={len(ids)}; target in list: {[m for m in MODELS if m in ids]}")
line(f"sample: {ids[:8]}")

for model in MODELS:
    if model not in ids:
        line(f"{model}: SKIP (not offered)")
        continue
    for thinking in (False, True):
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "reply with the single word: ok"}],
            "max_tokens": 16,
            "stream": False,
        }
        if thinking:
            body.update(config.THINKING_BODY)
        t0 = time.time()
        try:
            with httpx.Client(timeout=90.0) as c:
                r = c.post(
                    f"{BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                    json=body,
                )
            dt = time.time() - t0
            line(f"{model} thinking={int(thinking)}: {r.status_code} in {dt:.1f}s -> {r.text[:120]!r}")
        except Exception as e:  # noqa: BLE001
            line(f"{model} thinking={int(thinking)}: FAIL {type(e).__name__} after {time.time() - t0:.1f}s")

# 流式：首字节延迟（langchain 报错就在这一层）
model = config.MODEL
if model in ids:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "reply with the single word: ok"}],
        "max_tokens": 16,
        "stream": True,
    }
    body.update(config.THINKING_BODY)
    t0 = time.time()
    try:
        with httpx.Client(timeout=90.0) as c:
            with c.stream("POST", f"{BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                          json=body) as r:
                line(f"stream status={r.status_code} headers_in={time.time() - t0:.1f}s")
                n = 0
                for chunk in r.iter_lines():
                    n += 1
                    if n == 1:
                        line(f"stream first chunk at {time.time() - t0:.1f}s: {chunk[:100]!r}")
                line(f"stream done chunks={n} total={time.time() - t0:.1f}s")
    except Exception as e:  # noqa: BLE001
        line(f"stream FAIL {type(e).__name__} after {time.time() - t0:.1f}s: {str(e)[:120]}")
