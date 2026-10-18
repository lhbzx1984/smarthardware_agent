"""模型服务商预设。

只存「公开元信息」（网关地址、推荐模型、思考参数），任何密钥都由前端运行时传入，
不写进代码、不落盘。新增服务商只需往 PROVIDERS 里加一项。
"""
from __future__ import annotations

from typing import Any

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "siliconflow",
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "key_hint": "sk- 开头，控制台 API 密钥",
        "models": [
            "Qwen/Qwen3.5-122B-A10B",
            "Qwen/Qwen3-8B",
            "deepseek-ai/DeepSeek-V3.2",
            "deepseek-ai/DeepSeek-R1",
            "zai-org/GLM-4.6",
            "Qwen/Qwen2.5-72B-Instruct",
        ],
        "note": "国内直连；Qwen3 系列可开深度思考",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        "key_hint": "sk- 开头",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "deepseek-reasoner 原生输出思维链",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM（OpenAI 兼容）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "key_hint": "API Key，形如 xxxx.yyyy",
        "models": ["glm-4.6", "glm-4.5", "glm-4.5-air"],
        "note": "思考参数：thinking.type = enabled",
    },
    {
        "id": "dashscope",
        "name": "阿里百炼 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_hint": "sk- 开头",
        "models": ["qwen-plus", "qwen-max", "qwen3-max", "qwen-flash"],
        "note": "Qwen3 系列支持 enable_thinking",
    },
    {
        "id": "moonshot",
        "name": "月之暗面 Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "key_hint": "sk- 开头",
        "models": ["kimi-k2-0905-preview", "moonshot-v1-128k"],
        "note": "K2 支持工具调用",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "key_hint": "sk-proj- 开头",
        "models": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
        "note": "需外网",
    },
    {
        "id": "agnes",
        "name": "Agnes AI",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "key_hint": "sk- 开头，apihub.agnes-ai.com 控制台生成",
        "models": ["agnes-2.5-flash", "agnes-2.5-pro"],
        "note": "文本模型原生输出 reasoning_content；同一 key 可用于图像生成",
    },
    {
        "id": "custom",
        "name": "自定义（OpenAI 兼容 / Ollama / vLLM）",
        "base_url": "http://127.0.0.1:11434/v1",
        "key_hint": "本地服务可随便填，例如 ollama",
        "models": [],
        "note": "任意 OpenAI 兼容网关；Ollama 默认 http://127.0.0.1:11434/v1",
    },
]

BY_ID = {p["id"]: p for p in PROVIDERS}


def infer_thinking_body(model: str) -> dict[str, Any]:
    """按模型名推断深度思考参数。各家字段名不统一，这里做一层适配。"""
    low = (model or "").lower()
    if "qwen3" in low or low.startswith("qwen-plus") or low.startswith("qwen-max"):
        return {"enable_thinking": True}
    if "glm-4" in low:
        return {"thinking": {"type": "enabled"}}
    if "deepseek-reasoner" in low or "deepseek-r1" in low:
        return {}  # 原生就吐思维链，无需参数
    return {}


def disabled_thinking_body(model: str) -> dict[str, Any]:
    """显式关闭深度思考的参数体。

    为什么必须显式发：不发参数时网关按自己的默认值走，实测照样吐思维链。
    一次完整 run 的 thinking 输出 89~106 万字符、占总输出的 75%~85%，
    直接把免费额度的 token 窗口打满 —— 表现就是每一轮都弹「网关限流（429）」。
    字段名各家不统一，这里按模型名适配；认不出的退回最通用的
    `{"thinking": {"type": "disabled"}}`（未知字段网关一般忽略，不会报错）。
    """
    low = (model or "").lower()
    if "qwen3" in low or low.startswith("qwen-plus") or low.startswith("qwen-max"):
        return {"enable_thinking": False}
    if "glm-4" in low:
        return {"thinking": {"type": "disabled"}}
    if "deepseek-reasoner" in low or "deepseek-r1" in low:
        return {}  # 推理模型原生必吐思维链，关不掉；只能换模型省配额
    return {"thinking": {"type": "disabled"}}


def public() -> list[dict[str, Any]]:
    """返回给前端的预设（永不包含密钥）。"""
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "base_url": p["base_url"],
            "models": p["models"],
            "key_hint": p["key_hint"],
            "note": p["note"],
        }
        for p in PROVIDERS
    ]
