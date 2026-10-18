"""运行中断信号。

这三个异常是「主动中止」而不是「出错」，由护栏在事件回调里抛出，
沿着 push → 流式回调 → run_agent → server 的链路一路穿透，用来打断 LangGraph 流。

关键约束：任何事件推送的包装层（main_agent.push / tools.emit）都**不能**把
RunAborted 吞掉，否则取消和超时就会石沉大海。
"""
from __future__ import annotations


class RunAborted(Exception):
    """所有主动中止的基类。"""

    status = "aborted"
    label = "已中止"


class RunCancelled(RunAborted):
    status = "cancelled"
    label = "已停止"


class RunTimeout(RunAborted):
    status = "timeout"
    label = "超时中止"


class RunBudget(RunAborted):
    status = "budget"
    label = "超出预算中止"


class RunGoal(RunAborted):
    """六阶段产物齐全、宽限期已过 —— 这不是失败，是按目标交付。"""

    status = "done"
    label = "目标达成"
