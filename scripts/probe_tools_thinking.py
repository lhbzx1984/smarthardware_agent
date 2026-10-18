"""关键验证：候选模型能否「同时」输出思维链 + 调用工具（deepagents 两条都要）。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
KEY = os.getenv("SILICONFLOW_API_KEY", "")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_temperature",
            "description": "查询指定城市的当前气温",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名"}},
                "required": ["city"],
            },
        },
    }
]

CASES = [
    ("Qwen/Qwen3-32B", {"enable_thinking": True}),
    ("Qwen/Qwen3-14B", {"enable_thinking": True}),
    ("Qwen/Qwen3.5-122B-A10B", {"enable_thinking": True}),
    ("Qwen/Qwen3.5-122B-A10B", {}),
    ("deepseek-ai/DeepSeek-R1", {}),
    ("Pro/deepseek-ai/DeepSeek-R1", {}),
]

PROMPT = "北京现在多少度？必须调用工具查询。"


def call(model: str, extra: dict) -> tuple[bool, bool, str]:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "tools": TOOLS,
            "max_tokens": 500,
            "stream": False,
            **extra,
        }
    ).encode()
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    tag = f"{model:<28} {str(extra):<26}"
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {tag} {e.read().decode('utf-8','replace')[:180]}")
        return False, False, ""
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] {tag} {type(e).__name__}: {e}")
        return False, False, ""

    msg = res["choices"][0]["message"]
    rc = msg.get("reasoning_content") or ""
    tcs = msg.get("tool_calls") or []
    u = res.get("usage", {}).get("completion_tokens_details", {})
    print(
        f"[{'OK' if rc and tcs else '··'}] {tag} "
        f"思考={len(rc):>5}字 工具调用={len(tcs)}个 "
        f"reasoning_tokens={u.get('reasoning_tokens', 0)}"
    )
    if tcs:
        print(f"        -> {tcs[0]['function']['name']}({tcs[0]['function']['arguments']})")
    if rc:
        print(f"        思考样例: {rc[:110].replace(chr(10),' ')}")
    return bool(rc), bool(tcs)


if __name__ == "__main__":
    print()
    both = []
    for m, e in CASES:
        rc, tc = call(m, e)
        if rc and tc:
            both.append((m, e))
        print()
    print("=== 同时支持思考 + 工具调用 ===")
    for m, e in both:
        print("  ✓", m, e)
    if not both:
        print("  （无）")
