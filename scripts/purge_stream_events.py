"""把历史事件表里的逐条流式噪声**压缩成块**，而不是一删了之。

背景：thinking / token 曾经逐 token 落库 —— 一个卡住的 run 能写 33 万条，
历史列表的「事件 N」全是噪声，统计被拖垮，回放也慢。

现在的策略（默认）：按 (run, 类型, 子智能体) 分桶、每 N 秒合并成一条
`agg: true` 事件。内容保住了，条数降两个数量级。
想彻底不要思考过程就加 `--delete`。

用法（先停 worker，SQLite 写锁会互相等）：
    python scripts/purge_stream_events.py [--vacuum] [--window 8] [--delete]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "agent.db"

KEEP_AGG = "payload LIKE '%\"agg\": true%'"


def _compact(conn: sqlite3.Connection, window: float) -> tuple[int, int]:
    """把逐条 thinking/token 按时间窗合并。返回 (合并后条数, 删掉的条数)。"""
    run_ids = [
        r["run_id"]
        for r in conn.execute(
            f"SELECT DISTINCT run_id FROM events WHERE type IN ('thinking','token') AND NOT ({KEEP_AGG})"
        ).fetchall()
    ]
    kept = 0
    removed = 0
    for rid in run_ids:
        rows = conn.execute(
            "SELECT seq,ts,payload FROM events WHERE run_id=? AND type IN ('thinking','token')"
            f" AND NOT ({KEEP_AGG}) ORDER BY seq",
            (rid,),
        ).fetchall()

        buckets: dict[tuple[str, str], list[tuple[int, float, str]]] = {}
        for r in rows:
            try:
                ev = json.loads(r["payload"])
            except json.JSONDecodeError:
                continue
            buckets.setdefault((str(ev.get("type") or ""), str(ev.get("agent") or "")), []).append(
                (r["seq"], float(r["ts"] or 0), str(ev.get("text") or ""))
            )

        updates: list[tuple] = []
        deletes: list[tuple] = []
        for (etype, agent), items in buckets.items():
            items.sort()
            group: list[tuple[int, str]] = []
            start = None
            for seq, ts, text in items:
                if start is None:
                    start = ts
                elif ts - start > window and group:
                    updates.append(_pack(rid, etype, agent, group, start))
                    deletes.extend((rid, s) for s, _ in group[1:])
                    kept += 1
                    removed += len(group) - 1
                    group, start = [], ts
                group.append((seq, text))
            if group:
                updates.append(_pack(rid, etype, agent, group, start or 0.0))
                deletes.extend((rid, s) for s, _ in group[1:])
                kept += 1
                removed += len(group) - 1

        conn.executemany(
            "UPDATE events SET ts=?, payload=? WHERE run_id=? AND seq=?",
            [(ts, payload, r, seq) for r, seq, ts, payload in updates],
        )
        conn.executemany("DELETE FROM events WHERE run_id=? AND seq=?", deletes)
        conn.commit()
    return kept, removed


def _pack(run_id: str, etype: str, agent: str, group: list[tuple[int, str]], start: float) -> tuple:
    """一组事件压成一条：保留首个 seq（主键），正文拼接后截断。"""
    seq = group[0][0]
    text = "".join(t for _, t in group)[:6000]
    payload = json.dumps(
        {"type": etype, "agent": agent, "text": text, "agg": True, "n": len(group), "ts": start},
        ensure_ascii=False,
    )
    return (run_id, seq, start, payload)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vacuum", action="store_true", help="清理后回收磁盘空间（大库会慢）")
    ap.add_argument("--window", type=float, default=8.0, help="合并时间窗（秒），默认 8")
    ap.add_argument("--delete", action="store_true", help="直接删除而不合并（会丢失思考过程）")
    args = ap.parse_args()

    if not DB.exists():
        print(f"[purge] 找不到数据库 {DB}")
        return 1

    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row

    before = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(LENGTH(payload)),0) b FROM events").fetchone()
    noise = conn.execute(
        f"SELECT COUNT(*) n FROM events WHERE type IN ('thinking','token') AND NOT ({KEEP_AGG})"
    ).fetchone()["n"]
    print(f"[purge] 事件总数 {before['n']}（约 {before['b'] / 1e6:.1f} MB），其中逐条流式噪声 {noise} 条")

    t0 = time.time()
    if args.delete:
        conn.execute(f"DELETE FROM events WHERE type IN ('thinking','token') AND NOT ({KEEP_AGG})")
        conn.commit()
    else:
        kept, removed = _compact(conn, args.window)
        print(f"[purge] 合并成 {kept} 条，删除冗余 {removed} 条（{time.time() - t0:.1f}s）")

    # 计数、以及「进程被杀留下的 running」一并修正
    conn.execute(
        "UPDATE runs SET n_events=(SELECT COUNT(*) FROM events WHERE events.run_id=runs.id),"
        " n_tools=(SELECT COUNT(*) FROM events WHERE events.run_id=runs.id AND type='tool_end'),"
        " n_artifacts=(SELECT COUNT(*) FROM files WHERE files.run_id=runs.id)"
    )
    stale = conn.execute(
        "UPDATE runs SET status='interrupted', finished_at=COALESCE(finished_at, updated_at),"
        " error=COALESCE(NULLIF(error,''),'运行进程已终止（worker 重启或会话被杀）')"
        " WHERE status='running' AND updated_at < ?",
        (time.time() - 120,),
    ).rowcount
    conn.commit()

    after = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(LENGTH(payload)),0) b FROM events").fetchone()
    print(f"[purge] 剩 {after['n']} 条（约 {after['b'] / 1e6:.1f} MB）")
    print(f"[purge] 标记 {stale} 个残留 running 会话为 interrupted")

    if args.vacuum:
        print("[purge] VACUUM 回收空间…")
        conn.execute("VACUUM")

    conn.close()
    print("[purge] 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
