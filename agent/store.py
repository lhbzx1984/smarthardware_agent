"""SQLite 持久化：会话（run）、事件流、产物清单全部落库，重启不丢。

零依赖（stdlib sqlite3）。写策略：
- 事件先进内存缓冲，按「攒够 30 条 或 距上次落盘 2 秒」批量提交，高频小事务不拖慢流式输出
- run 生命周期（创建 / 完成 / 失败）立即提交
- **密钥永不入库**：settings 落库前先剥掉 api_key / apiKey

为什么不用 ORM：这是单机工具，SQL 只有六条，直接写更透明、更好排查。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import config

DB_PATH = Path(config.ROOT) / "data" / "agent.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_SECRET_KEYS = {"api_key", "apikey", "key", "token", "secret", "authorization"}

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

# run_id -> 未落盘事件缓冲
_buf: dict[str, list[tuple[int, float, str, str]]] = {}
_buf_count = 0
_buf_last = 0.0
FLUSH_EVERY = 30
FLUSH_SECONDS = 2.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL DEFAULT '',
  requirement  TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'running',
  provider     TEXT DEFAULT '',
  base_url     TEXT DEFAULT '',
  model        TEXT DEFAULT '',
  settings     TEXT DEFAULT '{}',
  created_at   REAL DEFAULT 0,
  updated_at   REAL DEFAULT 0,
  finished_at  REAL DEFAULT 0,
  elapsed      REAL DEFAULT 0,
  n_events     INTEGER DEFAULT 0,
  n_tools      INTEGER DEFAULT 0,
  n_artifacts  INTEGER DEFAULT 0,
  summary      TEXT DEFAULT '',
  error        TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
  run_id  TEXT NOT NULL,
  seq     INTEGER NOT NULL,
  ts      REAL,
  type    TEXT,
  payload TEXT,
  PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS files (
  run_id TEXT NOT NULL,
  path   TEXT NOT NULL,
  size   INTEGER DEFAULT 0,
  mtime  REAL DEFAULT 0,
  stage  TEXT DEFAULT '',
  PRIMARY KEY (run_id, path)
);
CREATE TABLE IF NOT EXISTS memories (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL,
  requirement  TEXT NOT NULL DEFAULT '',
  keywords     TEXT NOT NULL DEFAULT '',
  summary      TEXT NOT NULL DEFAULT '',
  stages       TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT '',
  n_artifacts  INTEGER DEFAULT 0,
  elapsed      REAL DEFAULT 0,
  created_at   REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reflexions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  pattern    TEXT NOT NULL,          -- 错误模式（英文短标识，聚类键）
  fix        TEXT NOT NULL,          -- 修正动作（可直接执行的一句话）
  priority   INTEGER DEFAULT 10,     -- 10 通用 / 20 阶段 / 30 运行时 / 40 鉴权
  hits       INTEGER DEFAULT 1,      -- 复发次数，越高越该被优先注入
  run_id     TEXT DEFAULT '',
  created_at REAL DEFAULT 0,
  updated_at REAL DEFAULT 0
);
-- 已删除会话的墓碑：记录被删掉后，磁盘目录若还在（删不掉 / 手抖重建），
-- 启动时 backfill 会把它重新扫回历史 —— 用户以为删了，重启又活过来。
CREATE TABLE IF NOT EXISTS deleted_runs (
  id         TEXT PRIMARY KEY,
  deleted_at REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_run   ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_run    ON files(run_id);
CREATE INDEX IF NOT EXISTS idx_mem_created  ON memories(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reflex_pattern ON reflexions(pattern);
"""

# 旧库迁移：runs 表新增的决策日志列（范式 / 反思条数 / 是否目标达成）
_MIGRATIONS = (
    ("runs", "paradigm", "TEXT DEFAULT ''"),
    ("runs", "n_reflexions", "INTEGER DEFAULT 0"),
    ("runs", "goal_hit", "INTEGER DEFAULT 0"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, ddl in _MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def _connect() -> sqlite3.Connection:
    global _conn
    conn = _conn
    if conn is not None:
        return conn
    # 先在局部建好再赋给全局：以前是「先把半成品连接挂到 _conn，再跑建表脚本」，
    # 健康检查的线程可能抢在 executescript 之前拿到这个连接，查询落到还不存在的表上。
    with _lock:
        if _conn is None:
            c = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")   # 读写并发不互相阻塞
            c.execute("PRAGMA synchronous=NORMAL")  # WAL 下足够安全，写入快很多
            c.executescript(_SCHEMA)
            c.commit()
            _conn = c
        return _conn


def init() -> None:
    """建库、建表，并把上次进程被杀留下的 running 回收为 interrupted。"""
    with _lock:
        conn = _connect()
        _migrate(conn)
        conn.commit()
    # 回收放在 migrate 之后：这一步原来是内联的一段 UPDATE，和 reap_orphans() 重复了，
    # 两处各自的文案还会互相覆盖 error 字段。统一走一个函数。
    reap_orphans()


def sanitize(settings: dict[str, Any] | None) -> dict[str, Any]:
    """落库前剥掉一切凭据字段。"""
    out: dict[str, Any] = {}
    for k, v in (settings or {}).items():
        if str(k).lower().replace("-", "_") in _SECRET_KEYS:
            out[k] = "***" if v else ""
        else:
            out[k] = v
    return out


def make_title(requirement: str, limit: int = 40) -> str:
    one = re.sub(r"\s+", " ", (requirement or "").strip())
    return one[:limit] + ("…" if len(one) > limit else "")


# ---------------------------------------------------------------- run 生命周期
def create_run(run_id: str, requirement: str, settings: dict[str, Any] | None = None) -> None:
    now = time.time()
    s = settings or {}
    base_url = str(s.get("base_url") or config.DEFAULT_BASE_URL)
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO runs (id,title,requirement,status,provider,base_url,model,"
            "settings,created_at,updated_at) VALUES (?,?,?,'running',?,?,?,?,?,?)",
            (
                run_id,
                make_title(requirement),
                requirement,
                str(s.get("provider") or config.DEFAULT_PROVIDER),
                base_url,
                str(s.get("model") or config.MODEL),
                json.dumps(sanitize(s), ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()


def reap_orphans(reason: str = "服务重启，本次运行未跑完") -> int:
    """把上次进程退出时残留的 running 记录收尾。

    进程被杀掉、崩溃、或整机关机时走不到 finish_run，DB 里就永远留着一条 running。
    前端「历史会话」按 status 显示 spinner，于是出现一个永远在转、点开却什么都没有的
    僵尸会话。启动时统一回收一次 —— 此时 RUNS 必然是空的，不会误伤真在跑的任务。
    """
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "UPDATE runs SET status='interrupted', error=?, updated_at=? WHERE status='running'",
            (reason, time.time()),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def finish_run(
    run_id: str,
    status: str,
    summary: str = "",
    error: str = "",
    elapsed: float = 0.0,
    paradigm: str = "",
    n_reflexions: int = 0,
    goal_hit: bool = False,
) -> None:
    flush(run_id, force=True)
    now = time.time()
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE runs SET status=?, summary=?, error=?, elapsed=?, finished_at=?, updated_at=?,"
            " paradigm=?, n_reflexions=?, goal_hit=?,"
            # 计数以事件表/文件表为准在这里一并刷新：收尾时刚做过 index_files，
            # 不刷的话历史列表里显示的是中途某个时点的旧值（实测 28 个文件显示成 2）。
            " n_tools=(SELECT COUNT(*) FROM events WHERE run_id=? AND type='tool_end'),"
            " n_artifacts=(SELECT COUNT(*) FROM files WHERE run_id=?),"
            " n_events=(SELECT COUNT(*) FROM events WHERE run_id=?) WHERE id=?",
            (
                status,
                summary[-8000:],
                error[:4000],
                round(elapsed, 1),
                now,
                now,
                paradigm[:32],
                int(n_reflexions),
                1 if goal_hit else 0,
                run_id,
                run_id,
                run_id,
                run_id,
            ),
        )
        conn.commit()


def _bump_counts(run_id: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE runs SET n_events=(SELECT COUNT(*) FROM events WHERE run_id=?),"
        " n_tools=(SELECT COUNT(*) FROM events WHERE run_id=? AND type='tool_end'),"
        " n_artifacts=(SELECT COUNT(*) FROM files WHERE run_id=?) WHERE id=?",
        (run_id, run_id, run_id, run_id),
    )


# ---------------------------------------------------------------- 事件流
def append(run_id: str, seq: int, ev: dict[str, Any]) -> None:
    """写单条事件（进缓冲）。artifact 事件顺手把产物登记进 files 表。"""
    global _buf_count, _buf_last
    etype = str(ev.get("type") or "")
    if etype == "artifact" and ev.get("path"):
        upsert_file(run_id, str(ev["path"]), int(ev.get("size") or 0), time.time())

    row = (seq, float(ev.get("ts") or time.time()), etype, json.dumps(ev, ensure_ascii=False))
    with _lock:
        _buf.setdefault(run_id, []).append(row)
        _buf_count += 1
        now = time.time()
        due = _buf_count >= FLUSH_EVERY or (now - _buf_last) >= FLUSH_SECONDS
    if due:
        flush(run_id)


def flush(run_id: str | None = None, force: bool = False) -> None:
    global _buf_count, _buf_last
    with _lock:
        if not _buf:
            return
        targets = [run_id] if run_id else list(_buf.keys())
        rows: list[tuple] = []
        touched: set[str] = set()
        for rid in targets:
            got = _buf.pop(rid, [])
            for r in got:
                rows.append((rid,) + r)
                touched.add(rid)
        if not rows:
            return
        conn = _connect()
        conn.executemany(
            "INSERT OR REPLACE INTO events (run_id,seq,ts,type,payload) VALUES (?,?,?,?,?)", rows
        )
        for rid in touched:
            _bump_counts(rid)
            conn.execute("UPDATE runs SET updated_at=? WHERE id=?", (time.time(), rid))
        conn.commit()
        _buf_count = 0
        _buf_last = time.time()


# ---------------------------------------------------------------- 产物
def upsert_file(run_id: str, path: str, size: int = 0, mtime: float = 0.0) -> None:
    rel = path.replace("\\", "/").lstrip("/")
    stage = rel.split("/")[0] if "/" in rel else ""
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO files (run_id,path,size,mtime,stage) VALUES (?,?,?,?,?)"
            " ON CONFLICT(run_id,path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime",
            (run_id, rel, int(size or 0), float(mtime or time.time()), stage),
        )
        conn.commit()


def index_files(run_id: str, root: Path) -> int:
    """以磁盘为准重建产物索引（用于收尾对账，防止漏记）。"""
    n = 0
    try:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name != "AGENTS.md":
                rel = p.relative_to(root).as_posix()
                st = p.stat()
                upsert_file(run_id, rel, st.st_size, st.st_mtime)
                n += 1
    except OSError:
        pass
    return n


def _guess_requirement(d: Path) -> str:
    """回填时猜原始需求：PRD 第一段「像人话」的那行，不要表格行和标题。

    Markdown 里最容易误抓的是 `| 项 | 内容 |` 这类表头 —— 所以要过滤掉
    以 | # > - * ` 开头、或中文占比过低的行。
    """
    prd = d / "00_prd" / "prd.md"
    cands: list[str] = []
    for src in (prd, d / "00_prd" / "prd_summary.md"):
        if not src.exists():
            continue
        try:
            lines = src.read_text(encoding="utf-8", errors="replace")[:2500].splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip().lstrip(">-*#").strip()
            if len(line) < 10 or len(line) > 220:
                continue
            if line[0] in "|`-=【":
                continue
            # 跳过「文档版本：v1.0」这种元信息行
            if re.match(r"^(文档版本|版本|日期|作者|更新)", line):
                continue
            cjk = len(re.findall(r"[\u4e00-\u9fff]", line))
            if cjk < 5 and not re.search(r"[A-Za-z]{4,}", line):
                continue
            cands.append(line)
        if cands:
            break
    if not cands:
        return ""
    # 过滤掉：文档标题、编号小节标题（「1.1 一句话产品定义」）、元信息（「**撰写人**：x」）
    noise = re.compile(
        r"产品需求文档|产品需求定义|PRD|演示数据|^\s*#{1,3}\s|^\d+(\.\d+)*[\s、.]|^\*\*[^*]{1,12}\*\*\s*[:：]"
    )
    good = [c for c in cands if not noise.search(c)]
    pool = good or cands
    # 真正的描述行通常最长：取中文字符数最多的那行
    best = max(pool, key=lambda s: len(re.findall(r"[\u4e00-\u9fff]", s)))
    # 「一句话产品定义**：xxx」这类残留的 Markdown 标记清掉
    best = re.sub(r"^\*\*[^*：:]{0,20}\*\*\s*[:：]\s*", "", best)
    return best.replace("**", "").strip()


def backfill(workspace: Path | None = None) -> int:
    """把磁盘上已有、但库里没有的旧会话补进历史。

    store 是后加的，之前跑完的那些 run 只留在 workspace/ 里，历史面板看不到。
    这里启动时扫一遍：以目录为一条会话，产物按磁盘索引，有 90_review 视为已完成。
    """
    root = Path(workspace or config.WORKSPACE)
    if not root.exists():
        return 0
    n = 0
    with _lock:
        conn = _connect()
        known = {r["id"] for r in conn.execute("SELECT id FROM runs").fetchall()}
        # 墓碑优先：删过的会话就算目录还在，也不能再出现在历史里
        tombs = {r["id"] for r in conn.execute("SELECT id FROM deleted_runs").fetchall()}
        for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir() or d.name.startswith("_") or d.name in known or d.name in tombs:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", d.name):
                continue
            files = [p for p in d.rglob("*") if p.is_file() and p.name != "AGENTS.md"]
            if not files:
                continue
            mtimes = [p.stat().st_mtime for p in files]
            created = min(mtimes)
            finished = max(mtimes)
            rels = [p.relative_to(d).as_posix() for p in files]
            has_review = any(r.startswith("90_review/") and r.endswith(".md") for r in rels)
            status = "done" if has_review else "interrupted"
            req = _guess_requirement(d)
            conn.execute(
                "INSERT OR IGNORE INTO runs (id,title,requirement,status,provider,base_url,model,"
                "settings,created_at,updated_at,finished_at,elapsed,n_events,n_tools,n_artifacts)"
                " VALUES (?,?,?,?,?,?,?,'{}',?,?,?,?,0,0,?)",
                (
                    d.name,
                    make_title(req or d.name),
                    req or d.name,
                    status,
                    config.DEFAULT_PROVIDER,
                    config.DEFAULT_BASE_URL,
                    config.MODEL,
                    created,
                    finished,
                    finished if has_review else 0.0,
                    round(finished - created, 1) if has_review else 0.0,
                    len(files),
                ),
            )
            for p in files:
                st = p.stat()
                rel = p.relative_to(d).as_posix()
                conn.execute(
                    "INSERT OR REPLACE INTO files (run_id,path,size,mtime,stage) VALUES (?,?,?,?,?)",
                    (d.name, rel, st.st_size, st.st_mtime, rel.split("/")[0]),
                )
            conn.commit()
            n += 1
    return n


# ---------------------------------------------------------------- 查询
def list_runs(limit: int = 50, offset: int = 0, keyword: str = "") -> list[dict[str, Any]]:
    conn = _connect()
    sql = (
        "SELECT id,title,requirement,status,model,created_at,updated_at,finished_at,elapsed,"
        " n_events,n_tools,n_artifacts,error FROM runs"
    )
    args: list[Any] = []
    if keyword.strip():
        sql += " WHERE requirement LIKE ? OR title LIKE ?"
        like = f"%{keyword.strip()}%"
        args += [like, like]
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [max(1, min(200, limit)), max(0, offset)]
    rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def latest_settings() -> dict[str, Any]:
    """最近一次会话实际用的模型设置（凭据已被 sanitize 成 ***）。

    用途：/health 要回答「现在出图到底用的哪个模型」。.env 只是服务端默认，
    用户在前端「模型设置 → 图像」里改过的值不会回写 .env —— 只按 .env 显示
    会让用户以为设置没生效（实测踩过：设置里填了 agnes-image-2.5-flash，
    健康检查仍显示 2.1）。会话快照才是「上一次真跑了什么」的事实来源。
    """
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT settings FROM runs WHERE settings IS NOT NULL AND settings NOT IN ('', '{}')"
            " ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {}
        data = json.loads(row["settings"] or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001  健康检查不该因为读历史设置失败而崩
        return {}


def get_run(run_id: str) -> dict[str, Any] | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["settings"] = json.loads(data.get("settings") or "{}")
    except json.JSONDecodeError:
        data["settings"] = {}
    data["files"] = [
        dict(r)
        for r in conn.execute(
            "SELECT path,size,mtime,stage FROM files WHERE run_id=? ORDER BY path", (run_id,)
        ).fetchall()
    ]
    return data


def load_events(run_id: str, after: int = 0, limit: int = 20000) -> list[dict[str, Any]]:
    """按 seq 顺序回放事件。seq 从 0 开始，after 表示「已收到 < after 的部分」。"""
    flush(run_id, force=True)
    conn = _connect()
    rows = conn.execute(
        "SELECT payload FROM events WHERE run_id=? AND seq>=? ORDER BY seq LIMIT ?",
        (run_id, max(0, after), max(1, min(50000, limit))),
    ).fetchall()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["payload"]))
        except json.JSONDecodeError:
            continue
    return out


def count_events(run_id: str) -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) AS n FROM events WHERE run_id=?", (run_id,)).fetchone()
    return int(row["n"]) if row else 0


def is_deleted(run_id: str) -> bool:
    """这个会话是不是已经被删过（墓碑命中）。backfill 用它避免把删掉的会话扫回来。"""
    conn = _connect()
    row = conn.execute("SELECT id FROM deleted_runs WHERE id=?", (run_id,)).fetchone()
    return row is not None


def delete_run(run_id: str, purge_disk: bool = True) -> dict[str, Any]:
    """彻底删除一次会话：库记录 + 事件 + 产物清单 + 情景记忆 + 反思 + 磁盘产物目录。

    「确认移除」的语义就是真删：只删库不删盘的话，启动时 backfill 扫到 workspace 下
    那个还在的目录，会把会话原样插回历史 —— 用户看到的就是「删了，重启又回来了」。
    所以这里除了删盘，还额外插一条墓碑：万一目录删不掉（占用/权限），也不会复活。

    返回 {ok, deleted_files, purged, tombstoned}。
    """
    import shutil

    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
        existed = cur.rowcount > 0
        conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM files WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM memories WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM reflexions WHERE run_id=?", (run_id,))
        # 墓碑先落库：哪怕后面删盘失败，这条会话也不会再被 backfill 扫回来
        conn.execute(
            "INSERT OR REPLACE INTO deleted_runs (id, deleted_at) VALUES (?, ?)",
            (run_id, time.time()),
        )
        conn.commit()

    purged = False
    if purge_disk:
        d = Path(config.WORKSPACE) / run_id
        if d.exists() and d.is_dir():
            try:
                shutil.rmtree(d)
                purged = True
            except OSError:
                # 目录被占用（Windows 上常见）：改名留痕，墓碑已经挡住复活了
                try:
                    d.rename(d.with_name(f"_{run_id}_deleted"))
                    purged = True
                except OSError:
                    purged = False

    return {
        "ok": existed,
        "purged": purged,
        "tombstoned": True,
        "run_id": run_id,
    }


# ---------------------------------------------------------------- 情景记忆
def _tokens(text: str) -> set[str]:
    """中文取 2-gram、英文取整词 —— 不需要分词器也能算相似度。"""
    s = (text or "").lower()
    out = {m.group(0) for m in re.finditer(r"[a-z0-9]{3,}", s)}
    cjk = re.findall(r"[\u4e00-\u9fff]", s)
    out.update("".join(cjk[i : i + 2]) for i in range(len(cjk) - 1))
    return out


def save_memory(
    run_id: str,
    requirement: str,
    summary: str,
    status: str = "",
    stages: str = "",
    n_artifacts: int = 0,
    elapsed: float = 0.0,
) -> None:
    """run 结束时把「需求 → 结果」写进情景记忆，下次遇到相似需求可以直接借鉴。"""
    if not (requirement or "").strip():
        return
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO memories (run_id,requirement,keywords,summary,stages,status,"
            "n_artifacts,elapsed,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                requirement.strip()[:1000],
                " ".join(sorted(_tokens(requirement)))[:4000],
                (summary or "")[:6000],
                stages[:500],
                status[:32],
                int(n_artifacts),
                round(float(elapsed), 1),
                time.time(),
            ),
        )
        conn.commit()


def recall(requirement: str, limit: int = 3, min_score: float = 0.12) -> list[dict[str, Any]]:
    """按相似度捞出历史会话。分数 = |A∩B| / sqrt(|A|·|B|)，低于阈值的不注入。"""
    q = _tokens(requirement)
    if not q:
        return []
    conn = _connect()
    rows = conn.execute(
        "SELECT id,run_id,requirement,keywords,summary,stages,status,n_artifacts,elapsed"
        " FROM memories ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        hist = set((r["keywords"] or "").split()) or _tokens(r["requirement"])
        if not hist:
            continue
        inter = len(q & hist)
        if not inter:
            continue
        score = inter / (len(q) * len(hist)) ** 0.5
        if score < min_score:
            continue
        scored.append((score, dict(r)))
    scored.sort(key=lambda x: -x[0])
    return [{"score": round(s, 3), **d} for s, d in scored[: max(1, limit)]]


def memory_hint(requirement: str, limit: int = 2) -> str:
    """把历史经验渲染成可直接塞进 system prompt 的一段话；没有命中就返回空串。"""
    hits = recall(requirement, limit=limit)
    if not hits:
        return ""
    lines = ["【历史经验 · 相似需求参考】下面是以前跑过的相近需求，避免重复踩坑、也别照抄，要按本次需求重新判断："]
    for h in hits:
        stages = h.get("stages") or "—"
        lines.append(
            f"- 需求：{h['requirement'][:120]}\n"
            f"  结果：{h['status'] or '未知'} · 阶段 {stages} · 产物 {h['n_artifacts']} 个 · 用时 {int(h['elapsed'])} 秒"
        )
        body = (h.get("summary") or "").strip().replace("\n", " ")
        if body:
            lines.append(f"  结论摘要：{body[:400]}")
    return "\n".join(lines)


# ---------------------------------------------------------------- Reflexion 反思规则库
# 反思规则不是让模型再跑一轮「请你反思一下」—— 那样又慢又贵，而且小模型写不出可执行规则。
# 这里用**确定性抽取**：从本次运行的终态（status / error / 缺失阶段）直接映射到
# 「错误模式 → 修正动作」，同 pattern 复发就累加 hits。规则越常犯，下次注入时优先级越高。
_STAGE_LABEL = {
    "00_prd": "需求分析（00_prd）",
    "10_appearance": "外观设计（10_appearance）",
    "20_structure": "结构设计（20_structure）",
    "30_hardware": "硬件设计（30_hardware）",
    "40_firmware": "固件设计（40_firmware）",
    "90_review": "集成评审（90_review）",
}


def _extract(status: str, error: str, missing: list[str], elapsed: float, n_artifacts: int) -> list[tuple[str, str, int]]:
    """(pattern, fix, priority)。优先级：40 鉴权 > 30 运行时 > 20 阶段 > 10 通用。"""
    out: list[tuple[str, str, int]] = []
    err = (error or "").lower()
    st = (status or "").strip().lower()

    if st == "timeout":
        out.append((
            "run_timeout",
            "每产出一份就立刻 save_artifact 落盘，不要攒到最后一次性写；"
            "长任务优先交付已完成的阶段，剩余部分在文档里标注「待续」。",
            30,
        ))
    if st == "budget":
        out.append((
            "tool_budget_exhausted",
            "同一工具不要重复调用：先 ls 工作区确认已有哪些文件，只补缺失的部分；"
            "不要为了「再优化一版」重跑已经落盘的产物。",
            30,
        ))
    if st == "cancelled":
        out.append((
            "user_cancelled",
            "输出节奏太慢会让用户中途叫停：先回交阶段产物再继续深化，别等全做完才说话。",
            10,
        ))

    if any(k in err for k in ("401", "403", "invalid token", "鉴权")):
        out.append((
            "image_auth_failed",
            "图像网关鉴权失败一律不重试（重试多少次都是 401）：直接调 render_product_views "
            "出离线工程视图与爆炸图，并在文档里注明 AI 图不可用的原因。",
            40,
        ))
    if any(k in err for k in ("ssl", "certificate", "server disconnected", "connection", "连接", "证书")):
        out.append((
            "gateway_unreachable",
            "网关不可达时不要反复重试联网工具：切到本地离线工具（build_structure / "
            "render_schematic / render_product_views），联网项在文档里标注「待联网补做」。",
            35,
        ))
    if any(k in err for k in ("build123d", "建模", "cad")):
        out.append((
            "cad_script_failed",
            "build123d 脚本报错时照抄已实测模板改尺寸，不要用 Workplanes / offset(amount=-w) "
            "/ Cylinder(r=,h=)（0.13 已移除或语义不同）；3 次仍失败就给参数化尺寸表兜底，禁止伪造尺寸。",
            25,
        ))
    if any(k in err for k in ("重复", "repeat")):
        out.append((
            "repeat_call_loop",
            "同一工具同一参数重复调用 = 无效循环：换参数或换工具，不要原地重试。",
            30,
        ))

    for m in missing:
        out.append((
            f"stage_missing_{m}",
            f"{_STAGE_LABEL.get(m, m)} 阶段没有产物：必须委派对应子智能体，"
            f"并确认它用 save_artifact 把文件落到 {m}/ 目录下，不能只写在回复里。",
            20,
        ))

    if elapsed > 300 and n_artifacts < 3:
        out.append((
            "idle_no_artifact",
            "长时间思考却不落盘 = 空转：先把当前结论写成文件，再继续优化内容。",
            25,
        ))
    return out


def save_reflexions(
    run_id: str,
    status: str,
    error: str = "",
    missing_stages: list[str] | None = None,
    elapsed: float = 0.0,
    n_artifacts: int = 0,
) -> int:
    """run 收尾时沉淀反思规则。同 pattern 复发累加 hits，返回写入条数。"""
    rules = _extract(status, error, list(missing_stages or []), elapsed, n_artifacts)
    if not rules:
        return 0
    now = time.time()
    with _lock:
        conn = _connect()
        for pattern, fix, prio in rules:
            conn.execute(
                "INSERT INTO reflexions (pattern,fix,priority,hits,run_id,created_at,updated_at)"
                " VALUES (?,?,?,1,?,?,?)"
                " ON CONFLICT(pattern) DO UPDATE SET hits=hits+1, updated_at=?,"
                " priority=MAX(priority,excluded.priority), run_id=excluded.run_id",
                (pattern, fix, prio, run_id, now, now, now),
            )
        conn.commit()
    # 规则库会膨胀到拖慢检索 —— 超阈值就聚类压缩（见 cluster_reflexions）
    if len(rules) and count_reflexions() > config.REFLEXION_CLUSTER_AT:
        cluster_reflexions()
    return len(rules)


def count_reflexions() -> int:
    """规则条数。只读统计，**绝不能因为查不到就把健康检查拖崩** —— 它出现在 /health 里。"""
    try:
        row = _connect().execute("SELECT COUNT(*) AS n FROM reflexions").fetchone()
    except sqlite3.Error:
        return 0
    if row is None:
        return 0
    try:
        return int(row["n"])
    except (KeyError, TypeError, ValueError):
        return 0


def recall_reflexions(limit: int = 6, min_priority: int = 0) -> list[dict[str, Any]]:
    """按「优先级 × 复发次数」排序取规则。高优先级 + 常犯的排在最前。"""
    try:
        rows = _connect().execute(
            "SELECT pattern,fix,priority,hits FROM reflexions WHERE priority>=?"
            " ORDER BY priority DESC, hits DESC, updated_at DESC LIMIT ?",
            (int(min_priority), max(1, limit)),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def reflexion_hint(limit: int = 6) -> str:
    """渲染成可直接塞进 system prompt 的一段话；没有规则就返回空串。"""
    rules = recall_reflexions(limit=limit)
    if not rules:
        return ""
    lines = [
        "【反思规则 · 以前踩过的坑】下面是历史会话沉淀下来的规则，"
        "按（优先级 × 复发次数）排序。**与本次情况冲突时以实际情况为准**，但不要无视它们："
    ]
    for r in rules:
        lines.append(f"- [{r['pattern']}｜犯过 {r['hits']} 次] {r['fix']}")
    return "\n".join(lines)


def cluster_reflexions(keep_per_group: int = 1) -> int:
    """规则库膨胀后的聚类压缩：按 pattern 前缀（下划线前第一段）归组，
    每组只保留最高优先级 + 累计 hits 的那条元规则，其余删除。

    实战数据：2000 条规则压到 12 条元规则，检索快 20 倍，且更不容易互相矛盾。
    """
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT id,pattern,fix,priority,hits,run_id,created_at FROM reflexions"
            " ORDER BY priority DESC, hits DESC, updated_at DESC"
        ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            groups.setdefault(str(r["pattern"]).split("_")[0], []).append(r)
        deleted = 0
        for _g, members in groups.items():
            if len(members) <= keep_per_group:
                continue
            head = members[0]
            total = sum(int(m["hits"]) for m in members)
            # 元规则的 fix 取最具代表性那条，并在末尾标注它覆盖的分支数
            conn.execute(
                "UPDATE reflexions SET hits=?, fix=? WHERE id=?",
                (
                    total,
                    f"{head['fix']}（本条由 {len(members)} 条同类规则归并）",
                    head["id"],
                ),
            )
            for m in members[keep_per_group:]:
                conn.execute("DELETE FROM reflexions WHERE id=?", (m["id"],))
                deleted += 1
        conn.commit()
    return deleted


def stats() -> dict[str, Any]:
    conn = _connect()
    row = conn.execute(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(n_events),0) AS events,"
        " COALESCE(SUM(n_artifacts),0) AS artifacts FROM runs"
    ).fetchone()
    # 已完成会话的平均耗时 —— 前端拿它估算「还剩多久」
    _a = conn.execute(
        "SELECT COALESCE(AVG(elapsed),0) AS a FROM runs WHERE status='done' AND elapsed>0"
    ).fetchone()
    avg = (_a["a"] if _a is not None else 0) or 0
    # 决策日志：范式分布 + 各范式的健康度（平均工具数 / 平均时长 / 目标达成率）
    paradigms: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT paradigm, COUNT(*) AS n, COALESCE(AVG(n_tools),0) AS tools,"
        " COALESCE(AVG(elapsed),0) AS sec, COALESCE(SUM(goal_hit),0) AS hit"
        " FROM runs WHERE paradigm<>'' GROUP BY paradigm"
    ):
        # 全部走 `or 0`：并发写库（跑回归测试 / 真删除级联）时这些聚合列可能取到 NULL，
        # 一旦抛 TypeError，/health 直接 500 —— 前端就把「服务正常」显示成「服务异常」。
        n = int(r["n"] or 0) or 1
        paradigms[str(r["paradigm"] or "unknown")] = {
            "runs": int(r["n"] or 0),
            "avg_tools": round(float(r["tools"] or 0), 1),
            "avg_seconds": round(float(r["sec"] or 0), 1),
            "goal_rate": round(int(r["hit"] or 0) / n, 2),
        }
    # 聚合查询理论上必有一行，但启动瞬间 /health 与建表/迁移线程抢同一个连接时
    # 实测取到过 None（TypeError → /health 500 → 前端把「服务正常」显示成「服务异常」）。
    # 取不到就按空库返回：健康检查不该因为统计口径崩掉。
    return {
        "db": str(DB_PATH),
        **(dict(row) if row is not None else {"runs": 0, "events": 0, "artifacts": 0}),
        "avg_elapsed": round(float(avg or 0.0), 1),
        "reflexions": count_reflexions(),
        "paradigms": paradigms,
    }


def close() -> None:
    global _conn
    with _lock:
        flush(force=True)
        if _conn is not None:
            _conn.close()
            _conn = None


__all__: Iterable[str] = (
    "init",
    "create_run",
    "finish_run",
    "append",
    "flush",
    "upsert_file",
    "index_files",
    "list_runs",
    "get_run",
    "load_events",
    "count_events",
    "delete_run",
    "save_memory",
    "recall",
    "memory_hint",
    "save_reflexions",
    "recall_reflexions",
    "reflexion_hint",
    "count_reflexions",
    "cluster_reflexions",
    "stats",
    "close",
)
