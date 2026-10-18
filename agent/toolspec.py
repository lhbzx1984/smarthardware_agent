"""工具 ID 绑定表 + 参数 Schema 预加载。

实战教训：让模型直接输出工具名，它会编造不存在的名字（工具幻觉），也会编造参数名
（参数幻觉）。两种做法把错误率压下去：

1. **工具 ID 绑定**：注册时生成唯一 ID（tool_001…），提示词里只认 ID，由这张表映射到真实工具。
2. **参数 Schema 预加载**：把每个工具的参数名/类型/必填项直接写进提示词，模型照着填，
   不再靠猜。

本模块只做「生成提示词片段」，不改动工具的注册方式 —— 工具本身仍是 langchain 的 @tool，
ID 只是提示词层面的别名，模型输出真名或 ID 都能被接受（真名优先，ID 兜底）。
"""
from __future__ import annotations

from typing import Any


def _type_of(prop: dict[str, Any]) -> str:
    """pydantic v2 的 JSON Schema 里，Optional[...] 会落成 anyOf。"""
    if not isinstance(prop, dict):
        return "any"
    t = prop.get("type")
    if t:
        return str(t)
    for sub in prop.get("anyOf") or []:
        if isinstance(sub, dict) and sub.get("type"):
            return str(sub["type"])
    if prop.get("enum"):
        return "enum"
    return "any"


def catalog() -> list[dict[str, Any]]:
    """从 ALL_TOOLS 抽出工具目录：ID / 名字 / 一句话说明 / 参数签名。"""
    from tools import ALL_TOOLS

    out: list[dict[str, Any]] = []
    for i, t in enumerate(ALL_TOOLS, 1):
        name = str(getattr(t, "name", "") or "")
        desc = ""
        for line in str(getattr(t, "description", "") or "").splitlines():
            line = line.strip()
            if line:
                desc = line[:110]
                break
        schema: dict[str, Any] = {}
        try:
            schema = t.args_schema.model_json_schema()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001  没定义 args_schema 的工具只写名字
            schema = {}
        props = schema.get("properties") or {}
        params = []
        for k, v in props.items():
            params.append(
                {
                    "name": k,
                    "type": _type_of(v),
                    "required": k in (schema.get("required") or []),
                }
            )
        out.append(
            {
                "id": f"tool_{i:03d}",
                "name": name,
                "desc": desc,
                "params": params,
            }
        )
    return out


def prompt_block() -> str:
    """渲染成可直接塞进 system prompt 的片段。工具多时也能一眼看完。"""
    items = catalog()
    if not items:
        return ""
    lines = [
        "【工具目录 · 只能调用下表里的工具】",
        "调用时参数名必须与下表一致，不要自造参数；不许调用下表中没有的工具。",
        "",
    ]
    for it in items:
        sig = ", ".join(
            f"{p['name']}:{p['type']}{'' if p['required'] else '?'}" for p in it["params"]
        )
        lines.append(f"- {it['id']}  {it['name']}({sig})")
        if it["desc"]:
            lines.append(f"    {it['desc']}")
    lines.append("")
    lines.append("（? 表示可选参数；类型 string/number/integer/boolean/array/object）")
    return "\n".join(lines)


def agents_md_block() -> str:
    """写进工作区 AGENTS.md 的版本 —— 子智能体通过记忆读得到同一份目录。"""
    return "# 工具目录\n\n" + prompt_block() + "\n"
