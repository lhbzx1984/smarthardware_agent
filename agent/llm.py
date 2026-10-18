"""带思维链的 ChatOpenAI。

langchain-openai 的 `_convert_delta_to_message_chunk` 只识别 `content` / `tool_calls` /
`function_call`，流式 delta 里的 `reasoning_content`（DeepSeek-R1、Qwen3 thinking 等
OpenAI 兼容网关的常见字段）会被直接丢弃，于是前端拿不到流式思考。

这里覆写 chunk 转换，把 reasoning_content 补写回 additional_kwargs，
上层用 `additional_kwargs["reasoning_content"]` 即可拿到逐块思考增量。
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

import config


def _pick_reasoning(delta: dict[str, Any]) -> str:
    for k in ("reasoning_content", "reasoning", "thinking_content"):
        v = delta.get(k)
        if v:
            return str(v)
    return ""


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI + 流式思维链透传 + 配额守卫（请求前主动限速）。

    配额守卫为什么放在这里：回调（callback）是在请求**已经发出**时才触发的，
    没法让请求慢下来。要在网关看来「我们变慢了」，只能卡在真正发请求之前。
    免费额度是滑动窗口 token 总量，撞一次 429 要退避 25~75 秒，
    而提前等几秒让旧记录滑出窗口，成本低一个数量级。
    """

    def _throttle(self, messages: Any) -> None:
        if not config.QUOTA_GUARD:
            return
        try:
            import quotaguard

            guard = quotaguard.get()
            # 撞过 429 就把思考关掉：它是免费额度的头号消耗项（占输出 75%~85%），
            # 关掉后同样的钱能跑完更多阶段。
            body = guard.degrade_thinking(getattr(self, "model_name", "") or "")
            if body:
                self.extra_body = body
            guard.wait_before(quotaguard.estimate_messages_tokens(messages))
        except Exception:  # noqa: BLE001  限速失败不能挡住主流程
            pass

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        self._throttle(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        self._throttle(messages)
        return super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _convert_chunk_to_generation_chunk(  # type: ignore[override]
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        gen = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if gen is None:
            return None
        try:
            choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
            delta = (choices[0].get("delta") or {}) if choices else {}
            rc = _pick_reasoning(delta)
            if rc and isinstance(gen.message, AIMessageChunk):
                gen.message.additional_kwargs["reasoning_content"] = rc
        except Exception:  # noqa: BLE001  思考字段缺失绝不影响主流程
            pass
        return gen
