"""决策范式路由（Orchestrator）：把「任务 DNA」映射成一种执行档位。

四种范式不是四个并列名词，而是四块不同的「思考肌肉」。选型判据是任务 DNA 的三个基因：

- **时效基因**：用户能等多久？短查询选 ReAct，完整产品设计选 Plan-and-Execute。
- **容错基因**：错了代价多高？查个元件型号错了无所谓；量产/防水/成本/认证错了就是事故，
  必须 Plan-and-Execute 保证全局视野，必要时再叠 Reflexion。
- **进化基因**：这类任务会不会反复遇到？会，就值得为它沉淀反思规则 —— Reflexion 的
  累积效应在高频重复任务上是指数级的。

本项目里每种范式对应一档**具体的执行参数**（跑哪些子智能体、多少预算、开不开分支/反思），
而不是只换个提示词。

降级链固定为 explore → review → standard → lean：任何一档超时、超预算或工具链断裂，
就往下降一档重跑，而不是原地重试 —— 原地重试只会重复失败路径。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Paradigm:
    """一档执行策略。"""

    key: str
    label: str
    philosophy: str          # 设计哲学（一句话，前端 tooltip 会用）
    boundary: str            # 适用边界（什么时候不该用它）
    stages: tuple[str, ...]  # 要委派的子智能体名，空 = 主智能体自己干
    tool_budget: int
    max_seconds: float
    reflect: bool = False    # 是否启用 Reflexion（评审后返工 + 沉淀规则）
    branches: int = 1        # ToT 分支数，1 = 不分支
    fallback: str = ""       # 降级目标 key
    recursion_limit: int = 120  # LangGraph 递归深度：档位越重留得越宽

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "philosophy": self.philosophy,
            "boundary": self.boundary,
            "stages": list(self.stages),
            "tool_budget": self.tool_budget,
            "max_seconds": self.max_seconds,
            "reflect": self.reflect,
            "branches": self.branches,
            "fallback": self.fallback,
        }


# ---------------------------------------------------------------- 四档定义
LEAN = Paradigm(
    key="lean",
    label="ReAct · 边做边想",
    philosophy="Thought→Action→Observation 咬合成环，走一步看一步，不预先规划全局。",
    boundary="只适用于工具链 ≤5 步、反馈即时、错误成本低的查询。跨阶段强依赖的任务用它必然陷入局部最优。",
    stages=(),
    tool_budget=int(os.getenv("AGENT_LEAN_BUDGET", "30")),
    max_seconds=float(os.getenv("AGENT_LEAN_SECONDS", "240")),
    fallback="",
    recursion_limit=40,
)

STANDARD = Paradigm(
    key="standard",
    label="Plan-and-Execute · 先画施工图",
    philosophy="先出 PRD 锁定全局约束，再按图施工；执行阶段不改计划，靠 Plan 阶段的全局视野避免漏项。",
    boundary="延迟明显高于 ReAct（Plan 生成本身要时间），不适合实时对话；换来的是低误报率与阶段可验证。",
    stages=(
        "requirements-analyst",
        "industrial-designer",
        "mechanical-engineer",
        "hardware-engineer",
        "firmware-engineer",
        "integration-reviewer",
    ),
    tool_budget=int(os.getenv("AGENT_STANDARD_BUDGET", "150")),
    max_seconds=float(os.getenv("AGENT_STANDARD_SECONDS", "900")),
    fallback="lean",
)

REVIEW = Paradigm(
    key="review",
    label="Reflexion · 事后反思",
    philosophy="在 Plan-and-Execute 之上加一层：评审发现的 P0/P1 必须返工一轮，并把失败模式沉淀成可复用规则。",
    boundary="无法预防首次错误，反思质量依赖模型能力；规则库不聚类会膨胀到拖慢检索 —— 必须配压缩策略。",
    stages=STANDARD.stages,
    tool_budget=int(os.getenv("AGENT_REVIEW_BUDGET", "180")),
    max_seconds=float(os.getenv("AGENT_REVIEW_SECONDS", "1200")),
    reflect=True,
    fallback="standard",
    recursion_limit=160,
)

EXPLORE = Paradigm(
    key="explore",
    label="Tree of Thoughts · 分支择优",
    philosophy="需求模糊或多目标冲突时，先并行出 3 个定位方案、按可行性/成本/风险独立打分，择优后再往下派。",
    boundary="开销是 ReAct 的数倍，只对高价值决策启用；完整树没有性价比，只做单层分支 + 打分筛选。",
    stages=STANDARD.stages,
    tool_budget=int(os.getenv("AGENT_EXPLORE_BUDGET", "220")),
    max_seconds=float(os.getenv("AGENT_EXPLORE_SECONDS", "1500")),
    reflect=True,
    branches=3,
    fallback="review",
    recursion_limit=200,
)

REGISTRY: dict[str, Paradigm] = {p.key: p for p in (LEAN, STANDARD, REVIEW, EXPLORE)}
ORDER = ("lean", "standard", "review", "explore")

# ---------------------------------------------------------------- 路由信号
# 查询类：短、即时、错了无所谓 —— 时效基因强、容错基因弱
QUERY_HINTS = ("多少", "是否", "能不能", "是什么", "区别", "查一下", "参数", "型号",
               "引脚", "电流", "功耗", "支持吗", "怎么用", "什么意思")
# 风险类：错了代价高 —— 容错基因强
RISK_HINTS = ("成本", "量产", "认证", "防水", "承重", "安全", "医疗", "车规", "寿命",
              "可靠性", "电池", "高温", "防爆", "儿童", "食品级")
# 权衡类：多目标冲突、需求模糊 —— 需要分支择优
TRADEOFF_HINTS = ("又", "权衡", "对比", "或者", "还是", "哪个更", "既要", "兼顾",
                  "预算有限", "方案", "几选", "取舍")


def _count(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if w in text)


def route(requirement: str, history_fail: int = 0, forced: str = "") -> Paradigm:
    """Orchestrator：按任务 DNA 选范式。

    history_fail 是同类历史需求的失败次数（进化基因）—— 踩过坑的需求值得多花一轮反思。
    forced 用于前端/环境变量强制指定档位（调试或用户明确要求时用）。
    """
    key = (forced or "").strip().lower()
    if key in REGISTRY:
        return REGISTRY[key]

    text = (requirement or "").strip()
    if not text:
        return STANDARD

    n_risk = _count(text, RISK_HINTS)
    n_trade = _count(text, TRADEOFF_HINTS)
    n_query = _count(text, QUERY_HINTS)
    # 显式约束（数字 + 单位）越多，越需要全局规划而不是走一步看一步
    n_numeric = len(re.findall(r"\d+\s*(mm|cm|mAh|V|mA|元|块|g|kg|小时|天|℃)", text))

    # 时效基因压倒一切：短查询、无风险词、无多约束 → 一步到位，别兴师动众派六个子智能体
    if len(text) <= 40 and n_query and not n_risk and n_trade == 0 and n_numeric <= 1:
        return LEAN

    # 多目标权衡 / 需求模糊 → 先分支打分再收敛
    if n_trade >= 2 or (n_trade >= 1 and n_risk >= 2):
        return EXPLORE

    # 进化基因：同类需求踩过坑 → 加反思层；硬约束多的高风险需求同样加
    if history_fail >= 1 or n_risk >= 3:
        return REVIEW

    return STANDARD


def demote(p: Paradigm) -> Paradigm | None:
    """降级：超时/超预算/工具链断裂时往下降一档。已是最低档返回 None。"""
    if not p.fallback:
        return None
    return REGISTRY.get(p.fallback)


def stage_plan(p: Paradigm) -> str:
    """把档位渲染成可塞进 system prompt 的执行清单。"""
    if not p.stages:
        return (
            "本档位（ReAct）**不委派子智能体**：你自己直接查知识库、直接作答，"
            "工具链控制在 5 步以内，拿到答案就回交用户。"
        )
    lines = [f"本档位必须按顺序委派以下 {len(p.stages)} 个子智能体（括号里是它们的落盘目录）："]
    dirs = {
        "requirements-analyst": "00_prd",
        "industrial-designer": "10_appearance",
        "mechanical-engineer": "20_structure",
        "hardware-engineer": "30_hardware",
        "firmware-engineer": "40_firmware",
        "integration-reviewer": "90_review",
    }
    for i, s in enumerate(p.stages, 1):
        lines.append(f"  Step {i}. {s}  →  {dirs.get(s, '')}/")
    if p.branches > 1:
        lines.append(
            f"【分支择优】Step 1 完成后，先并行出 {p.branches} 个产品定位方案，"
            "按（可行性×0.4 + 成本可控性×0.3 + 风险×0.3）各自打分，"
            "把评分表写进 00_prd/prd.md，只让得分最高的方案进入 Step 2。不要往下建完整树。"
        )
    if p.reflect:
        lines.append(
            "【反思轮】Step 6 评审后：若存在 P0/P1 问题，必须把问题指派回对应子智能体返工一轮，"
            "并在 90_review/review.md 里记录「错误模式 → 修正动作」两条字段，供后续会话复用。"
        )
    return "\n".join(lines)


def describe(p: Paradigm) -> str:
    """给前端/日志用的一行说明。"""
    return f"{p.label}｜预算 {p.tool_budget} 次工具 / {int(p.max_seconds)} 秒｜降级 {p.fallback or '—'}"
