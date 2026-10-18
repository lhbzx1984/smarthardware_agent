"""网关体检：一家一家问「有没有 key / 通不通 / 能不能真的调一次」。

用途：出现「网关不可达」类提示时先跑它，30 秒内分清三种完全不同的情况 ——

- **unreachable**：连不上（超时 / TLS 握手失败 / 连接重置）→ 等它恢复或换网关
- **quota**：通，但 402 余额不足 / 401 key 无效 / 404 模型不存在 → 等待没用，得充值或换 key
- **ok**：真的能用

只读探测，不写任何文件，不发长请求（max_tokens=8）。
用法：python scripts/diag_gateway.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

import config  # noqa: E402

PROBE_MSG = [{"role": "user", "content": "只回复一个字：好"}]


def http_status(e: Exception) -> str:
    """从 httpx / urllib 的异常里抠出状态码，没有就返回异常类名。"""
    code = getattr(e, "response", None)
    if code is not None and getattr(code, "status_code", None):
        return f"HTTP{code.status_code}"
    if "HTTPError" in type(e).__name__ and hasattr(e, "code"):
        return f"HTTP{e.code}"
    return type(e).__name__


def check(name: str, base: str, key: str, model: str) -> tuple[str, str, float]:
    if not key:
        return "no-key", "没配 API key", 0.0
    if not base:
        return "no-key", "没配 base_url", 0.0
    import httpx

    t0 = time.time()
    # 1) /models：判断网关在不在线
    try:
        with httpx.Client(verify=config.ssl_verify(), timeout=15.0) as cli:
            r = cli.get(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return "quota", f"/models HTTP{r.status_code}", time.time() - t0
    except Exception as e:  # noqa: BLE001
        return "unreachable", f"/models {type(e).__name__}: {str(e)[:48]}", time.time() - t0

    # 2) 真发一次极小 chat：判断能不能花钱调用（/models 通不代表有额度）
    try:
        with httpx.Client(verify=config.ssl_verify(), timeout=45.0) as cli:
            r = cli.post(
                base.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": PROBE_MSG, "max_tokens": 8},
            )
        dt = time.time() - t0
        if r.status_code == 200:
            return "ok", f"可用（{dt:.1f}s）", dt
        return "quota", f"chat HTTP{r.status_code} {r.text[:60]}", dt
    except Exception as e:  # noqa: BLE001
        return "unreachable", f"chat {http_status(e)}: {str(e)[:48]}", time.time() - t0


def main() -> int:
    provs = [
        ("agnes", config.AGNES_BASE_URL, config.AGNES_API_KEY, config.MODEL),
        ("siliconflow", config.SILICONFLOW_BASE_URL, config.SILICONFLOW_API_KEY,
         "Qwen/Qwen2.5-7B-Instruct" if config.SILICONFLOW_API_KEY else ""),
        ("dashscope", config.DASHSCOPE_BASE_URL, config.DASHSCOPE_API_KEY, "qwen-plus"),
        ("openai", config.OPENAI_BASE_URL, config.OPENAI_API_KEY, "gpt-4o-mini"),
    ]
    print(f"当前默认 provider：{config.DEFAULT_PROVIDER}  模型：{config.MODEL}")
    print(f"AGENT_PROVIDER={'已设' if os.getenv('AGENT_PROVIDER') else '未设（按 key 推断）'}\n")

    rows = []
    for name, base, key, model in provs:
        state, note, dt = check(name, base, key, model)
        rows.append((name, state, note))
        icon = {"ok": "可用", "quota": "调不动", "unreachable": "连不上", "no-key": "没配置"}[state]
        print(f"  {name:12s} {icon:6s} {note}")

    usable = [n for n, s, _ in rows if s == "ok"]
    print()
    if usable:
        print(f"结论：可用网关 → {', '.join(usable)}")
        if config.DEFAULT_PROVIDER not in usable:
            print(f"  当前默认 {config.DEFAULT_PROVIDER} 不可用，在 .env 加一行切过去：")
            print(f"  AGENT_PROVIDER={usable[0]}")
        return 0

    quota = [n for n, s, _ in rows if s == "quota"]
    if quota:
        print("结论：没有可直接用的网关。")
        print(f"  {', '.join(quota)} 连得上但调不动 —— 402 充值 / 401 换 key / 404 改模型名，等待无效。")
        print("  其余网关要么没配 key 要么连不上。给其中一个充值或配上有额度的 key，再跑一次本脚本确认。")
        return 1
    print("结论：所有网关都连不上 —— 网关侧故障或本机网络问题，等恢复后重试。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
