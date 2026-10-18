"""探测 SiliconFlow 上哪些模型/参数能返回深度思考内容（reasoning_content）。

用法: python scripts/probe_reasoning.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
KEY = os.getenv("SILICONFLOW_API_KEY", "")

CANDIDATES = [
    # (model, extra_body)
    ("deepseek-ai/DeepSeek-V3.2", {}),
    ("deepseek-ai/DeepSeek-V3.2", {"enable_thinking": True}),
    ("deepseek-ai/DeepSeek-V3.2", {"thinking_budget": 512}),
    ("Pro/deepseek-ai/DeepSeek-V3.2", {"thinking_budget": 512}),
    ("deepseek-ai/DeepSeek-R1", {}),
    ("Qwen/Qwen3-8B", {"enable_thinking": True}),
    ("Qwen/Qwen3-32B", {"enable_thinking": True}),
    ("zai-org/GLM-4.6", {}),
]


def post(path: str, payload: dict, timeout: int = 90):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def probe_models() -> list[str]:
    req = urllib.request.Request(BASE + "/models", headers={"Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return sorted(m.get("id", "") for m in data.get("data", []))


def main() -> int:
    if not KEY:
        print("缺少 SILICONFLOW_API_KEY")
        return 1

    print("=== 可用模型（含 R1 / thinking / reason 关键字）===")
    try:
        ids = probe_models()
        print(f"总计 {len(ids)} 个")
        for i in ids:
            low = i.lower()
            if any(k in low for k in ("r1", "reason", "think", "v3.2", "glm-4.6", "qwen3")):
                print("  ", i)
    except Exception as e:  # noqa: BLE001
        print("  模型列表获取失败:", e)

    print("\n=== 探测 reasoning_content 返回 ===")
    prompt = "一个 3 位数，各位数字之和是 14，且能被 11 整除。求这个数，简述推理。"
    for model, extra in CANDIDATES:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "stream": False,
            **extra,
        }
        code, res = post("/chat/completions", body)
        if code != 200:
            print(f"[{code}] {model} extra={extra} -> {str(res)[:160]}")
            continue
        try:
            msg = res["choices"][0]["message"]
        except Exception:  # noqa: BLE001
            print(f"[?] {model} extra={extra} -> 结构异常 {str(res)[:160]}")
            continue
        rc = msg.get("reasoning_content") or ""
        has_rc_key = "reasoning_content" in msg
        usage = res.get("usage", {})
        print(
            f"[OK] {model:<32} extra={str(extra):<28} "
            f"reasoning_content={'✓ ' + str(len(rc)) + '字符' if rc else ('键存在但空' if has_rc_key else '✗ 无')} "
            f"| content={len(msg.get('content') or '')}字 | usage={usage}"
        )
        if rc:
            print("      样例:", rc[:180].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
