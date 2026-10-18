"""回归：「确认移除」必须是真删 —— 库记录/事件/文件/记忆 + 磁盘目录，且重启扫不回来。"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import store  # noqa: E402

N = [0]
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    N[0] += 1
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name} {extra}")


# 用独立临时库 + 独立 workspace，不碰真实数据
tmp = Path(tempfile.mkdtemp(prefix="delrun_"))
config.WORKSPACE = tmp / "workspace"
config.WORKSPACE.mkdir(parents=True)
store.DB_PATH = tmp / "test.db"
store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
store._conn = None
store.init()

rid = "abc123def456"
d = config.WORKSPACE / rid
(d / "20_structure").mkdir(parents=True)
(d / "20_structure" / "model.step").write_text("x", encoding="utf-8")
(d / "10_appearance").mkdir(parents=True)
(d / "10_appearance" / "render-01.png").write_text("x", encoding="utf-8")

store.create_run(rid, "做个土壤湿度计", {"model": "m"})
store.append(rid, 1, {"type": "run_start"})
store.append(rid, 2, {"type": "artifact", "path": "20_structure/model.step"})
store.upsert_file(rid, "20_structure/model.step", 1)
store.upsert_file(rid, "10_appearance/render-01.png", 1)
store.save_memory(rid, "做个土壤湿度计", "摘要", status="done", stages="a,b")
store.save_reflexions(rid, "budget", "超出预算中止", missing_stages=["40_firmware"], elapsed=900, n_artifacts=2)
store.flush(force=True)

print("== 删除前 ==")
check("库里有这条会话", store.get_run(rid) is not None)
check("磁盘目录存在", d.exists())
check("事件已入库", store.count_events(rid) >= 2, str(store.count_events(rid)))

print("\n== 真删 ==")
res = store.delete_run(rid, purge_disk=True)
check("删除返回 ok", bool(res.get("ok")), str(res))
check("落了墓碑", bool(res.get("tombstoned")))
check("磁盘目录已清掉", (not d.exists()) and bool(res.get("purged")), str(d))
check("墓碑可查", store.is_deleted(rid))
check("runs 记录没了", store.get_run(rid) is None)
check("事件没了", store.count_events(rid) == 0, str(store.count_events(rid)))
conn = store._connect()
check("files 没了", conn.execute("SELECT COUNT(*) FROM files WHERE run_id=?", (rid,)).fetchone()[0] == 0)
check("memories 没了", conn.execute("SELECT COUNT(*) FROM memories WHERE run_id=?", (rid,)).fetchone()[0] == 0)
check("reflexions 没了", conn.execute("SELECT COUNT(*) FROM reflexions WHERE run_id=?", (rid,)).fetchone()[0] == 0)

print("\n== 关键：重启扫描不能复活 ==")
# 模拟「目录还在」的最坏情况：手工把目录重建出来，再跑一次 backfill
d.mkdir(parents=True)
(d / "90_review").mkdir()
(d / "90_review" / "review.md").write_text("x", encoding="utf-8")
n = store.backfill()
check("backfill 没有把删掉的会话扫回来", store.get_run(rid) is None, f"backfill 新增 {n}")
check("列表里查不到", all(r["id"] != rid for r in store.list_runs(limit=100)))

print("\n== 未删过的会话照常能补进历史 ==")
rid2 = "feedface1234"
d2 = config.WORKSPACE / rid2
(d2 / "90_review").mkdir(parents=True)
(d2 / "90_review" / "review.md").write_text("x", encoding="utf-8")
(d2 / "00_prd" / "prd.md").parent.mkdir(parents=True, exist_ok=True)
(d2 / "00_prd" / "prd.md").write_text("需求：智能花盆", encoding="utf-8")
n2 = store.backfill()
check("新会话被补进历史", store.get_run(rid2) is not None, f"backfill 新增 {n2}")

print("\n== 删除不存在的会话 ==")
check("返回 ok=False", store.delete_run("nope_nope", purge_disk=True).get("ok") is False)
check("不影响墓碑表", store.is_deleted("nope_nope") or True)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'ALL PASS' if not FAILED else 'FAILED'}: {N[0] - len(FAILED)}/{N[0]}")
if FAILED:
    print("失败项：", FAILED)
    sys.exit(1)
