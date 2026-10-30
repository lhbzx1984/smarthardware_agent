"""子智能体工具裁剪与提示词权威清单的回归测试。

背景（真实事故）：六个子智能体原先都绑定全部 16 个工具。网关降级到备用模型后，
工业设计师在思考里断定「我手上只有 ls/read_file/...，没有 generate_product_image」，
于是压根没调用它 —— 外观阶段零张渲染图交付，结构阶段又因为没有形状约束只能瞎编尺寸。

修法是两条：按角色裁剪工具集 + 把「你手上真正有哪些工具」写进各自的提示词。
本测试锁住这两条，防止以后有人图省事改回 tools=ALL_TOOLS。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}  {detail}")


import subagents  # noqa: E402
import main_agent  # noqa: E402

AGENTS = {a["name"]: a for a in subagents.ALL_SUBAGENTS}


def names_of(agent: str) -> set[str]:
    return {getattr(t, "name", "") for t in AGENTS[agent]["tools"]}


print("=" * 60)
print("【1】六个子智能体都在，工具集非空")
for n in subagents.TOOLS_FOR:
    check(f"{n} 已注册", n in AGENTS)
    check(f"{n} 工具非空", bool(names_of(n)))

print("\n【2】每个角色的契约工具必须在手（缺一个就干不了本阶段）")
NEED = {
    "requirements-analyst": {"stage_gate", "save_artifact", "confirm_with_user"},
    "industrial-designer": {"generate_product_image", "render_product_views",
                            "save_artifact", "confirm_with_user", "stage_gate"},
    "mechanical-engineer": {"text_to_cad", "build_structure", "render_product_views",
                            "generate_product_image", "save_artifact", "stage_gate"},
    "hardware-engineer": {"check_circuit", "render_schematic", "emit_kicad_project",
                          "emit_pcb", "emit_easyeda", "export_gerbers", "build_bom",
                          "save_artifact", "stage_gate"},
    "firmware-engineer": {"check_arduino", "save_artifact", "stage_gate"},
    "integration-reviewer": {"save_artifact", "confirm_with_user"},
}
for agent, need in NEED.items():
    miss = need - names_of(agent)
    check(f"{agent} 契约工具齐全", not miss, f"缺 {sorted(miss)}")

print("\n【3】工具集确实被裁剪过（不是 16 个全给）")
for agent in NEED:
    n = len(names_of(agent))
    check(f"{agent} 工具数 ≤ 11（实为 {n}）", n <= 11, f"{n} 个")
check("没有谁再拿满 16 个", all(len(names_of(a)) < 16 for a in NEED))

print("\n【4】跨角色不串工具（硬件不该拿出图、设计师不该拿建模）")
check("hardware-engineer 没有 generate_product_image",
      "generate_product_image" not in names_of("hardware-engineer"))
check("industrial-designer 没有 build_structure",
      "build_structure" not in names_of("industrial-designer"))
check("firmware-engineer 没有 emit_pcb", "emit_pcb" not in names_of("firmware-engineer"))
check("integration-reviewer 没有出图/建模工具",
      not ({"generate_product_image", "build_structure", "text_to_cad"} & names_of("integration-reviewer")))

print("\n【5】提示词里的「权威清单」包含该角色每一个工具名")
for agent in NEED:
    sp = AGENTS[agent]["system_prompt"]
    check(f"{agent} 提示词含权威清单标题", "权威清单" in sp)
    miss = [n for n in sorted(names_of(agent)) if n and n not in sp]
    check(f"{agent} 清单里每个工具名都写到了提示词", not miss, f"漏 {miss}")

print("\n【6】出图能力的两条防幻觉说明")
ind = AGENTS["industrial-designer"]["system_prompt"]
check("工业设计师提示词点名 Agnes 出图与模型无关", "Agnes" in ind and "无关" in ind)
check("通用准则里有「以为自己没工具就照清单调用」的兜底",
      "以清单为准" in subagents.COMMON_RULES or "以清单为准" in ind)

print("\n【7】主智能体：串行派单 + 未出图要补派")
mp = main_agent.MAIN_PROMPT if hasattr(main_agent, "MAIN_PROMPT") else ""
if not mp:  # 提示词是函数拼出来的，取不到就退回读源码
    mp = (ROOT / "agent" / "main_agent.py").read_text(encoding="utf-8")
check("主提示词禁止并行派单", "一次只派一个" in mp and "并行" in mp)
check("主提示词写明出图/建模尤其不能并行", "出图/建模阶段尤其不能并行" in mp)
check("主提示词要求子智能体报『工具不可用』时补派一次", "补派" in mp and "本次未生成" in mp)
check("主提示词要求派单描述用中文", "中文" in mp and "description" in mp)

print("\n" + "=" * 60)
print(f"通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
