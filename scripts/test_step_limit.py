"""LangGraph 步数上限（recursion_limit）自检。

背景（真事）：一次标准档运行跑到硬件阶段直接崩
`GraphRecursionError: Recursion limit of 120 reached`，整轮被记成 error，
前面五个阶段已经落盘的产物全部作废。

两个根因，这个文件把两条都钉死：
1. **子图与父图共享 step 计数**：子智能体内部每一次「模型 → 工具 → 模型」都算在
   主图账上。六阶段串行链绝不是「6 次派单」那么便宜，120 步必然不够。
2. **撞上限被当成故障**：GraphRecursionError 一路冒泡到 server 的兜底 except，
   run 状态被打成 error。实际上产物往往已经攒到 4~5 个阶段，应该按「中止」处理
   —— 保留产物 + 允许自动续跑。

零框架依赖：python scripts/test_step_limit.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402

# 别把自测数据灌进真实历史库
import store  # noqa: E402

_TMP = Path(tempfile.mkdtemp())
store.DB_PATH = _TMP / "step-limit-selftest.db"

import guards  # noqa: E402
import paradigms  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


# ---------------------------------------------------------------- 1. 档位预算
print("[1] 各档步数上限")

limits = {k: paradigms.REGISTRY[k].recursion_limit for k in paradigms.ORDER}
print("      ", limits)

check("标准档 ≥ 400 步（六阶段串行链 + 子图共享计数）", limits["standard"] >= 400, str(limits["standard"]))
check("ReAct 档也应留出 ≥100 步（子智能体内部往返同样算账）", limits["lean"] >= 100, str(limits["lean"]))
check(
    "档位越重步数越宽：lean < standard < review < explore",
    limits["lean"] < limits["standard"] < limits["review"] < limits["explore"],
    str(limits),
)
check(
    "步数上限可通过环境变量覆盖",
    paradigms._env_int("AGENT_RECURSION_NOPE_NOT_SET", 600) == 600
    and paradigms._env_int("AGENT_RECURSION_STANDARD", 600) >= 1,
)

os.environ["AGENT_RECURSION_STANDARD"] = "777"
try:
    import importlib

    reloaded = importlib.reload(paradigms)
    check("AGENT_RECURSION_STANDARD=777 生效", reloaded.STANDARD.recursion_limit == 777, str(reloaded.STANDARD.recursion_limit))
finally:
    os.environ.pop("AGENT_RECURSION_STANDARD", None)
    paradigms = importlib.reload(paradigms)  # noqa: F811


# ---------------------------------------------------------------- 2. 中止语义
print("[2] 撞上限 = 中止，不是故障")


class _StepLimitBoom(Exception):
    pass


check("RunStepLimit 是 RunAborted 子类（能被 server 的中止分支接住）", issubclass(guards.RunStepLimit, guards.RunAborted))
check("status 记为 steps 而不是 error", guards.RunStepLimit.status == "steps")
check("label 说人话", "步数" in guards.RunStepLimit.label, guards.RunStepLimit.label)
check("超时中止仍然是 timeout", guards.RunTimeout.status == "timeout")


# ---------------------------------------------------------------- 3. 真跑一遍流
print("[3] run_agent：流耗尽 → 转成中止信号")

import main_agent  # noqa: E402
from langgraph.errors import GraphRecursionError  # noqa: E402


class FakeAgent:
    """只实现 stream()，按脚本吐事件，最后一次抛 GraphRecursionError。"""

    def __init__(self, events: list[tuple], boom: bool) -> None:
        self._events = events
        self._boom = boom

    def stream(self, *args, **kwargs):
        def gen():
            for ev in self._events:
                yield ev
            if self._boom:
                raise GraphRecursionError("Recursion limit of 120 reached")

        return gen()


def _run_with_agent(fake: FakeAgent, paradigm) -> tuple[list[dict], str | None]:
    """跑 run_agent，返回 (事件, 异常名)。"""
    events: list[dict] = []

    def sink(ev: dict) -> None:
        events.append(ev)

    run_dir = _TMP / "ws"
    run_dir.mkdir(parents=True, exist_ok=True)
    orig = main_agent.build_agent
    main_agent.build_agent = lambda *a, **k: fake  # noqa: E731
    try:
        main_agent.run_agent("做个小夜灯", run_dir, sink, {}, paradigm, None)
    except Exception as e:  # noqa: BLE001
        return events, type(e).__name__
    finally:
        main_agent.build_agent = orig
    return events, None


from langchain_core.messages import AIMessageChunk  # noqa: E402

# 3a. 正常收尾
ok_events, err = _run_with_agent(
    FakeAgent([((), "messages", (AIMessageChunk(content="交付完成"), {}))], boom=False),
    paradigms.LEAN,
)
check("正常流不抛异常", err is None, str(err))
check("正常流把文本累加进最终回复", any(e.get("type") == "token" and "交付完成" in e.get("text", "") for e in ok_events))

# 3b. 撞上限
boom_events, err = _run_with_agent(FakeAgent([], boom=True), paradigms.LEAN)
check("流耗尽时抛的是 RunStepLimit 而不是 GraphRecursionError", err == "RunStepLimit", str(err))
check(
    "撞上限时推了人话提示（而不是甩 traceback）",
    any(e.get("type") == "warn" and "步数上限" in str(e.get("message", "")) for e in boom_events),
    str([e for e in boom_events if e.get("type") == "warn"]),
)

# 3c. 软阈值预警：喂够 80% 的 updates 事件
warn_events, err = _run_with_agent(
    FakeAgent([((), "updates", {"agent": {"messages": []}}) for _ in range(int(paradigms.LEAN.recursion_limit * 0.8) + 1)], boom=False),
    paradigms.LEAN,
)
check(
    "接近上限时提前预警一次",
    any(e.get("type") == "warn" and "接近本轮步数上限" in str(e.get("message", "")) for e in warn_events),
    str([e.get("message") for e in warn_events if e.get("type") == "warn"]),
)
check(
    "预警只推一次，不刷屏",
    sum(1 for e in warn_events if e.get("type") == "warn" and "接近本轮步数上限" in str(e.get("message", ""))) == 1,
)


# ---------------------------------------------------------------- 4. 续跑
print("[4] 撞上限后允许自动续跑")

import server  # noqa: E402

check("续跑名单包含 steps", "steps" in server._RESUMABLE_STATUS, str(server._RESUMABLE_STATUS))
check("续跑名单仍包含 timeout", "timeout" in server._RESUMABLE_STATUS)
check("steps 有中文说法", "步数" in server._RESUME_LABEL.get("steps", ""), str(server._RESUME_LABEL))


print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
