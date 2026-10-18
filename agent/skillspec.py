"""把 `skills/` 下的技能文档注入智能体提示词。

为什么要做成文件而不是写死在提示词里：
- 出图链 / CAD 链的坑是**实测攒出来的**（同图覆盖、i2i 一致性锚、build123d 0.13 的
  四个必错写法），改动频繁，写死在 subagents.py 里每次改都要动代码。
- 同一份文档既给智能体读，也能被人直接查阅（`skills/*/SKILL.md`），
  口径只有一份，不会出现「提示词这么说、文档那么写」。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"

# 阶段 → 该阶段必须照做的技能
STAGE_SKILLS: dict[str, str] = {
    "10_appearance": "agnes-product-image",
    "20_structure": "text-to-cad-structure",
}


def _body(name: str) -> str:
    p = SKILLS_DIR / name / "SKILL.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    # 去掉 YAML front-matter：模型不需要看 name/description 这些元数据
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return text.strip()


def block(stage: str) -> str:
    """某阶段要照做的技能全文（找得到就返回带标题的片段）。"""
    name = STAGE_SKILLS.get(stage)
    if not name:
        return ""
    body = _body(name)
    if not body:
        return ""
    return f"【必须照做的技能 · {name}】\n{body}\n"


def catalog() -> list[dict[str, str]]:
    """给主智能体看的技能索引：哪个阶段绑了哪个技能。"""
    out = []
    for stage, name in STAGE_SKILLS.items():
        out.append({"stage": stage, "skill": name, "has_body": bool(_body(name))})
    return out


def index_block() -> str:
    items = catalog()
    if not items:
        return ""
    lines = ["【阶段技能绑定 · 委派前先确认子智能体知道要照做哪一份】"]
    for it in items:
        flag = "已加载" if it["has_body"] else "文档缺失"
        lines.append(f"- {it['stage']} → skills/{it['skill']}/SKILL.md（{flag}）")
    return "\n".join(lines)
