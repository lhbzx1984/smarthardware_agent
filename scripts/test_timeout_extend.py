"""回归：超时「续期 + 自动续跑」。

线上现场：一次六阶段运行跑到 904 秒被 `档位时限 900 秒已到` 砍掉，而它当时**一直在
产出新内容**（正在出图 / 正在落硬件产物）。时间到 ≠ 失败，**停滞**才是失败。

修法两层：
1. 软续期：到点但距上次实质进展 < STALL_SECONDS → 自动延长，直到硬上限
2. 自动续跑：真到硬上限还没做完（且已有阶段产物）→ 复用同一工作区自动再开一轮
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
import guards  # noqa: E402
import store  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


# 测试不碰真实数据库：Run 的构造与事件落库都改成本地空实现
store.create_run = lambda *a, **k: None          # type: ignore[assignment]
store.append = lambda *a, **k: None              # type: ignore[assignment]
store.recall = lambda *a, **k: []                # type: ignore[assignment]

import server  # noqa: E402
import stages  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="timeout_"))
run_dir = tmp / "run1"
(run_dir / "00_prd").mkdir(parents=True)
(run_dir / "10_appearance").mkdir(parents=True)
(run_dir / "00_prd" / "prd.md").write_text("# PRD\n\n尺寸 60×35×15mm", encoding="utf-8")
(run_dir / "10_appearance" / "appearance.md").write_text("# 外观\n\n圆角矩形", encoding="utf-8")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * (40 * 1024)
(run_dir / "10_appearance" / "render-01.png").write_bytes(PNG)


def new_run() -> "server.Run":
    r = server.Run("设计一个儿童坐姿提醒器")
    r.dir = run_dir
    return r


print("== 1. 到点但仍在进展 → 自动续期，不中止 ==")
r = new_run()
base = r.max_seconds
r.started = time.time() - (base + 5)
r.last_progress = time.time() - 10          # 10 秒前刚有实质进展
try:
    r.push({"type": "tool_end", "name": "save_artifact", "result": "已写入 00_prd/x.md"})
    raised = ""
except guards.RunAborted as e:  # noqa: PERF203
    raised = str(e)
check("没有抛中止", raised == "", raised)
check("时限被延长", r.max_seconds > base, f"{base} -> {r.max_seconds}")
check("续期次数=1", r.timeout_extends == 1, str(r.timeout_extends))
check("硬上限未被越界", r.max_seconds <= r.hard_seconds, f"{r.max_seconds} vs {r.hard_seconds}")
check("推送了续期说明", any("自动延长" in str(e.get("message", "")) for e in r.events), str(r.events[-3:]))

print("== 2. 到点且长时间没进展 → 判定超时中止 ==")
r2 = new_run()
r2.started = time.time() - (r2.hard_seconds + 5)      # 已越过硬上限
r2.artifacts = set(stages.scan_dir(run_dir))          # 磁盘上的产物早已记过账，这次没有新东西
r2.last_progress = time.time() - (config.STALL_SECONDS + 60)
try:
    r2.push({"type": "tool_end", "name": "save_artifact", "result": "ok"})
    check("抛出 RunTimeout", False, "没抛异常")
except guards.RunAborted as e:
    check("抛出 RunTimeout", e.status == "timeout", f"{type(e).__name__} {e}")
    check("原因里写明停滞", "没有新进展" in str(e) and "硬上限" in str(e), str(e))

print("== 3. 提示预警（用掉 80% 时限）==")
r3 = new_run()
r3.artifacts = set(stages.scan_dir(run_dir))
r3.started = time.time() - (r3.max_seconds * 0.85)
r3.last_progress = time.time()
r3.push({"type": "ping"})
check("推了一条时限预警", any("已用掉" in str(e.get("message", "")) for e in r3.events), str(r3.events[-2:]))

print("== 4. 自动续跑 ==")
import pipeline  # noqa: E402
import stages  # noqa: E402

started: list[str] = []
server._start = lambda r: started.append(r.id)        # type: ignore[assignment]

r4 = new_run()
r4.db_status = "timeout"
prog = stages.progress_of_dir(run_dir)
check("现场已含 2 个阶段产物", prog["done"] >= 2, str(prog))
server._maybe_auto_resume(r4)
check("自动开了一轮续跑", len(started) == 1, str(started))
new_id = started[0] if started else ""
nxt = server.RUNS.get(new_id)
check("新 run 复用同一工作区", nxt is not None and nxt.dir == run_dir, str(getattr(nxt, "dir", None)))
check("续跑轮次=1", nxt is not None and nxt.auto_resume_count == 1, str(getattr(nxt, "auto_resume_count", None)))
check("记录了来源会话", nxt is not None and nxt.auto_resume_of == r4.id, str(getattr(nxt, "auto_resume_of", None)))
check("原会话给出提示", any("自动续跑" in str(e.get("message", "")) for e in r4.events), str(r4.events[-2:]))
check("续跑会话需求里带产物清单", nxt is not None and "已落盘的产物" in nxt.requirement, "")

print("== 5. 不该续跑的三种情况 ==")
started.clear()

r5 = new_run()
r5.db_status = "timeout"
r5.cancel_requested = True
server._maybe_auto_resume(r5)
check("用户手动停止 → 不续跑", not started, str(started))

r6 = new_run()
r6.db_status = "error"
server._maybe_auto_resume(r6)
check("非超时状态 → 不续跑", not started, str(started))

r7 = new_run()
r7.db_status = "timeout"
r7.auto_resume_count = config.AUTO_RESUME_MAX
server._maybe_auto_resume(r7)
check(f"达到 {config.AUTO_RESUME_MAX} 轮上限 → 不续跑", not started, str(started))

empty_dir = tmp / "empty"
empty_dir.mkdir()
r8 = new_run()
r8.dir = empty_dir
r8.db_status = "timeout"
server._maybe_auto_resume(r8)
check("一点产物都没有 → 不续跑（问题不在时间上）", not started, str(started))
check("已续跑过的会话不重复续跑", getattr(r4, "auto_resumed", False) is True, "")

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
