"""网关限流（429）不應判死整轮：退避續跑 + 等待不計入總時限。

用法：python scripts/test_rate_limit.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import config  # noqa: E402
import server  # noqa: E402

OK = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


print("\n[1] 限流识别")
cases = [
    ("OpenAIRateLimitError: Error code: 429 - rate limit for free users", True),
    ("Error code: 429", True),
    ("{'message': 'Too many requests'}", True),
    ("openai.RateLimitError: rate_limit_exceeded", True),
    ("ConnectionError: connection reset", False),
    ("SSLError: certificate verify failed", False),
    ("ValueError: bad request", False),
    ("insufficient balance", False),
]
for msg, want in cases:
    check(f"识别 {msg[:44]!r} -> {want}", server._is_rate_limit(msg) is want)

print("\n[2] 退避续跑：限流后能跑完，且等待不计入总时限")
run = server.Run("限流续跑测试", {})


class FakeAgent:
    """前 N 次抛限流，之后正常返回。"""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, *a, **kw) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("OpenAIRateLimitError: Error code: 429 - rate limit")
        return "最终交付汇总"


# 把耗时压到可测范围，同时验证退避公式确实被调用
saved_backoff, saved_wait, saved_attempts = (
    config.RATE_LIMIT_BACKOFF,
    config.RATE_LIMIT_MAX_WAIT,
    config.RATE_LIMIT_ATTEMPTS,
)
config.RATE_LIMIT_BACKOFF = 0.01
config.RATE_LIMIT_MAX_WAIT = 0.02
config.RATE_LIMIT_ATTEMPTS = 4

fake = FakeAgent(fail_times=2)
import main_agent  # noqa: E402

orig = main_agent.run_agent
main_agent.run_agent = fake  # type: ignore[assignment]
started0 = run.started
t0 = time.time()
try:
    out = server._run_with_retry(run)
finally:
    main_agent.run_agent = orig  # type: ignore[assignment]
    config.RATE_LIMIT_BACKOFF, config.RATE_LIMIT_MAX_WAIT = saved_backoff, saved_wait
    config.RATE_LIMIT_ATTEMPTS = saved_attempts

check("限流后成功续跑", out == "最终交付汇总", out)
check("共调用 3 次（2 次限流 + 1 次成功）", fake.calls == 3, str(fake.calls))
check("快速返回（退避被压缩）", time.time() - t0 < 5, f"{time.time() - t0:.1f}s")
check("等待时间已补回时限", run.started >= started0, f"{run.started - started0:.3f}s")

warns = [
    e for e in run.events
    if e.get("type") == "warn" and "限流" in str(e.get("message", ""))
]
check("推了限流提示（用户看得到在等什么）", len(warns) == 2, str(len(warns)))
check("提示里带了退避秒数", any("秒后自动续跑" in str(w.get("message")) for w in warns))

print("\n[3] 超出重试次数仍然放弃（不能无限等）")


class AlwaysFail:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *a, **kw) -> str:
        self.calls += 1
        raise RuntimeError("Error code: 429 - rate limit")


config.RATE_LIMIT_BACKOFF = 0.01
config.RATE_LIMIT_MAX_WAIT = 0.02
config.RATE_LIMIT_ATTEMPTS = 3
always = AlwaysFail()
main_agent.run_agent = always  # type: ignore[assignment]
raised = False
try:
    server._run_with_retry(run)
except RuntimeError:
    raised = True
finally:
    main_agent.run_agent = orig  # type: ignore[assignment]
    config.RATE_LIMIT_BACKOFF, config.RATE_LIMIT_MAX_WAIT = saved_backoff, saved_wait
    config.RATE_LIMIT_ATTEMPTS = saved_attempts

check("重试耗尽后抛出（交给上层给提示）", raised)
check("重试次数受配置约束", always.calls == 3, str(always.calls))

print("\n[4] 非限流错误不占用限流重试额度")


class NetFail:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *a, **kw) -> str:
        self.calls += 1
        raise ConnectionError("connection reset by peer")


config.RATE_LIMIT_ATTEMPTS = 4
net = NetFail()
main_agent.run_agent = net  # type: ignore[assignment]
raised2 = False
try:
    server._run_with_retry(run, max_attempts=2)
except ConnectionError:
    raised2 = True
finally:
    main_agent.run_agent = orig  # type: ignore[assignment]
    config.RATE_LIMIT_ATTEMPTS = saved_attempts

check("网络错误走原有网络重试通道", raised2)
check("网络错误只重试 max_attempts 次", net.calls == 2, str(net.calls))

print(f"\n结果：{OK} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
