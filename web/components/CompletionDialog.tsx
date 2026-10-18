"use client";

import { useEffect, useMemo, useState } from "react";

/** 交付完成弹窗：拿到渲染图就当主视觉，配阶段进度与耗时，给用户一个明确的「收工」信号 */

export type DlgFile = { path: string; size?: number; mtime?: number };
export type DlgStage = {
  id: string;
  label: string;
  plan_title?: string;
  status?: string;
  n_files?: number;
  files?: { path: string }[];
};

const RASTER = /\.(png|jpe?g|webp|gif|bmp)$/i;
const VECTOR = /\.svg$/i;

/**
 * 主图打分：产品渲染图最高，外观阶段次之，结构预览再次；
 * 爆炸图/正交图是工程图不是产品主视角，往后排。序号小的靠前（render-01 是首个视角）。
 */
function rank(p: string): number {
  const s = p.toLowerCase();
  let n = 0;
  if (/(render|渲染|product|产品|cover|hero|main)/.test(s)) n += 100;
  if (/(10_appearance|外观)/.test(s)) n += 40;
  if (/(20_structure|结构|preview)/.test(s)) n += 20;
  if (/(exploded|爆炸)/.test(s)) n -= 40;
  if (/(ortho|三视|四视|sheet)/.test(s)) n -= 20;
  const m = s.match(/(\d+)(?=\D*$)/);
  if (m) n -= Number(m[1]);
  return n;
}

function isVector(p: string) {
  return VECTOR.test(p);
}

/** 摘要里挑出人话：去掉标题符号、列表符号、表格行，只留前几句 */
function plainLines(md: string, n: number): string[] {
  return md
    .split("\n")
    .map((l) => l.replace(/^#{1,6}\s*/, "").replace(/^[-*+]\s*/, "").replace(/\*\*/g, "").trim())
    .filter((l) => l && !l.startsWith("|") && !l.startsWith("---"))
    .slice(0, n);
}

export default function CompletionDialog({
  open,
  runId,
  files,
  manifest,
  title,
  summary,
  elapsedText,
  onClose,
  onOpenFile,
  downloadHref,
}: {
  open: boolean;
  runId: string | null;
  files: DlgFile[];
  manifest: { stages?: DlgStage[]; next?: string | null } | null;
  title: string;
  summary: string;
  elapsedText: string;
  onClose: () => void;
  onOpenFile: (path: string) => void;
  downloadHref: string;
}) {
  const [hero, setHero] = useState<string>("");
  // 取不到的图（产物被后续阶段清理、链接失效）单独记账，自动退到下一张，不留空白框
  const [broken, setBroken] = useState<string[]>([]);

  const gallery = useMemo(() => {
    const byRank = (a: DlgFile, b: DlgFile) => rank(b.path) - rank(a.path);
    const raster = files.filter((f) => RASTER.test(f.path)).sort(byRank);
    // 没出位图时（出图失败/被跳过），用外观视图、结构预览顶上
    const vector = files
      .filter((f) => VECTOR.test(f.path) && /(view|preview|render|ortho|exploded|外观|结构)/i.test(f.path))
      .sort(byRank);
    return [...raster, ...vector].slice(0, 8).map((f) => f.path);
  }, [files]);

  const usable = gallery.filter((p) => !broken.includes(p));
  const shown = hero && usable.includes(hero) ? hero : usable[0] || "";

  useEffect(() => {
    if (usable.length) setHero(usable[0]);
  }, [usable.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  // 换会话重开时清掉「取不到的图」记录，否则上一轮的坏链接会误伤这一轮
  useEffect(() => {
    if (open) setBroken([]);
  }, [open, runId]);

  // ESC 关闭 + 弹窗期间锁背景滚动
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  const stages = manifest?.stages || [];
  const doneStages = stages.filter((s) => (s.files?.length ?? s.n_files ?? 0) > 0);
  const allDone = !manifest?.next;
  const lines = plainLines(summary || "", 3);
  const rawSrc = (p: string) =>
    `/api/agent/raw?${new URLSearchParams({ path: p, ...(runId ? { run_id: runId } : {}) }).toString()}`;
  const heroSrc = shown ? rawSrc(shown) : "";

  return (
    <div
      className="dialog-mask fixed inset-0 z-[60] grid place-items-center p-4"
      style={{ background: "rgba(0,0,0,0.5)", backdropFilter: "blur(6px)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="completion-title"
    >
      <div
        className="dialog-card scroll-thin max-h-[90vh] w-full max-w-[720px] overflow-y-auto rounded-3xl p-5 sm:p-6"
        style={{
          background: "var(--bg-elev)",
          border: "1px solid var(--border)",
          boxShadow: "var(--shadow), 0 24px 70px -18px var(--accent)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部：完成标记 + 标题 + 关闭 */}
        <div className="mb-4 flex items-start gap-3">
          <span
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-[18px]"
            style={{
              background: "linear-gradient(135deg, var(--accent-2), var(--accent))",
              color: "#fff",
              boxShadow: "0 6px 18px -6px var(--accent-2)",
            }}
          >
            ✓
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="completion-title" className="text-[17px] font-semibold">
              {allDone ? "全部设计已交付" : "本阶段已交付"}
            </h2>
            <p className="mt-0.5 truncate text-[12px]" style={{ color: "var(--text-faint)" }}>
              {title || "智能硬件设计流水线"}
            </p>
          </div>
          <button
            onClick={onClose}
            className="magnetic shrink-0 rounded-lg px-2 py-1 text-[12px]"
            style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
            aria-label="关闭"
          >
            关闭
          </button>
        </div>

        <div className="grid gap-4 sm:grid-cols-[minmax(0,300px)_minmax(0,1fr)]">
          {/* 左：产品渲染图 */}
          <div>
            <div
              className="hero-frame relative grid aspect-square w-full place-items-center overflow-hidden rounded-2xl"
              style={{ background: "var(--bg-sunken)", border: "1px solid var(--border)" }}
            >
              {heroSrc ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                  key={shown}
                  src={heroSrc}
                  alt={shown}
                  className="hero-img h-full w-full object-cover"
                  style={isVector(shown) ? { background: "#fff", objectFit: "contain", padding: 10 } : undefined}
                  onError={() => setBroken((b) => (b.includes(shown) ? b : [...b, shown]))}
                />
              ) : (
                <div className="text-center text-[12px]" style={{ color: "var(--text-faint)" }}>
                  {files.length ? (
                    <p>本次没有可用于预览的渲染图<br />可在右侧「设计产物」里查看文档与几何文件</p>
                  ) : (
                    <p className="pulse-dot">正在整理产物…</p>
                  )}
                </div>
              )}
              {shown && (
                <span
                  className="absolute bottom-2 left-2 rounded-md px-1.5 py-0.5 font-mono text-[10px]"
                  style={{ background: "rgba(0,0,0,0.55)", color: "#fff" }}
                >
                  {shown.split("/").pop()}
                </span>
              )}
            </div>

            {usable.length > 1 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {usable.map((p) => (
                  <button
                    key={p}
                    onClick={() => setHero(p)}
                    title={p}
                    className="magnetic h-11 w-11 overflow-hidden rounded-lg"
                    style={{
                      border: p === shown ? "2px solid var(--accent)" : "1px solid var(--border)",
                      background: "var(--bg-sunken)",
                    }}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={rawSrc(p)}
                      alt={p}
                      className="h-full w-full object-cover"
                      onError={() => setBroken((b) => (b.includes(p) ? b : [...b, p]))}
                    />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 右：阶段进度 + 统计 + 摘要 */}
          <div className="flex flex-col">
            {stages.length > 0 && (
              <div className="mb-3">
                <p className="mb-1.5 text-[11px]" style={{ color: "var(--text-faint)" }}>
                  阶段进度 {doneStages.length}/{stages.length}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {stages.map((s) => {
                    const ok = (s.files?.length ?? s.n_files ?? 0) > 0;
                    return (
                      <span
                        key={s.id}
                        className="rounded-md px-1.5 py-0.5 text-[11px]"
                        style={{
                          background: ok ? "var(--accent-2-soft)" : "var(--bg-sunken)",
                          color: ok ? "var(--accent-2)" : "var(--text-faint)",
                        }}
                        title={s.plan_title || s.label}
                      >
                        {ok ? "✓ " : "○ "}
                        {s.label}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="mb-3 grid grid-cols-3 gap-2">
              {[
                { k: "产物文件", v: String(files.length || "—") },
                { k: "阶段完成", v: stages.length ? `${doneStages.length}/${stages.length}` : "—" },
                { k: "本轮用时", v: elapsedText || "—" },
              ].map((x) => (
                <div
                  key={x.k}
                  className="rounded-xl px-2.5 py-2"
                  style={{ background: "var(--bg-sunken)", border: "1px solid var(--border)" }}
                >
                  <p className="text-[10px]" style={{ color: "var(--text-faint)" }}>
                    {x.k}
                  </p>
                  <p className="mt-0.5 text-[14px] font-semibold">{x.v}</p>
                </div>
              ))}
            </div>

            {lines.length > 0 && (
              <div
                className="scroll-thin flex-1 overflow-y-auto rounded-xl p-3 text-[12px] leading-relaxed"
                style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
              >
                {lines.map((l, i) => (
                  <p key={i} className={i ? "mt-1" : ""}>
                    {l}
                  </p>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 底部操作 */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            onClick={() => {
              if (shown) onOpenFile(shown);
              onClose();
            }}
            disabled={!shown}
            className="magnetic btn-primary rounded-xl px-4 py-2 text-[13px] text-white disabled:opacity-45"
            style={{ background: "var(--accent)" }}
          >
            查看设计产物
          </button>
          <a
            href={downloadHref}
            className="magnetic rounded-xl px-4 py-2 text-[13px]"
            style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
          >
            下载全部
          </a>
          <button
            onClick={onClose}
            className="magnetic ml-auto rounded-xl px-4 py-2 text-[13px]"
            style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
          >
            知道了
          </button>
        </div>
      </div>
    </div>
  );
}
