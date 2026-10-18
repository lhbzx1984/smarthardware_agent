"""重复调用护栏 + 流式参数补齐 自检。

背景（真事）：一次运行被判「同一调用重复 5 次（read_file），判定为无效循环并中止」，
但产物其实都出来了。根因是 tool_start 事件里的 args 恒为空 —— 参数是流式逐块
拼出来的，首发那一下还没到。于是 (工具名, 参数) 这个去重签名退化成了「只有工具名」，
读 5 个不同文件也被算成同一调用重复 5 次。

这个脚本把两件事钉死：
1. 护栏：重复 ≠ 空转，必须「次数够多 + 期间毫无进展」才中止；产物齐了按交付定性。
2. 流式：tool_start 必须带着完整参数发出，且永远排在对应 tool_end 之前。

零框架依赖：python scripts/test_loop_guard.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import guards  # noqa: E402
import store  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def _fresh_run(title: str = "护栏自检"):
    import server

    return server.Run(title, {})


def test_read_only_not_killed() -> None:
    print("[1] 只读工具重复读不同文件 —— 不该中止")
    r = _fresh_run()
    aborted = None
    try:
        for i in range(12):
            # 每次读完都交出一个非空结果（= 有进展）
            r.push({"type": "tool_start", "cid": f"c{i}", "name": "read_file", "args": f'{{"path":"/00_prd/p{i}.md"}}'})
            r.push({"type": "tool_end", "cid": f"c{i}", "name": "read_file", "result": f"内容 {i}"})
    except guards.RunAborted as e:
        aborted = e
    check("读 12 个不同文件全程放行", aborted is None, str(aborted))
    check("只读调用只消耗 1/4 预算", abs(r.tool_weight - 12 * config.TOOL_WEIGHT_READ) < 1e-6, str(r.tool_weight))
    check("原始调用次数照常记账", r.tool_calls == 12, str(r.tool_calls))


def test_noarg_signature() -> None:
    print("[2] args 缺失时签名退化 —— 阈值放宽且不得立刻中止")
    r = _fresh_run()
    aborted = None
    try:
        for i in range(config.REPEAT_LIMIT + 3):
            r.push({"type": "tool_start", "cid": f"c{i}", "name": "read_file", "args": ""})
            r.push({"type": "tool_end", "cid": f"c{i}", "name": "read_file", "result": f"r{i}"})
    except guards.RunAborted as e:
        aborted = e
    check("args 为空时不按 5 次阈值误杀", aborted is None, str(aborted))
    check(
        "无参签名的阈值不低于 REPEAT_LIMIT",
        r._repeat_limit("read_file", "") >= max(config.REPEAT_LIMIT_NOARG, config.REPEAT_LIMIT),
        str(r._repeat_limit("read_file", "")),
    )
    check(
        "无参的写类工具取 NOARG 阈值",
        r._repeat_limit("save_artifact", "") == config.REPEAT_LIMIT_NOARG,
    )

    r2 = _fresh_run()
    check("有参的写类工具仍是 5 次阈值", r2._repeat_limit("save_artifact", '{"path":"a.md"}') == config.REPEAT_LIMIT)
    # 带路径的只读调用按「文件」聚合，阈值压到更低的一档（反复回读同一文件最可疑）
    check("同文件反复读走 SAMEFILE 阈值", r2._repeat_limit("ls", '{"path":"/"}') == config.REPEAT_LIMIT_SAMEFILE)
    check(
        "同文件阈值 < 普通只读阈值",
        config.REPEAT_LIMIT_SAMEFILE < config.REPEAT_LIMIT_READ,
        f"{config.REPEAT_LIMIT_SAMEFILE} vs {config.REPEAT_LIMIT_READ}",
    )


def test_real_loop_still_caught() -> None:
    print("[3] 真·空转循环 —— 必须能拦住")
    r = _fresh_run()
    # 把进展时刻推回很久以前：等于「这段时间什么都没产出来」
    r.last_progress = time.time() - config.STALL_SECONDS - 5
    hit = None
    try:
        for i in range(config.REPEAT_LIMIT + 1):
            r.push({"type": "tool_start", "cid": f"c{i}", "name": "generate_product_image", "args": '{"prompt":"同样的词"}'})
            r.push({"type": "tool_end", "cid": f"c{i}", "name": "generate_product_image", "result": ""})
    except guards.RunAborted as e:
        hit = e
    check("同参数 + 无进展 → 中止", hit is not None)
    check("产物一个没有 → 定性为超预算", isinstance(hit, guards.RunBudget), str(type(hit)))

    # 有进展时不中止：每次重复之间都在出东西
    r2 = _fresh_run()
    hit2 = None
    try:
        for i in range(config.REPEAT_LIMIT + 3):
            r2.push({"type": "tool_start", "cid": f"c{i}", "name": "save_artifact", "args": '{"path":"a.md"}'})
            r2.push({"type": "artifact", "path": f"00_prd/p{i}.md", "size": 10})
    except guards.RunAborted as e:
        hit2 = e
    check("重复但持续产出 → 不中止", hit2 is None, str(hit2))


def test_abort_qualifies_as_delivery() -> None:
    print("[4] 产物已够 → 中止也要定性为交付，不是失败")
    r = _fresh_run()
    for p in (
        "00_prd/prd.md",
        "10_appearance/appearance.md",
        "20_structure/structure.md",
        "30_hardware/bom.csv",
    ):
        r.push({"type": "artifact", "path": p, "size": 1})
    r.last_progress = time.time() - config.STALL_SECONDS - 5
    hit = None
    try:
        for i in range(config.REPEAT_LIMIT_READ + 2):
            r.push({"type": "tool_start", "cid": f"c{i}", "name": "read_file", "args": '{"path":"00_prd/prd.md"}'})
            r.push({"type": "tool_end", "cid": f"c{i}", "name": "read_file", "result": ""})
    except guards.RunAborted as e:
        hit = e
    check("四阶段产物在手 → RunGoal", isinstance(hit, guards.RunGoal), str(type(hit)))
    check("RunGoal status=done（前端显示交付而非失败）", hit is not None and hit.status == "done")


def test_budget_weighting() -> None:
    print("[5] 预算按代价加权")
    r = _fresh_run()
    hit = None
    try:
        for i in range(config.TOOL_BUDGET + 10):
            r.push({"type": "tool_start", "cid": f"c{i}", "name": "ls", "args": f"/{i}"})
    except guards.RunAborted as e:
        hit = e
    check("90 次只读调用不撞预算", hit is None, str(hit))

    r2 = _fresh_run()
    hit2 = None
    try:
        for i in range(config.TOOL_BUDGET + 2):
            r2.push({"type": "tool_start", "cid": f"c{i}", "name": "build_structure", "args": f"script{i}"})
    except guards.RunAborted as e:
        hit2 = e
    check("昂贵调用照旧撞预算", hit2 is not None)


def test_stream_args() -> None:
    print("[6] 流式参数补齐（tool_start 不能带着空 args 出门）")
    import main_agent
    from langchain_core.messages import AIMessageChunk, ToolMessage

    class FakeAgent:
        def __init__(self, items: list[tuple[Any, str, Any]]) -> None:
            self.items = items

        def stream(self, *_a, **_kw):
            return iter(self.items)

    def patch(items):
        def _mk(**_kw):
            return FakeAgent(items)

        main_agent.create_deep_agent = _mk  # type: ignore[assignment]

    # 场景 A：参数分两块到，结果最后到
    items: list[tuple[Any, str, Any]] = [
        ((), "messages", (AIMessageChunk(content="", tool_call_chunks=[{"id": "c1", "name": "read_file", "args": '{"path":', "index": 0}]), {})),
        ((), "messages", (AIMessageChunk(content="", tool_call_chunks=[{"id": "c1", "name": "read_file", "args": '"00_prd/prd.md"}', "index": 0}]), {})),
        ((), "messages", (ToolMessage(content="PRD 正文", tool_call_id="c1", name="read_file"), {})),
    ]
    events: list[dict] = []
    patch(items)

    tmp = Path(tempfile.mkdtemp())
    main_agent.run_agent("测试", tmp, events.append, {}, paradigm=None)

    starts = [e for e in events if e.get("type") == "tool_start"]
    ends = [e for e in events if e.get("type") == "tool_end"]
    check("只发一张开始卡片", len(starts) == 1, str(len(starts)))
    check("参数已补齐", bool(starts) and "prd.md" in str(starts[0].get("args")), str(starts[:1]))
    check("开始排在结束之前", bool(starts and ends) and events.index(starts[0]) < events.index(ends[0]))

    # 场景 A2：网关先发占位 {}，真实参数随后到 —— 不许拿空壳当参数发出去
    items_a2 = [
        ((), "messages", (AIMessageChunk(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {}}]), {})),
        ((), "messages", (AIMessageChunk(content="", tool_call_chunks=[{"id": "c1", "name": "read_file", "args": '{"file_path":"00_prd/prd.md"}', "index": 0}]), {})),
        ((), "messages", (ToolMessage(content="PRD", tool_call_id="c1", name="read_file"), {})),
    ]
    ev_a2: list[dict] = []
    patch(items_a2)
    main_agent.run_agent("测试A2", tmp / "a2", ev_a2.append, {}, paradigm=None)
    s_a2 = [e for e in ev_a2 if e.get("type") == "tool_start"]
    check("占位 {} 不被当成完整参数", bool(s_a2) and str(s_a2[0].get("args")) != "{}", str(s_a2[:1]))
    check("真参数覆盖占位", bool(s_a2) and "prd.md" in str(s_a2[0].get("args")), str(s_a2[:1]))

    # 场景 A3：网关在 messages 流里只给空壳参数，真参数藏在节点 update 的 AIMessage 里
    from langchain_core.messages import AIMessage

    items_a3 = [
        ((), "messages", (AIMessageChunk(content="", tool_calls=[{"id": "c1", "name": "search_cirkit_docs", "args": {}}]), {})),
        ((), "updates", {"tools": {"messages": [
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "search_cirkit_docs", "args": {"query": "风扇 PWM"}}])
        ]}}),
        ((), "messages", (ToolMessage(content="文档", tool_call_id="c1", name="search_cirkit_docs"), {})),
    ]
    ev_a3: list[dict] = []
    patch(items_a3)
    main_agent.run_agent("测试A3", tmp / "a3", ev_a3.append, {}, paradigm=None)
    s_a3 = [e for e in ev_a3 if e.get("type") == "tool_start"]
    check("从节点 update 回填参数", bool(s_a3) and "风扇" in str(s_a3[0].get("args")), str(s_a3[:1]))

    # 场景 B：结果比参数先到（网关不按序），也要先补 start 再补 end
    items_b = [
        ((), "messages", (AIMessageChunk(content="", tool_call_chunks=[{"id": "c1", "name": "read_file", "args": "", "index": 0}]), {})),
        ((), "messages", (ToolMessage(content="内容", tool_call_id="c1", name="read_file"), {})),
    ]
    ev_b: list[dict] = []
    patch(items_b)
    main_agent.run_agent("测试B", tmp / "b", ev_b.append, {}, paradigm=None)
    s_b = [e for e in ev_b if e.get("type") == "tool_start"]
    e_b = [e for e in ev_b if e.get("type") == "tool_end"]
    check("结果先到也补出开始卡片", len(s_b) == 1 and len(e_b) == 1, f"start={len(s_b)} end={len(e_b)}")
    check("配对顺序正确", bool(s_b and e_b) and ev_b.index(s_b[0]) < ev_b.index(e_b[0]))


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    store.DB_PATH = tmp / "loop-guard-selftest.db"
    config.WORKSPACE = tmp / "workspace"
    config.WORKSPACE.mkdir(parents=True, exist_ok=True)
    store.init()
    test_read_only_not_killed()
    test_noarg_signature()
    test_real_loop_still_caught()
    test_abort_qualifies_as_delivery()
    test_budget_weighting()
    test_stream_args()
    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
