"""网关配额守卫：把「撞 429 再退避」改成「快撞了就先慢下来」。

背景（2026-10-13 实测）：
    网关对免费额度按**滑动窗口内的 token 总量**限流。串行小请求连打 5 次全 200；
    但只要单轮上下文一大（实测 16k 字符 ≈ 8k token）就立刻 429，返回体写的是
    "You've reached the API rate limit for free users"。等一会儿再打又恢复 200。

    所以退避不是补救而是止损：撞一次要等 25~75 秒，而在请求前主动等几秒、
    让窗口里的旧记录滑出去，成本更低、也不会打断已经跑出来的产物。

    更关键的是**输出侧**：一次完整 run 的 thinking 输出实测 89~106 万字符，
    占总输出的 75%~85%（见 docs/quota-and-429-2026-10-13.md）。光靠事后退避救不回来，
    必须把 thinking 真正关掉（get_model 里关闭分支此前根本没发 disabled 参数）。

用法：
    guard = QuotaGuard()
    model.callbacks = [guard.handler()]        # 挂到 LLM 上自动记账
    guard.wait_before(estimated_tokens)        # 请求前主动限速（可选）
    guard.penalize()                           # 撞到 429 后把窗口记满，后续自动减速
"""

from __future__ import annotations

import threading
import time
from collections import deque

import config


class QuotaGuard:
    """滑动窗口 token 预算。线程安全：主智能体与子智能体可能并发打网关。"""

    def __init__(
        self,
        window: float | None = None,
        budget: int | None = None,
    ) -> None:
        self.window = float(window if window is not None else config.QUOTA_WINDOW_SECONDS)
        self.budget = int(budget if budget is not None else config.QUOTA_WINDOW_TOKENS)
        self._events: deque[tuple[float, int]] = deque()
        # 必须是**可重入**锁：wait_before() 持锁时会调 used()，而 used() 也要这把锁。
        # 用普通 Lock 会当场死锁（实测：第一个 LLM 调用就永久卡住，界面毫无反应）。
        self._lock = threading.RLock()
        self._penalty_until = 0.0
        self._hits = 0

    # ------------------------------------------------------------------ 记账
    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] > self.window:
            self._events.popleft()

    def used(self) -> int:
        with self._lock:
            self._prune(time.time())
            return sum(t for _, t in self._events)

    def record(self, tokens: int) -> None:
        now = time.time()
        with self._lock:
            self._events.append((now, max(0, int(tokens))))

    def penalize(self, seconds: float | None = None) -> None:
        """撞到 429：把窗口记满，并让后续请求先停一停。

        不记满的话，下一次请求还会按同样节奏打出去，再撞一次 —— 这就是
        「每次都弹限流提示」的来源。
        """
        self._hits += 1
        wait = seconds if seconds is not None else config.QUOTA_PENALTY_SECONDS
        with self._lock:
            self._penalty_until = max(self._penalty_until, time.time() + float(wait))
            # 把预算吃满，逼后续走 wait_before 的等待分支
            self._events.append((time.time(), max(0, self.budget - self.used())))

    @property
    def hits(self) -> int:
        return self._hits

    # -------------------------------------------------------------- 思考降级
    # thinking 输出占一次 run 总输出的 75%~85%（实测 89~106 万字符），是免费额度
    # 的头号消耗项。撞到 429 后自动把它关掉：宁可少一点推理过程，也要把整轮跑完。
    @property
    def thinking_off(self) -> bool:
        return self._hits > 0

    def degrade_thinking(self, model_name: str) -> dict | None:
        """返回关闭思考的参数体；还没撞过 429 就返回 None（维持原样）。"""
        if not self.thinking_off:
            return None
        try:
            import providers

            return providers.disabled_thinking_body(model_name)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ 限速
    def headroom(self) -> int:
        return max(0, self.budget - self.used())

    def wait_before(self, estimated: int) -> float:
        """请求前按估算体积主动等待，返回实际等待秒数（没等就是 0）。"""
        waited = 0.0
        # 惩罚期：撞过 429 就老实等，别再试探
        now = time.time()
        if now < self._penalty_until:
            d = self._penalty_until - now
            time.sleep(d)
            waited += d

        # 单次请求本身就超过整个窗口预算：等也没用（窗口滑出去它还是超），直接放行
        if estimated >= self.budget:
            return waited

        deadline = time.time() + config.QUOTA_MAX_WAIT
        while time.time() < deadline:
            with self._lock:
                self._prune(time.time())
                if self.used() + estimated <= self.budget:
                    return waited
                # 等到最早那条记录滑出窗口，腾出的空间够不够本次用
                gap = self.used() + estimated - self.budget
                if not self._events:
                    return waited
                slop = min(self.window - (time.time() - self._events[0][0]), config.QUOTA_MAX_WAIT)
            slop = max(0.5, min(slop, config.QUOTA_MAX_WAIT))
            if slop > config.QUOTA_MAX_WAIT:
                return waited
            time.sleep(slop)
            waited += slop
        return waited

    # --------------------------------------------------------- langchain 回调
    def handler(self):
        """生成 langchain 回调，自动按真实 usage 记账。拿不到 usage 就按字符估算。"""
        from langchain_core.callbacks import BaseCallbackHandler

        guard = self

        class _Handler(BaseCallbackHandler):
            def on_llm_start(self, serialized, messages, **kwargs):  # noqa: ANN001
                try:
                    guard.record(estimate_messages_tokens(messages))
                except Exception:  # noqa: BLE001  记账失败绝不影响主流程
                    pass

            def on_llm_end(self, response, **kwargs):  # noqa: ANN001
                try:
                    used = 0
                    for gen in getattr(response, "generations", None) or []:
                        for g in gen:
                            meta = getattr(g, "message", None)
                            um = getattr(meta, "usage_metadata", None) or {}
                            used += int(um.get("total_tokens") or 0)
                    out = getattr(response, "llm_output", None) or {}
                    if not used:
                        used = int((out.get("token_usage") or {}).get("total_tokens") or 0)
                    if used:
                        guard.record(used)
                except Exception:  # noqa: BLE001
                    pass

        return _Handler()


def estimate_tokens(text: str) -> int:
    """粗估 token 数。中文按 1 字 ≈ 1 token、其余按 4 字符 ≈ 1 token 混合计。

    宁可高估：高估只是多等一会儿，低估会撞 429（撞一次 25~75 秒，贵得多）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk + other / 4) + 1


def estimate_messages_tokens(messages) -> int:  # noqa: ANN001
    total = 0
    for m in messages or []:
        content = getattr(m, "content", None)
        if isinstance(content, list):
            content = "".join(
                str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
            )
        total += estimate_tokens(str(content or "")) + 4
    return total


# 全局单例：主智能体与所有子智能体共享同一个窗口
_GUARD = QuotaGuard()
_GUARD_LOCK = threading.RLock()


def get() -> QuotaGuard:
    return _GUARD


def penalize(seconds: float | None = None) -> None:
    _GUARD.penalize(seconds)


def wait_before(estimated: int) -> float:
    return _GUARD.wait_before(estimated)


def reset(window: float | None = None, budget: int | None = None) -> None:
    """测试用：重建单例。"""
    global _GUARD
    with _GUARD_LOCK:
        _GUARD = QuotaGuard(window, budget)
    return _GUARD
