"""harness 四层自检：阶段判据 / 情景记忆 / 文件沙箱 / 护栏中止穿透。

零框架依赖，直接 `python scripts/test_harness.py`。
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import guards  # noqa: E402
import stages  # noqa: E402
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


def test_stages() -> None:
    print("[1] 阶段判据（产物反推）")
    full = [
        "00_prd/prd.md",
        "10_appearance/appearance.md",
        "20_structure/structure.md",
        "20_structure/model.step",
        "30_hardware/netlist.json",
        "30_hardware/bom.csv",
        "40_firmware/firmware.ino",
        "90_review/review.md",
    ]
    p = stages.stage_progress(full)
    check("六阶段齐全", p["done"] == 6 and p["finished"], str(p))
    check("齐全时无当前阶段", p["current"] == "" and p["label"] == "全部完成", str(p))

    partial = ["00_prd/prd.md", "10_appearance/appearance.md"]
    p2 = stages.stage_progress(partial)
    check("只做了两阶段", p2["done"] == 2 and not p2["finished"], str(p2))
    check("当前阶段是结构设计", p2["current"] == "20_structure", str(p2))

    # 模型臆造子目录：判据仍要认（这是前端踩过的坑）
    nested = ["home/user/repos/prod-x/40_firmware/firmware.ino"]
    check("子目录里的固件也认", stages.stages_done(nested)["40_firmware"])

    # 结构阶段只认 md / step / stl
    check("结构目录里只有 png 不算完成", not stages.stages_done(["20_structure/exploded-01.png"])["20_structure"])
    check("结构有 stl 就算完成", stages.stages_done(["20_structure/model.stl"])["20_structure"])

    # 真实磁盘扫描
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "00_prd").mkdir()
        (root / "00_prd" / "prd.md").write_text("x", encoding="utf-8")
        (root / "AGENTS.md").write_text("memory", encoding="utf-8")
        scan = stages.scan_dir(root)
        check("扫盘排除 AGENTS.md", scan == ["00_prd/prd.md"], str(scan))
        check("扫盘进度", stages.progress_of_dir(root)["done"] == 1)


def test_memory() -> None:
    print("[2] 情景记忆（相似需求检索）")
    rid = f"selftest-{int(time.time())}"
    store.save_memory(
        rid,
        "设计一款桌面温湿度监测小夜灯，带 OLED 显示和光敏自动点灯",
        "选 DHT11 + SSD1306，USB 5V 直供，成本 68 元",
        "done",
        "6/6",
        12,
        420.0,
    )
    hit = store.recall("做一台温湿度小夜灯，要能显示温湿度")
    check("相似需求能召回", len(hit) >= 1 and hit[0]["score"] > 0.12, str(hit[:1]))
    check("召回带摘要", "DHT11" in (hit[0]["summary"] if hit else ""))

    miss = store.recall("设计一台工业级激光切割机的水冷系统")
    check("不相关需求不召回", len(miss) == 0, str([m["score"] for m in miss]))

    hint = store.memory_hint("温湿度小夜灯 OLED")
    check("提示词非空且含历史字样", bool(hint) and "历史经验" in hint, hint[:80])
    check("完全陌生需求无提示", store.memory_hint("量子计算机超导比特校准") == "")


def test_sandbox() -> None:
    print("[3] 文件沙箱（越界拒绝）")
    import tools

    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "run-abc"
        root.mkdir()
        (Path(d) / "run-evil").mkdir()   # 前缀相同的兄弟目录
        tools.bind_context(root, None, {})

        ok, err = tools._safe_target("00_prd/prd.md")
        check("正常相对路径放行", ok is not None and ok.parent.name == "00_prd", str(ok))

        ok, err = tools._safe_target("../../evil.md")
        check("../ 越界被拒", ok is None and "越界" in err, err)

        # 绝对路径一律 strip 成沙箱内相对路径 —— 这才是想要的行为：不是报错，是关进笼子
        ok, err = tools._safe_target("/etc/passwd")
        check("绝对路径被关进沙箱", ok is not None and str(ok).startswith(str(root.resolve())), str(ok))

        ok, err = tools._safe_target("")
        check("空路径被拒", ok is None, err)


def test_guards() -> None:
    print("[4] 护栏（中止信号穿透）")
    check("取消是中止不是错误", guards.RunCancelled("x").status == "cancelled")
    check("超时 status=timeout", guards.RunTimeout("x").status == "timeout")
    check("预算 status=budget", guards.RunBudget("x").status == "budget")
    check("目标达成 status=done", guards.RunGoal("x").status == "done")
    check(
        "全部是 RunAborted 子类",
        all(
            issubclass(c, guards.RunAborted)
            for c in (guards.RunCancelled, guards.RunTimeout, guards.RunBudget, guards.RunGoal)
        ),
    )

    # 中止不能被宽泛的 except 吞掉：模拟 main_agent 主循环的写法
    def sink(_ev: dict) -> None:
        raise guards.RunTimeout("模拟超时")

    swallowed = True
    try:
        try:
            sink({"type": "thinking"})
        except guards.RunAborted:
            raise
        except Exception:  # noqa: BLE001
            swallowed = False
    except guards.RunAborted:
        swallowed = False
    except Exception:  # noqa: BLE001
        pass
    check("RunAborted 不被宽泛 except 吞掉", not swallowed)


def test_termination() -> None:
    print("[5] 终止判据（真的能拦住流）")
    import server

    # Run 会建 workspace 目录并写库 —— 前面 main() 已把两者都指向临时目录
    r = server.Run("测试目标达成", {})
    for p in (
        "00_prd/prd.md",
        "10_appearance/appearance.md",
        "20_structure/structure.md",
        "30_hardware/bom.csv",
        "40_firmware/firmware.ino",
        "90_review/review.md",
    ):
        r.push({"type": "artifact", "path": p, "size": 1})
    check("产物齐全即触发 goal_reached", r.goal_at > 0, str(r.goal_at))

    # 宽限期没过：继续放行
    try:
        r.push({"type": "token", "agent": "main", "text": "收尾中"})
        check("宽限期内放行", True)
    except guards.RunAborted as e:
        check("宽限期内放行", False, f"提前中止：{e}")

    # 宽限期过了：目标达成 → 按交付中止（status=done，不是错误）
    r.goal_at = time.time() - config.GOAL_GRACE_SECONDS - 1
    try:
        r.push({"type": "token", "agent": "main", "text": "还想再改一版"})
        check("宽限期后按目标达成中止", False, "没有中止")
    except guards.RunGoal as e:
        check("宽限期后按目标达成中止", True)
        check("目标达成不算错误", e.status == "done" and e.label == "目标达成")

    # 取消：下一个事件立刻中断
    r2 = server.Run("测试取消", {})
    r2.cancel()
    try:
        r2.push({"type": "token", "agent": "main", "text": "x"})
        check("取消即刻中断", False, "没有中断")
    except guards.RunCancelled:
        check("取消即刻中断", True)

    # 工具预算：按代价加权，只有昂贵调用才真正消耗预算
    r3 = server.Run("测试预算", {})
    hit = False
    try:
        for i in range(config.TOOL_BUDGET + 5):
            r3.push({"type": "tool_start", "cid": f"c{i}", "name": "build_structure", "args": f"script{i}"})
    except guards.RunBudget:
        hit = True
    check("昂贵调用超预算中止", hit)

    r4 = server.Run("测试只读预算", {})
    hit_ro = False
    try:
        for i in range(config.TOOL_BUDGET + 5):
            r4.push({"type": "tool_start", "cid": f"c{i}", "name": "ls", "args": f"/{i}"})
    except guards.RunAborted:
        hit_ro = True
    check("只读调用不按 1 次计入预算", not hit_ro, f"weight={r4.tool_weight}")


def main() -> int:
    # 记忆 / 终止测试都要建 run（写库 + 建目录）—— 全部指向临时目录，别污染真实数据
    tmp = Path(tempfile.mkdtemp())
    store.DB_PATH = tmp / "harness-selftest.db"
    config.WORKSPACE = tmp / "workspace"
    config.WORKSPACE.mkdir(parents=True, exist_ok=True)
    store.init()
    test_stages()
    test_memory()
    test_sandbox()
    test_guards()
    test_termination()
    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
