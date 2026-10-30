"""回归：write_file / edit_file 直写的产物也要进事件流。

真实事故（run `e60f46e54e23`）：固件阶段用 write_file 写了 firmware.md / simulation.md，
磁盘上有、事件流里没有。我拿着 `/api/runs/{id}` 的 files 字段（只认 artifact 事件）去
监控，就误判成「固件阶段卡住 35 分钟没产出」——其实 14 分钟就跑完了，后面还是我重启
worker 把它打断的。用户刷新后走扫磁盘的 files 接口才看得到，运行中的实时面板一直是空的。

根因：`artifact` 事件只有 `save_artifact` 会发，而 deepagents 自带的 write_file / edit_file
不走我们的 emit。系统里早有磁盘扫描兜底（_maybe_scan_goal），但它只把路径并进
`self.artifacts` 判据，没有推事件。

本测试钉死：扫到的新文件会补发 artifact 事件，且不重复、不递归、不误报内部台账文件。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="artifact-sync-"))
    try:
        config.WORKSPACE = tmp / "workspace"
        config.WORKSPACE.mkdir(parents=True, exist_ok=True)
        config.DB_PATH = tmp / "db.sqlite3"
        config.DISK_SCAN_SECONDS = 0.0        # 关掉节流，测试不等 5 秒

        import server  # noqa: E402  （要在 config 指好路径之后才 import）

        print("[1] write_file 直写能补发登记")
        r = server.Run("测试产物补登记", {})
        before = [e for e in r.events if e.get("type") == "artifact"]
        check("起始无产物事件", not before, str(before))

        # 模拟子智能体绕过 save_artifact：直接往工作区落盘
        (r.dir / "40_firmware").mkdir(parents=True, exist_ok=True)
        (r.dir / "40_firmware" / "firmware.md").write_text("# 固件\n", encoding="utf-8")
        (r.dir / "40_firmware" / "simulation.md").write_text("# 仿真\n", encoding="utf-8")
        # 内部台账不能被当成交付物
        (r.dir / "40_firmware" / "_stage.json").write_text("{}", encoding="utf-8")

        # 任意一次写类工具结束都会触发扫描（push 里 tool_end 挂钩）
        t0 = time.time()
        r.push({"type": "tool_end", "cid": "c1", "agent": "firmware-engineer", "name": "write_file", "args": "{}"})
        check("扫描不卡死（<2s）", time.time() - t0 < 2.0, f"{time.time() - t0:.1f}s")

        paths = [e["path"] for e in r.events if e.get("type") == "artifact"]
        check("补发了 firmware.md", "40_firmware/firmware.md" in paths, str(paths))
        check("补发了 simulation.md", "40_firmware/simulation.md" in paths, str(paths))
        check("内部台账不算产物", "40_firmware/_stage.json" not in paths, str(paths))
        src = {e.get("source") for e in r.events if e.get("type") == "artifact"}
        check("补发事件标了来源", src == {"disk"}, str(src))
        sizes = {e["path"]: e.get("size") for e in r.events if e.get("type") == "artifact"}
        check("size 非空", sizes.get("40_firmware/firmware.md", 0) > 0, str(sizes))

        print("[2] 不重复补发")
        n1 = len([e for e in r.events if e.get("type") == "artifact"])
        r.push({"type": "tool_end", "cid": "c2", "agent": "firmware-engineer", "name": "edit_file", "args": "{}"})
        r.push({"type": "tool_end", "cid": "c3", "agent": "firmware-engineer", "name": "read_file", "args": "{}"})
        n2 = len([e for e in r.events if e.get("type") == "artifact"])
        check("同一文件只登记一次", n1 == n2, f"{n1} -> {n2}")

        print("[3] save_artifact 登记过的不再补发")
        r2 = server.Run("测试不重复登记", {})
        r2.push({"type": "artifact", "path": "30_hardware/bom.csv", "size": 10})
        (r2.dir / "30_hardware").mkdir(parents=True, exist_ok=True)
        (r2.dir / "30_hardware" / "bom.csv").write_text("a,b\n", encoding="utf-8")
        r2.push({"type": "tool_end", "cid": "c1", "agent": "hardware-engineer", "name": "write_file", "args": "{}"})
        cnt = len([e for e in r2.events if e.get("type") == "artifact" and e.get("path") == "30_hardware/bom.csv"])
        check("bom.csv 只登记一次", cnt == 1, f"出现 {cnt} 次")

        print("[4] 补发事件仍然进目标判据")
        r3 = server.Run("测试判据", {})
        for rel in (
            "00_prd/prd.md", "10_appearance/appearance.md", "20_structure/structure.md",
            "30_hardware/bom.csv", "40_firmware/firmware.ino", "90_review/review.md",
        ):
            p = r3.dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        r3.push({"type": "tool_end", "cid": "c1", "agent": "main", "name": "write_file", "args": "{}"})
        check("六阶段齐全被判出来", r3.goal_at > 0, str(r3.goal_at))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {PASS} / 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
