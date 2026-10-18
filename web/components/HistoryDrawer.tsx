"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

export type RunSummary = {
  id: string;
  title: string;
  requirement: string;
  status: string; // running | done | error | interrupted
  model?: string;
  created_at: number;
  updated_at: number;
  finished_at: number;
  elapsed: number;
  n_events: number;
  n_tools: number;
  n_artifacts: number;
  error?: string;
};

const STATUS_STYLE: Record<string, { label: string; bg: string; fg: string }> = {
  running: { label: "运行中", bg: "var(--accent-soft)", fg: "var(--accent)" },
  done: { label: "已完成", bg: "var(--accent-2-soft)", fg: "var(--accent-2)" },
  error: { label: "失败", bg: "var(--danger-soft)", fg: "var(--danger)" },
  interrupted: { label: "已中断", bg: "var(--warn-soft)", fg: "var(--warn)" },
  // 主动中止的三种：不是故障，用中性色区分开，免得和「失败」混为一谈
  cancelled: { label: "已停止", bg: "var(--bg-sunken)", fg: "var(--text-dim)" },
  timeout: { label: "超时中止", bg: "var(--warn-soft)", fg: "var(--warn)" },
  budget: { label: "超出预算中止", bg: "var(--warn-soft)", fg: "var(--warn)" },
};

function fmtTime(ts: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtDur(sec: number): string {
  if (!sec || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 时 ${m % 60} 分`;
}

function ago(ts: number): string {
  if (!ts) return "";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return "刚刚";
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  return `${Math.floor(d / 86400)} 天前`;
}

export default function HistoryDrawer({
  open,
  onClose,
  currentRunId,
  refreshKey,
  onOpenRun,
  onDeleted,
}: {
  open: boolean;
  onClose: () => void;
  currentRunId: string | null;
  /** 变化时重新拉取列表（例如一轮跑完） */
  refreshKey?: number;
  onOpenRun: (runId: string) => void;
  /** 会话被彻底删除后回调（父组件可据此清空当前视图） */
  onDeleted?: (runId: string) => void;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/agent/runs?limit=100&q=${encodeURIComponent(q)}`, {
        cache: "no-store",
      });
      const d = await r.json();
      setRuns(d.runs || []);
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, refreshKey, load]);

  const total = useMemo(
    () => runs.reduce((a, r) => a + (r.n_artifacts || 0), 0),
    [runs],
  );

  // 运行中的会话要显示「实时」耗时，否则列表里永远是入库那一刻的 0
  const hasRunning = runs.some((r) => r.status === "running");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!open || !hasRunning) return;
    const t = setInterval(() => setTick((v) => v + 1), 2000);
    return () => clearInterval(t);
  }, [open, hasRunning]);
  const nowSec = useMemo(() => Date.now() / 1000, [tick]);

  const stop = async (id: string) => {
    await fetch(`/api/agent/runs?run_id=${encodeURIComponent(id)}&action=cancel`, { method: "POST" });
    setTimeout(() => void load(), 1500);
  };

  const remove = async (id: string) => {
    setErr("");
    try {
      const r = await fetch(`/api/agent/runs?run_id=${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setErr(d.detail || "删除失败");
        return;
      }
      setConfirmId(null);
      onDeleted?.(id);
      void load();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex" role="dialog" aria-modal="true">
      <button
        aria-label="关闭历史面板"
        onClick={onClose}
        className="flex-1 cursor-default"
        style={{ background: "rgba(8, 11, 15, 0.45)", backdropFilter: "blur(3px)" }}
      />
      <aside
        className="flex h-full w-[420px] max-w-[92vw] flex-col border-l"
        style={{ background: "var(--bg-elev)", borderColor: "var(--border)", boxShadow: "var(--shadow)" }}
      >
        <div className="flex items-center gap-3 border-b px-4 py-3" style={{ borderColor: "var(--border)" }}>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">历史会话</h2>
            <p className="text-xs" style={{ color: "var(--text-faint)" }}>
              {runs.length} 个会话 · 共 {total} 个产物 · 存于 SQLite
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="magnetic ml-auto rounded-lg px-2 py-1 text-xs"
            style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
            title="刷新"
          >
            刷新
          </button>
          <button
            onClick={onClose}
            className="magnetic rounded-lg px-2 py-1 text-xs"
            style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
          >
            关闭
          </button>
        </div>

        <div className="border-b px-4 py-2.5" style={{ borderColor: "var(--border)" }}>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void load()}
            placeholder="搜索需求关键词，回车确认"
            className="w-full rounded-lg px-3 py-2 text-[13px] outline-none"
            style={{ background: "var(--bg-sunken)", border: "1px solid var(--border)", color: "var(--text)" }}
          />
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3">
          {err && (
            <div className="mb-3 rounded-lg px-3 py-2 text-xs" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
              {err}
            </div>
          )}
          {!runs.length && !loading && (
            <p className="px-2 py-8 text-center text-sm" style={{ color: "var(--text-faint)" }}>
              还没有历史会话。跑一轮设计之后，这里会自动记录需求、事件流与全部产物。
            </p>
          )}
          {runs.map((r) => {
            const st = STATUS_STYLE[r.status] || STATUS_STYLE.interrupted;
            const active = r.id === currentRunId;
            return (
              <div
                key={r.id}
                className="glass mb-2 rounded-xl p-3 transition-all"
                style={{
                  borderColor: active ? "var(--accent)" : "var(--border)",
                  boxShadow: active ? "0 0 0 1px var(--accent-soft)" : "none",
                }}
              >
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium" title={r.requirement}>
                      {r.title || r.requirement}
                    </p>
                    <p className="mt-0.5 text-xs" style={{ color: "var(--text-faint)" }}>
                      {fmtTime(r.created_at)} · {ago(r.created_at)} · 用时{" "}
                      {fmtDur(r.status === "running" ? Math.max(0, nowSec - r.created_at) : r.elapsed)}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full px-2 py-0.5 text-[11px]" style={{ background: st.bg, color: st.fg }}>
                    {st.label}
                  </span>
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]" style={{ color: "var(--text-dim)" }}>
                  <span className="rounded-md px-1.5 py-0.5" style={{ background: "var(--bg-sunken)" }}>
                    事件 {r.n_events}
                  </span>
                  <span className="rounded-md px-1.5 py-0.5" style={{ background: "var(--bg-sunken)" }}>
                    工具 {r.n_tools}
                  </span>
                  <span className="rounded-md px-1.5 py-0.5" style={{ background: "var(--bg-sunken)" }}>
                    产物 {r.n_artifacts}
                  </span>
                  <span className="rounded-md px-1.5 py-0.5" style={{ background: "var(--bg-sunken)" }}>
                    {r.id}
                  </span>
                </div>

                {r.error && (
                  <p className="mt-1.5 line-clamp-2 text-[11px]" style={{ color: "var(--danger)" }} title={r.error}>
                    {r.error}
                  </p>
                )}

                <div className="mt-2.5 flex gap-2">
                  <button
                    onClick={() => {
                      onOpenRun(r.id);
                      onClose();
                    }}
                    className="magnetic rounded-lg px-2.5 py-1.5 text-xs"
                    style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                  >
                    {r.status === "running" ? "打开并跟随" : "打开这个会话"}
                  </button>
                  {r.status === "running" && (
                    <button
                      onClick={() => void stop(r.id)}
                      className="magnetic rounded-lg px-2.5 py-1.5 text-xs"
                      style={{ background: "var(--danger-soft)", color: "var(--danger)" }}
                      title="中断这次生成，已产出的产物会保留"
                    >
                      停止
                    </button>
                  )}
                  {confirmId === r.id ? (
                    <>
                      <button
                        onClick={() => void remove(r.id)}
                        className="magnetic rounded-lg px-2.5 py-1.5 text-xs"
                        style={{ background: "var(--danger-soft)", color: "var(--danger)" }}
                        title="删除会话记录、事件流与该会话的全部产物文件，不可恢复"
                      >
                        确认删除
                      </button>
                      <button
                        onClick={() => setConfirmId(null)}
                        className="magnetic rounded-lg px-2.5 py-1.5 text-xs"
                        style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => setConfirmId(r.id)}
                      className="magnetic rounded-lg px-2.5 py-1.5 text-xs"
                      style={{ background: "var(--bg-sunken)", color: "var(--text-faint)" }}
                      title="删除会话记录、事件流与该会话的全部产物文件，不可恢复"
                    >
                      删除
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <p className="border-t px-4 py-2.5 text-[11px] leading-relaxed" style={{ borderColor: "var(--border)", color: "var(--text-faint)" }}>
          会话、事件流、产物清单都存在 <code>data/agent.db</code>（SQLite），关掉服务也不丢。
          删除会<b>连同该会话在磁盘上的全部产物一起删掉，且不可恢复</b>；删过的会话重启服务也不会再回来。
        </p>
      </aside>
    </div>
  );
}
