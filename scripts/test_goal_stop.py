"""目标达成判据 + 无效回读循环 自检。

背景（真事）：一次续跑在 11 分钟时界面显示「规划 6/6 完成」，但任务一直转着不停，
最后用户手动点了停止。磁盘上六阶段 28 个文件其实全齐，run 的 goal_hit 却是 0。

两个根因：
1. **产物记账只认 `artifact` 事件**，而该事件只有 `save_artifact` 会发。子智能体更常用
   `write_file` / `edit_file` 直接落盘 → 事件里只有 1 个 artifact → 「六阶段齐全」永远
   判不出来 → 自动收尾永不触发。
2. **只读工具的返回内容被算作「进展」** → 评审子智能体反复回读自己刚写的报告，每次都
   刷新进展时刻，停滞判据形同虚设，只能等总时限兜底或用户手动掐。

这个脚本把两条都钉死：磁盘是产物的权威；读文件不算进展。

零框架依赖：python scripts/test_goal_stop.py
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import guards  # noqa: E402
import server  # noqa: E402

PASS = 0
FAIL = 0

STAGE_FILES = [
    "00_prd/prd.md",
    "10_appearance/appearance.md",
    "20_structure/structure.md",
    "30_hardware/hardware.md",
    "40_firmware/firmware.ino",
    "90_review/review.md",
]


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def _fresh_run(title: str = "收尾判据自检") -> server.Run:
    r = server.Run(title, {})
    r._last_scan = 0.0  # 关掉节流，测试里要立即看到扫描结果
    return r


def _drop(r: server.Run) -> None:
    shutil.rmtree(r.dir, ignore_errors=True)


def _write_all(r: server.Run) -> None:
    for rel in STAGE_FILES:
        p = r.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("内容", encoding="utf-8")


def test_disk_is_authority() -> None:
    print("[1] 磁盘产物是权威 —— 不走 save_artifact 也要能判定目标达成")
    r = _fresh_run()
    try:
        _write_all(r)
        # 只发一个写类工具的结果（write_file），**不发 artifact 事件**
        r.push({"type": "tool_start", "cid": "c1", "name": "write_file",
                "args": '{"file_path":"/90_review/review.md"}'})
        r.push({"type": "tool_end", "cid": "c1", "name": "write_file",
                "result": "已写入 90_review/review.md"})
        check("六阶段文件被扫进记账", len(r.artifacts) >= 6, str(sorted(r.artifacts)))
        check("目标达成标记置位", r.goal_hit)
        check("已进入收尾宽限期", r.goal_at > 0)
        types = [e.get("type") for e in r.events]
        check("推送了 goal_reached 事件", "goal_reached" in types, str(types[-5:]))
    finally:
        _drop(r)


def test_partial_not_finished() -> None:
    print("[2] 阶段没齐 —— 不许误判达成")
    r = _fresh_run()
    try:
        for rel in STAGE_FILES[:3]:
            p = r.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        r.push({"type": "tool_end", "cid": "c1", "name": "write_file", "result": "ok"})
        check("三阶段不足 → 未达成", not r.goal_hit and r.goal_at == 0.0)
    finally:
        _drop(r)


def test_read_only_is_not_progress() -> None:
    print("[3] 只读工具返回内容不算进展")
    r = _fresh_run()
    try:
        before = r.last_progress
        time.sleep(0.01)
        r.push({"type": "tool_start", "cid": "c1", "name": "read_file",
                "args": '{"file_path":"/90_review/review.md"}'})
        r.push({"type": "tool_end", "cid": "c1", "name": "read_file", "result": "报告正文"})
        check("read_file 不刷新进展时刻", r.last_progress == before, f"{r.last_progress} vs {before}")

        mark = r.progress_marks
        r.push({"type": "tool_start", "cid": "c2", "name": "write_file",
                "args": '{"file_path":"/00_prd/prd.md"}'})
        r.push({"type": "tool_end", "cid": "c2", "name": "write_file", "result": "已写入"})
        check("写类工具刷新进展时刻", r.last_progress > before)
        check("写类工具计入进展标记", r.progress_marks > mark)
    finally:
        _drop(r)


def test_same_file_reread_caught() -> None:
    print("[4] 反复回读同一个文件 —— 必须被判定为空转并中止")
    r = _fresh_run()
    try:
        _write_all(r)
        r.push({"type": "tool_end", "cid": "c0", "name": "write_file", "result": "ok"})
        check("（前置）六阶段已达成", r.goal_hit)
        # 把进展时刻推回很久以前：这段时间确实什么都没产出来
        r.last_progress = time.time() - config.STALL_SECONDS - 60
        hit = None
        try:
            for i in range(config.REPEAT_LIMIT_SAMEFILE + 2):
                # 同一个文件，每次 offset 都不同 —— 归一化后必须算同一个签名
                r.push({"type": "tool_start", "cid": f"r{i}", "name": "read_file",
                        "args": f'{{"file_path":"/90_review/review.md","offset":{i * 50}}}'})
                r.push({"type": "tool_end", "cid": f"r{i}", "name": "read_file",
                        "result": "报告正文（又是一样的内容）"})
        except guards.RunAborted as e:
            hit = e
        check("同文件回读被拦下", hit is not None)
        check("产物已齐 → 定性为目标达成而非失败", isinstance(hit, guards.RunGoal), str(type(hit)))
        if hit is not None:
            check("RunGoal 状态为 done（前端显示交付）", hit.status == "done", str(hit.status))
    finally:
        _drop(r)


def test_different_files_not_killed() -> None:
    print("[5] 读不同文件 —— 正常协作，不许误杀")
    r = _fresh_run()
    try:
        hit = None
        try:
            for i in range(config.REPEAT_LIMIT_SAMEFILE + 6):
                r.push({"type": "tool_start", "cid": f"c{i}", "name": "read_file",
                        "args": f'{{"file_path":"/00_prd/p{i}.md"}}'})
                r.push({"type": "tool_end", "cid": f"c{i}", "name": "read_file", "result": f"内容 {i}"})
        except guards.RunAborted as e:
            hit = e
        check("读 16 个不同文件全程放行", hit is None, str(hit))
    finally:
        _drop(r)


def test_tool_path_parse() -> None:
    print("[6] 参数解析边界 —— 残缺参数不许当路径用")
    check("空串 → 无路径", server._tool_path("read_file", "") == "")
    check("半截 JSON → 无路径", server._tool_path("read_file", '{"file_path":"/a') == "")
    check("占位 {} → 无路径", server._tool_path("read_file", "{}") == "")
    check("file_path 提取", server._tool_path("read_file", '{"file_path":"/00_prd/prd.md"}') == "/00_prd/prd.md")
    check("path 提取并归一化分隔符", server._tool_path("ls", '{"path":"C:\\\\a\\\\b"}') == "C:/a/b")
    check("filename 提取", server._tool_path("generate_product_image", '{"filename":"30_hardware/pcb.png"}') == "30_hardware/pcb.png")

    r = _fresh_run()
    try:
        check(
            "同文件不同 offset 归一为同一签名",
            r._sig_of("read_file", '{"file_path":"/a.md","offset":0}')
            == r._sig_of("read_file", '{"file_path":"/a.md","offset":900}'),
        )
        check(
            "不同文件签名不同",
            r._sig_of("read_file", '{"file_path":"/a.md"}')
            != r._sig_of("read_file", '{"file_path":"/b.md"}'),
        )
        check(
            "写类工具签名仍含完整参数",
            r._sig_of("save_artifact", '{"path":"a.md"}')
            != r._sig_of("save_artifact", '{"path":"b.md"}'),
        )
    finally:
        _drop(r)


def main() -> int:
    test_disk_is_authority()
    test_partial_not_finished()
    test_read_only_is_not_progress()
    test_same_file_reread_caught()
    test_different_files_not_killed()
    test_tool_path_parse()
    print(f"\n通过 {PASS} / 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
