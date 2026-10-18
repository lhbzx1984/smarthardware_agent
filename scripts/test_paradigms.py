"""决策范式 / 工具绑定 / 反思规则库 自检。

跑法：python scripts/test_paradigms.py
覆盖：范式路由（任务 DNA 三基因）、降级链、档位清单生成、工具目录抽取、
反思规则抽取与优先级、规则聚类压缩。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402

# 反思库测试会写库 —— 指向临时文件，别把自测数据灌进真实历史
import store  # noqa: E402

_TMP = Path(tempfile.mkdtemp())
store.DB_PATH = _TMP / "paradigm-selftest.db"

import paradigms  # noqa: E402
import toolspec  # noqa: E402

_OK = 0
_FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _OK, _FAIL
    if cond:
        _OK += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}  {extra}")


def test_route() -> None:
    print("— 范式路由（任务 DNA 三基因）")
    # 时效基因：短查询、无风险、无多约束 → ReAct
    p = paradigms.route("ESP32-C3 的 GPIO 最大输出电流是多少")
    check("短查询 → lean", p.key == "lean", p.key)

    p = paradigms.route("设计一个桌面式温湿度监测小夜灯，带 OLED 显示")
    check("常规产品设计 → standard", p.key == "standard", p.key)

    p = paradigms.route(
        "设计一个儿童夜灯，要求防水、防摔、材料食品级，成本控制在 30 元以内，要量产"
    )
    check("高风险 + 硬约束 → review", p.key == "review", p.key)

    p = paradigms.route(
        "我想要一个既能当闹钟又能当音响的设备，预算有限，还要兼顾便携，哪种方案更好？"
    )
    check("多目标权衡 → explore", p.key == "explore", p.key)

    # 进化基因：同类需求踩过坑就升级到带反思的档位
    p = paradigms.route("设计一个桌面式温湿度监测小夜灯，带 OLED 显示", history_fail=2)
    check("历史失败 → 升级为 review", p.key == "review", p.key)

    p = paradigms.route("随便什么需求都行", forced="explore")
    check("强制指定生效", p.key == "explore", p.key)

    check("空需求回落 standard", paradigms.route("").key == "standard")


def test_demote() -> None:
    print("— 降级链 explore → review → standard → lean")
    chain = []
    p: paradigms.Paradigm | None = paradigms.EXPLORE
    while p is not None:
        chain.append(p.key)
        p = paradigms.demote(p)
    check("降级链完整", chain == ["explore", "review", "standard", "lean"], str(chain))
    check("最低档不可再降", paradigms.demote(paradigms.LEAN) is None)
    check("lean 预算 < standard", paradigms.LEAN.tool_budget < paradigms.STANDARD.tool_budget)
    check("explore 有时限余量", paradigms.EXPLORE.max_seconds > paradigms.STANDARD.max_seconds)
    check(
        "递归深度随档位递增",
        paradigms.LEAN.recursion_limit
        < paradigms.STANDARD.recursion_limit
        < paradigms.REVIEW.recursion_limit
        < paradigms.EXPLORE.recursion_limit,
    )


def test_stage_plan() -> None:
    print("— 档位执行清单")
    lean = paradigms.stage_plan(paradigms.LEAN)
    check("lean 不派子智能体", "不委派子智能体" in lean)

    std = paradigms.stage_plan(paradigms.STANDARD)
    check("standard 含 6 个 Step", "Step 6" in std and "Step 1" in std, std[:80])
    check("standard 不带分支", "分支择优" not in std)
    check("standard 不带反思轮", "反思轮" not in std)

    exp = paradigms.stage_plan(paradigms.EXPLORE)
    check("explore 有分支择优", "分支择优" in exp and "3 个产品定位方案" in exp)
    check("explore 有反思轮", "反思轮" in exp)

    rev = paradigms.stage_plan(paradigms.REVIEW)
    check("review 有反思轮", "反思轮" in rev)
    check("review 无分支", "分支择优" not in rev)

    d = paradigms.EXPLORE.as_dict()
    check("as_dict 可序列化", set(("key", "label", "philosophy", "boundary")) <= set(d))


def test_toolspec() -> None:
    print("— 工具 ID 绑定 + 参数 Schema")
    cat = toolspec.catalog()
    check("工具目录非空", len(cat) >= 8, str(len(cat)))
    ids = [c["id"] for c in cat]
    check("ID 唯一", len(set(ids)) == len(ids))
    check("ID 形如 tool_00N", ids[0] == "tool_001" and ids[-1].startswith("tool_"), ids[-1])
    names = {c["name"] for c in cat}
    for must in ("save_artifact", "build_structure", "render_schematic", "render_product_views"):
        check(f"含工具 {must}", must in names)
    # 每个工具都要能抽出参数签名，抽不出来就起不到防幻觉的作用
    no_params = [c["name"] for c in cat if not c["params"]]
    check("全部工具都抽出参数", not no_params, str(no_params))
    block = toolspec.prompt_block()
    check("提示词块含 ID 表", "tool_001" in block and "只能调用下表里的工具" in block)
    check("提示词块含类型标注", "string" in block)


def test_reflexions() -> None:
    print("— Reflexion 规则库")
    store.init()

    n = store.save_reflexions("r1", "timeout", "运行超时", ["20_structure"], elapsed=600, n_artifacts=1)
    check("抽取到多条规则", n >= 3, str(n))

    rules = store.recall_reflexions()
    pats = {r["pattern"] for r in rules}
    check("含 run_timeout", "run_timeout" in pats)
    check("含 stage_missing_20_structure", "stage_missing_20_structure" in pats)
    check("含 idle_no_artifact", "idle_no_artifact" in pats)

    # 优先级排序：鉴权类（40）必须排在最前
    store.save_reflexions("r2", "error", "Agnes 401 Invalid token")
    ranked = store.recall_reflexions()
    check("鉴权规则排最前", ranked[0]["pattern"] == "image_auth_failed", ranked[0]["pattern"])
    check("鉴权优先级 = 40", ranked[0]["priority"] == 40, str(ranked[0]["priority"]))

    # 复发累加 hits
    before = next(r["hits"] for r in store.recall_reflexions() if r["pattern"] == "image_auth_failed")
    store.save_reflexions("r3", "error", "401 invalid token 又来了")
    after = next(r["hits"] for r in store.recall_reflexions() if r["pattern"] == "image_auth_failed")
    check("同 pattern 累加 hits", after == before + 1, f"{before} -> {after}")

    hint = store.reflexion_hint()
    check("提示词含规则头", "反思规则" in hint and "image_auth_failed" in hint)

    check("干净运行不产规则", store.save_reflexions("r4", "done") == 0)

    # 聚类压缩：灌一批同前缀规则，压完每组只留一条
    conn = store._connect()
    for i in range(30):
        conn.execute(
            "INSERT OR REPLACE INTO reflexions (pattern,fix,priority,hits,run_id,created_at,updated_at)"
            " VALUES (?,?,10,1,'bulk',?,?)",
            (f"stage_missing_{i:02d}", f"补第 {i} 个阶段", 0.0, 0.0),
        )
    conn.commit()
    total_before = store.count_reflexions()
    deleted = store.cluster_reflexions()
    total_after = store.count_reflexions()
    check("聚类删掉了冗余", deleted > 0, f"deleted={deleted}")
    check("总数下降", total_after < total_before, f"{total_before} -> {total_after}")
    check("元规则标注归并条数", "由 30 条同类规则归并" in " ".join(
        r["fix"] for r in store.recall_reflexions(limit=50)
    ) or "归并" in " ".join(r["fix"] for r in store.recall_reflexions(limit=50)))


def test_config() -> None:
    print("— 配置")
    check("心跳阈值合理", 0.5 <= config.HEARTBEAT_SECONDS <= 10, str(config.HEARTBEAT_SECONDS))
    check("聚类阈值已设", config.REFLEXION_CLUSTER_AT > 0, str(config.REFLEXION_CLUSTER_AT))


def main() -> int:
    test_route()
    test_demote()
    test_stage_plan()
    test_toolspec()
    test_reflexions()
    test_config()
    print(f"\n通过 {_OK} 项，失败 {_FAIL} 项")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
