"""精简探测：只测 3 个最可能的 thinking 组合，快速给出结论。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
KEY = os.getenv("SILICONFLOW_API_KEY", "")

CASES = [
    ("deepseek-ai/DeepSeek-V3.2", {}),
    ("deepseek-ai/DeepSeek-V3.2", {"thinking_budget": 256}),
    ("Qwen/Qwen3-8B", {"enable_thinking": True}),
]

PROMPT = "9.11 和 9.9 哪个大？直接给结论。"


def call(model: str, extra: dict) -> None:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 300,
            "stream": False,
            **extra,
        }
    ).encode()
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    tag = f"{model} + {extra or '{}'}"
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {tag}\n    {e.read().decode('utf-8', 'replace')[:220]}\n")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] {tag} -> {type(e).__name__}: {e}\n")
        return

    msg = res["choices"][0]["message"]
    rc = msg.get("reasoning_content") or ""
    print(f"[OK] {tag}")
    print(f"     reasoning_content 键存在: {'reasoning_content' in msg} | 长度 {len(rc)}")
    print(f"     usage: {res.get('usage')}")
    if rc:
        print(f"     样例: {rc[:150].replace(chr(10), ' ')}")
    print()


if __name__ == "__main__":
    print(f"key: {'已配置' if KEY else '缺失'}\n")
    for m, e in CASES:
        call(m, e)
