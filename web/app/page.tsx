"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import ClarifyForm, { type Question, type Section } from "@/components/ClarifyForm";
import CompletionDialog from "@/components/CompletionDialog";
import HistoryDrawer from "@/components/HistoryDrawer";
import MiniMarkdown from "@/components/MiniMarkdown";
import SettingsDialog from "@/components/SettingsDialog";
import ThemeToggle from "@/components/ThemeToggle";
import ThinkingBlock from "@/components/ThinkingBlock";
import { agentLabel, agentTint, buildFeed, coreOnly, type AgentEvent } from "@/lib/feed";
import { derivePlan, stageProgress, stagesDone } from "@/lib/plan";
import { PARADIGM_OPTIONS, STALE_MS, type ParadigmInfo } from "@/lib/paradigms";
import {
  DEFAULT_SETTINGS,
  loadSettings,
  saveSettings,
  toPayload,
  withServerDefault,
  type ModelSettings,
  type ServerDefault,
} from "@/lib/settings";

type Todo = { content: string; status: string };
type FileItem = { path: string; size: number; mtime: number };
/** 交付清单：阶段分组的标题取自规划清单，产物逐个列出（单文件/按阶段打包/全量打包） */
type ManifestFile = { path: string; name: string; title: string; size: number };
type ManifestStage = {
  id: string;
  label: string;
  plan_title: string;
  status: string;
  n_files: number;
  size: number;
  files: ManifestFile[];
};
type ManifestData = {
  chain: string[];
  stages: ManifestStage[];
  next: string;
  total_files: number;
  total_size: number;
};
type ClarifyPlan = {
  product_name: string;
  readiness: number;
  missing: string[];
  questions: Question[];
  assumptions: string[];
  source?: string;
  framework?: string;
  // 问卷框架（分区）由后端骨架下发，跨产品一致；换产品只换选项，不换结构
  sections?: Section[];
};
type PendingReview = {
  run_id?: string;
  stage: string;
  summary?: string;
  files?: string[];
  rounds?: number;
  waited?: number;
  wait_seconds?: number;
};

const STAGE_LABEL: Record<string, string> = {
  "00_prd": "需求定义",
  "10_appearance": "外观设计",
  "20_structure": "结构设计",
  "30_hardware": "硬件设计",
  "40_firmware": "固件与仿真",
  "90_review": "交叉评审",
};

function fmtDur(sec: number): string {
  const s = Math.round(sec || 0);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 时 ${m % 60} 分`;
}

const EXAMPLES = [
  "设计一个桌面式温湿度监测小夜灯，带柔和呼吸灯和 OLED 显示",
  "做一个便携式土壤湿度检测仪，USB 充电，适合阳台盆栽",
  "设计一款儿童防近视坐姿提醒器，佩戴在衣领上",
];

export default function Home() {
  const [req, setReq] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [manifest, setManifest] = useState<ManifestData | null>(null);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [health, setHealth] = useState<{
    ok: boolean;
    mode?: string;
    model?: string;
    default?: ServerDefault;
    cad?: { ok: boolean; info?: string };
    /** 出图链路（模型 + 端点 + 来源），来源 recent-run 表示取自前端设置 */
    image?: { host?: string; model?: string; key?: string; source?: string; env_model?: string };
    store?: { runs?: number; events?: number; artifacts?: number; avg_elapsed?: number };
  } | null>(null);
  const esRef = useRef<EventSource | null>(null);
  // 超时自动续跑：后端在 done 之前推一条含「新会话 <id>」的提示，
  // 前端记住它，done 时自动切过去继续看 —— 否则用户只会看到「超时中止」，
  // 以为这轮白跑了，其实后半段正在另一个会话里继续。
  const loadRunRef = useRef<((id: string) => Promise<void>) | null>(null);
  const pendingResumeRef = useRef<string>("");

  // 运行计时（用户最关心的「还要多久」，给个实时数）+ 历史会话面板
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);

  // 模型设置 + 需求补全
  const [settings, setSettings] = useState<ModelSettings>(DEFAULT_SETTINGS);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [plan, setPlan] = useState<ClarifyPlan | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [clarifyBusy, setClarifyBusy] = useState(false);
  const [clarifyOpen, setClarifyOpen] = useState(true);
  // 被「记忆」字段沿用过来的条数，用于提示「已带入 N 项上次填写」
  const [stickyApplied, setStickyApplied] = useState(0);
  // 执行过程视图：core = 只显示核心步骤，all = 原始事件流
  const [feedMode, setFeedMode] = useState<"core" | "all">("core");
  // 决策范式：服务端 Orchestrator 的路由结果（空 = 自动路由）
  const [paradigm, setParadigm] = useState<ParadigmInfo | null>(null);
  const [paradigmChoice, setParadigmChoice] = useState<string>("");
  // 人工确认闸门：外观 / 结构做完会挂起等用户表态，这两步是整条链的基线，
  // 用户没看过就往下走，一改就是全链返工。
  const [pendingReview, setPendingReview] = useState<PendingReview | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);
  // 假死检测：心跳与事件都会刷新这个时间戳，超时说明后端卡住了而不是「还在思考」
  const [stale, setStale] = useState(false);
  const lastEvRef = useRef<number>(Date.now());
  // 交付完成弹窗：只在真正收到 done 且不需要续跑时弹一次（中途「超时续跑」不算收工）
  const [celebrate, setCelebrate] = useState(false);
  const celebratedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    setSettings(loadSettings());
    fetch("/api/agent/health")
      .then((r) => r.json())
      .then((h) => {
        setHealth(h);
        // 本地没保存过设置时，对齐服务端 .env 里预置的 provider / 模型
        if (h?.default?.base_url) {
          setSettings((s) => {
            const next = withServerDefault(s, h.default);
            if (next !== s) saveSettings(next);
            return next;
          });
        }
      })
      .catch(() => setHealth({ ok: false }));
  }, []);

  const loadFiles = useCallback((id: string) => {
    fetch(`/api/agent/files?run_id=${id}`)
      .then((r) => r.json())
      .then((d) => setFiles(d.files || []))
      .catch(() => {});
    // 交付清单：分组标题取自规划清单，同时给出按阶段打包所需的信息
    fetch(`/api/agent/manifest?run_id=${id}`)
      .then((r) => r.json())
      .then((d) => setManifest(d?.stages ? (d as ManifestData) : null))
      .catch(() => {});
  }, []);

  // 事件流订阅（新建会话 / 重新接上历史会话，共用一套处理）
  const followStream = useCallback(
    (id: string) => {
      const es = new EventSource(`/api/agent/stream?run_id=${id}`);
      esRef.current = es;
      es.onmessage = (e) => {
        const ev: AgentEvent = JSON.parse(e.data);
        if (ev.type === "hello") return;
        // 范式公告与心跳只是「元信息」，不进事件流（否则会变成一堆没有内容的卡片）
        if (ev.type === "paradigm") {
          setParadigm(ev as unknown as ParadigmInfo);
          lastEvRef.current = Date.now();
          return;
        }
        lastEvRef.current = Date.now();
        setStale(false);
        if (ev.type === "heartbeat") return;
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "todo" && ev.items) setTodos(ev.items);
        // 等人确认：把面板弹出来；用户看到的是「已暂停，等你表态」而不是「还在转圈」
        if (ev.type === "awaiting") {
          setPendingReview(ev as unknown as PendingReview);
          setReviewNote("");
          loadFiles(id);
        }
        if (ev.type === "reviewed") setPendingReview(null);
        if (ev.type === "warn") {
          const msg = (ev as unknown as { message?: string }).message || "";
          const m = msg.match(/新会话\s*([0-9a-fA-F]{6,})/);
          if (m) pendingResumeRef.current = m[1];
        }
        if (ev.type === "done") {
          setStatus("done");
          setPendingReview(null);
          loadFiles(id);
          setHistoryKey((k) => k + 1);
          es.close();
          const nid = pendingResumeRef.current;
          if (nid && nid !== id) {
            // 超时续跑：还有后半段在另一个会话里跑，此刻不该报「收工」
            pendingResumeRef.current = "";
            void loadRunRef.current?.(nid);
          } else {
            celebratedRef.current.add(id);
            setCelebrate(true);
          }
        }
        if (ev.type === "error") {
          setStatus("error");
          setPendingReview(null);
          setHistoryKey((k) => k + 1);
          es.close();
        }
        if (ev.type === "closed") {
          setStatus((s) => (s === "running" ? "done" : s));
          setPendingReview(null);
          setHistoryKey((k) => k + 1);
          es.close();
        }
      };
      es.onerror = () => {
        es.close();
        setStatus((s) => (s === "running" ? "done" : s));
      };
      return es;
    },
    [loadFiles],
  );

  useEffect(() => {
    if (!runId || status !== "running") return;
    const t = setInterval(() => loadFiles(runId), 3000);
    return () => clearInterval(t);
  }, [runId, status, loadFiles]);

  // 计时：运行中每秒刷新，结束后停在最终值
  useEffect(() => {
    if (status !== "running" || startedAt == null) return;
    const t = setInterval(() => setElapsed(Math.max(0, Date.now() / 1000 - startedAt)), 1000);
    setElapsed(Math.max(0, Date.now() / 1000 - startedAt));
    return () => clearInterval(t);
  }, [status, startedAt]);

  // 假死检测：连心跳都收不到 = 后端卡住了。这条提示能把「还在跑」和「已经死了」区分开，
  // 否则用户只能盯着一个永远转圈的「运行中」。
  useEffect(() => {
    if (status !== "running") {
      setStale(false);
      return;
    }
    lastEvRef.current = Date.now();
    const t = setInterval(() => setStale(Date.now() - lastEvRef.current > STALE_MS), 1000);
    return () => clearInterval(t);
  }, [status]);

  // 开新跑 / 出错时收掉完成弹窗，避免它压在下一轮的进度上
  useEffect(() => {
    if (status === "running" || status === "error") setCelebrate(false);
  }, [status]);

  const start = useCallback(
    async (override?: string) => {
      const text = (override ?? req).trim();
      if (!text || status === "running") return;
      esRef.current?.close();
      setEvents([]);
      setTodos([]);
      setFiles([]);
      setManifest(null);
      setContent("");
      setActiveFile(null);
      setStatus("running");
      setStartedAt(Date.now() / 1000);
      setElapsed(0);
      setStale(false);
      setParadigm(null);
      setPendingReview(null);
      setReviewNote("");
      lastEvRef.current = Date.now();
      try {
        const r = await fetch("/api/agent/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            requirement: text,
            settings: toPayload(settings),
            paradigm: paradigmChoice,
          }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || "启动失败");
        if (d.paradigm) setParadigm(d.paradigm);
        setRunId(d.run_id);
        followStream(d.run_id);
      } catch (e) {
        setEvents((p) => [...p, { type: "error", message: (e as Error).message }]);
        setStatus("error");
      }
    },
    [req, followStream, status, settings, paradigmChoice],
  );

  // 停止生成：护栏会在下一个事件到达时打断整条流，已落盘的产物保留
  const stopRun = useCallback(async () => {
    if (!runId) return;
    try {
      const r = await fetch(`/api/agent/runs?run_id=${encodeURIComponent(runId)}&action=cancel`, {
        method: "POST",
      });
      const d = await r.json();
      setEvents((p) => [...p, { type: "warn", message: d.note || d.error || "已请求停止" }]);
    } catch (e) {
      setEvents((p) => [...p, { type: "warn", message: `停止请求失败：${(e as Error).message}` }]);
    }
  }, [runId]);

  // 人工确认：approve 放行下游；revise 把用户原话原样发回去，智能体会带着它重做本阶段
  const submitReview = useCallback(
    async (decision: "approve" | "revise") => {
      if (!runId || !pendingReview || reviewBusy) return;
      const note = reviewNote.trim();
      if (decision === "revise" && !note) {
        setEvents((p) => [
          ...p,
          { type: "warn", message: "请先写清楚要改什么（例如「换成暖白光」「高度压到 40mm」）" },
        ]);
        return;
      }
      setReviewBusy(true);
      try {
        const r = await fetch(`/api/agent/review?run_id=${encodeURIComponent(runId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, feedback: note, stage: pendingReview.stage }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || d.error || "提交失败");
        setEvents((p) => [
          ...p,
          {
            type: "warn",
            message:
              decision === "approve"
                ? `已确认「${STAGE_LABEL[pendingReview.stage] || pendingReview.stage}」，智能体继续下一步${note ? `（附言：${note}）` : ""}`
                : `已提交修改意见：${note} —— 智能体将按此重做本阶段，改完会再请你确认`,
          },
        ]);
        setPendingReview(null);
        setReviewNote("");
      } catch (e) {
        setEvents((p) => [...p, { type: "warn", message: `提交失败：${(e as Error).message}` }]);
      } finally {
        setReviewBusy(false);
      }
    },
    [runId, pendingReview, reviewNote, reviewBusy],
  );

  // 续跑：产物只做了一半时（例如外观图已出、后面还没做），从下一个未完成阶段接着跑。
  // 智能硬件各阶段是串行依赖链，重跑一遍既费时又会让已确认的外观图重新生成。
  const resumeRun = useCallback(
    async (fromStage?: string) => {
      if (!runId || status === "running" || resumeBusy) return;
      setResumeBusy(true);
      try {
        const r = await fetch(`/api/agent/resume?run_id=${encodeURIComponent(runId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ from_stage: fromStage || "", settings: toPayload(settings) }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || d.error || "续跑失败");
        esRef.current?.close();
        setEvents([]);
        setTodos([]);
        setFiles([]);
        setManifest(null);
        setContent("");
        setActiveFile(null);
        setStatus("running");
        setStartedAt(Date.now() / 1000);
        setElapsed(0);
        setStale(false);
        setPendingReview(null);
        setReviewNote("");
        if (d.paradigm) setParadigm(d.paradigm);
        setRunId(d.run_id);
        followStream(d.run_id);
        setHistoryKey((k) => k + 1);
      } catch (e) {
        setEvents((p) => [...p, { type: "warn", message: `续跑失败：${(e as Error).message}` }]);
      } finally {
        setResumeBusy(false);
      }
    },
    [runId, status, resumeBusy, settings, followStream],
  );

  // 打开历史会话：跑完的回放事件流；还在跑的接上 SSE 继续跟随
  const loadRun = useCallback(
    async (id: string) => {
      esRef.current?.close();
      esRef.current = null;
      setRunId(id);
      setEvents([]);
      setTodos([]);
      setFiles([]);
      setContent("");
      setActiveFile(null);
      try {
        const r = await fetch(`/api/agent/runs?run_id=${encodeURIComponent(id)}`, { cache: "no-store" });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || "会话不存在");
        setReq(d.requirement || "");
        const created = Number(d.created_at) || Date.now() / 1000;
        setStartedAt(created);
        setElapsed(d.status === "running" ? Math.max(0, Date.now() / 1000 - created) : Number(d.elapsed) || 0);
        loadFiles(id);
        // 恢复「正在等人」的面板：刷新或深链进来时，光看事件流回放不出可交互的按钮
        setPendingReview(d.awaiting || null);
        setReviewNote("");
        if (d.status === "running") {
          setStatus("running");
          followStream(id); // worker 的 SSE 会从第 0 条重放，不用先拉历史
          return;
        }
        const er = await fetch(
          `/api/agent/runs?run_id=${encodeURIComponent(id)}&events=1`,
          { cache: "no-store" },
        );
        const ed = await er.json();
        const evs: AgentEvent[] = ed.events || [];
        setEvents(evs);
        // 回放旧会话时把模型写的清单恢复出来（没有就由 derivePlan 按阶段兜底造一份）
        const lastTodo = [...evs].reverse().find((e) => e.type === "todo" && e.items);
        if (lastTodo?.items) setTodos(lastTodo.items);
        setStatus(d.status === "error" ? "error" : "done");
        // 刷新 / 深链进来时补一次交付提示：只在本轮刚结束（10 分钟内）且没提示过才弹，
        // 翻旧会话不会被反复打扰
        const finishedAt = created + (Number(d.elapsed) || 0);
        if (!celebratedRef.current.has(id) && Date.now() / 1000 - finishedAt < 600) {
          celebratedRef.current.add(id);
          setCelebrate(true);
        }
      } catch (e) {
        setEvents([{ type: "error", message: (e as Error).message }]);
        setStatus("error");
      }
    },
    [followStream, loadFiles],
  );

  // 让事件流里的「自动续跑」提示能切到新会话（followStream 定义在 loadRun 之前，
  // 用 ref 转发，避免改掉两个 useCallback 的依赖顺序）
  useEffect(() => {
    loadRunRef.current = loadRun;
  }, [loadRun]);

  // ?run=<id> 深链：直接打开某次会话（可分享，也方便回来接着看）
  const bootRunId = useRef<string | null>(null);
  if (bootRunId.current === null) {
    bootRunId.current =
      typeof window === "undefined" ? "" : (new URLSearchParams(window.location.search).get("run") || "");
  }
  useEffect(() => {
    const id = bootRunId.current;
    if (id) void loadRun(id);
  }, [loadRun]);

  // 「记忆」字段跨项目复用：稳定偏好（类目/供电/主控/风格/成本/交付深度）存本机，
  // 下次打开问卷自动带入，只改本次不同的部分。随产品变的（名称/场景/功能）不存。
  const STICKY_KEY = "pd.clarify.sticky";

  const loadSticky = (): Record<string, string> => {
    try {
      const v = JSON.parse(localStorage.getItem(STICKY_KEY) || "{}");
      return v && typeof v === "object" ? v : {};
    } catch {
      return {};
    }
  };

  const saveSticky = (qs: Question[], ans: Record<string, string>) => {
    try {
      const next = { ...loadSticky() };
      for (const q of qs) {
        if (!q.sticky) continue;
        const v = String(ans[q.key] ?? "").trim();
        if (v) next[q.key] = v;
      }
      localStorage.setItem(STICKY_KEY, JSON.stringify(next));
    } catch {
      // 隐私模式下 localStorage 不可用，不记住也不影响使用
    }
  };

  const clearSticky = () => {
    try {
      localStorage.removeItem(STICKY_KEY);
    } catch {
      /* 同上 */
    }
    setStickyApplied(0);
    if (plan) {
      const init: Record<string, string> = {};
      for (const q of plan.questions) init[q.key] = q.default || "";
      setAnswers(init);
    }
  };

  // 需求补全：默认直接用固定模板（不再等一轮模型出题）
  const runClarify = async (override?: string) => {
    const text = (override ?? req).trim();
    if (!text || clarifyBusy) return;
    setClarifyBusy(true);
    try {
      const r = await fetch("/api/agent/clarify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirement: text, settings: toPayload(settings) }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "补全失败");
      setPlan(d as ClarifyPlan);
      const mem = loadSticky();
      const init: Record<string, string> = {};
      let applied = 0;
      for (const q of (d.questions || []) as Question[]) {
        const remembered = q.sticky ? String(mem[q.key] || "") : "";
        if (remembered) applied += 1;
        init[q.key] = remembered || q.default || "";
      }
      setAnswers(init);
      setStickyApplied(applied);
      setClarifyOpen(true);
    } catch (e) {
      setEvents((p) => [...p, { type: "error", message: `需求补全失败：${(e as Error).message}` }]);
    } finally {
      setClarifyBusy(false);
    }
  };

  // 把答案拼成结构化简报，作为智能体的正式输入
  const buildBrief = () => {
    const head = plan?.product_name || "未命名产品";
    const lines = [`# 产品需求简报：${head}`, "", "## 原始需求", req.trim(), ""];
    const filled = Object.entries(answers).filter(([, v]) => String(v).trim());
    if (filled.length) {
      lines.push("## 已确认信息");
      for (const [k, v] of filled) {
        const label = plan?.questions?.find((q) => q.key === k)?.label || k;
        lines.push(`- **${label}**：${v}`);
      }
      lines.push("");
    }
    if (plan?.assumptions?.length) {
      lines.push("## 未填写项的默认假设");
      for (const a of plan.assumptions) lines.push(`- ${a}`);
      lines.push("");
    }
    lines.push(
      "## 交付要求",
      "按以上确认信息完成产品定义、外观、结构、硬件与固件仿真，全部产物落盘并在最后汇总。",
    );
    return lines.join("\n");
  };

  const missingRequired = useMemo(
    () =>
      (plan?.questions || [])
        .filter((q) => q.required && !String(answers[q.key] || "").trim())
        .map((q) => q.label),
    [plan, answers],
  );

  // 主按钮：默认先让智能体提问补齐信息；已生成过清单则按简报开工
  const handleStart = () => {
    if (!plan) {
      void runClarify();
      setClarifyOpen(true);
      return;
    }
    void start(buildBrief());
    setClarifyOpen(false);
  };

  // 明确跳过：按用户当前描述 + 智能体默认假设直接开工
  const handleSkip = () => {
    void start();
    setClarifyOpen(false);
  };

  // 支持 ?q=需求 直接进入流程（便于分享链接、也便于无人值守截图验证）
  // 默认与「开始设计」一致：先提问补全；&go=1 跳过提问直接开工；&settings=1 打开模型设置
  const autoStarted = useRef(false);
  useEffect(() => {
    if (autoStarted.current) return;
    const sp = new URLSearchParams(window.location.search);
    if (sp.get("settings") === "1") setSettingsOpen(true);
    const q = sp.get("q");
    if (!q) return;
    autoStarted.current = true;
    setReq(q);
    if (sp.get("go") === "1") void start(q);
    else void runClarify(q);
  }, [start]);

  const openFile = (path: string) => {
    if (!runId) return;
    setActiveFile(path);
    fetch(`/api/agent/file?run_id=${runId}&path=${encodeURIComponent(path)}`)
      .then((r) => r.json())
      .then((d) => setContent(d.content || ""))
      .catch(() => setContent(""));
  };

  const feed = useMemo(() => buildFeed(events), [events]);

  // 核心视图折叠掉规划/文件读写这类记账动作，只留真正推进设计的关键步骤
  const shown = useMemo(() => (feedMode === "core" ? coreOnly(feed) : feed), [feed, feedMode]);
  const noiseCount = feed.length - coreOnly(feed).length;

  const thinkingCount = useMemo(() => feed.filter((f) => f.kind === "thinking").length, [feed]);

  // 事件流自动跟随最新，用户上翻时暂停跟随
  const feedRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const onFeedScroll = () => {
    const el = feedRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  useEffect(() => {
    const el = feedRef.current;
    if (!el || !stick.current) return;
    el.scrollTop = el.scrollHeight;
  }, [feed]);

  const summary = events.find((e) => e.type === "done")?.summary || "";

  // 任务规划：以磁盘产物为准反推阶段状态。模型往往只在开头写一次清单就不再回写，
  // 光信 write_todos 会出现「产物都齐了、规划还停在第一格」。
  const filePaths = useMemo(() => files.map((f) => f.path), [files]);
  const planItems = useMemo(() => derivePlan(todos, filePaths), [todos, filePaths]);
  const progress = useMemo(() => stageProgress(filePaths), [filePaths]);
  const doneMap = useMemo(() => stagesDone(filePaths), [filePaths]);
  const errMsg = events.find((e) => e.type === "error")?.message || "";

  // 剩余时间估算：拿历史会话的平均总时长按「未完成阶段占比」折算。
  // 前期没有样本时 avg_elapsed=0，就只显示已用时间，不硬编一个假的进度。
  const avgElapsed = Number(health?.store?.avg_elapsed || 0);
  // 剩余时间 = 历史平均总时长 − 已用时间，clamp 到 0（绝不显示负数）。
  // 没有历史样本就只显示已用时间，不硬编一个假的倒计时。
  const etaLeft = status === "running" && avgElapsed > elapsed ? Math.round(avgElapsed - elapsed) : 0;
  const overAvg = status === "running" && avgElapsed > 0 && elapsed > avgElapsed;
  // 后端判「六阶段产物齐全」后进入收尾宽限期 —— 这时别再报预计剩余，会自相矛盾
  const goalHit = events.some((e) => e.type === "goal_reached");

  const grouped = useMemo(() => {
    const g: Record<string, FileItem[]> = {};
    for (const f of files) {
      const stage = f.path.split("/")[0];
      (g[stage] ||= []).push(f);
    }
    return g;
  }, [files]);

  // 下载链接：单文件用 path，阶段包用 stage，全量用 scope=all
  const dl = (params: Record<string, string>) =>
    `/api/agent/download?${new URLSearchParams({ run_id: runId ?? "", ...params }).toString()}`;

  const isSvg = activeFile?.endsWith(".svg");
  const isMd = activeFile?.endsWith(".md");
  const isImg = !!activeFile && /\.(png|jpe?g|gif|webp|bmp|ico)$/i.test(activeFile);
  const isBinary = !!activeFile && /\.(step|stp|stl|3mf|zip)$/i.test(activeFile);
  // Markdown 里的图片常写成相对当前文档的路径，按所在目录兜底查找
  const docBase = activeFile ? activeFile.slice(0, Math.max(0, activeFile.lastIndexOf("/"))) : "";

  return (
    <div className="min-h-screen">
      <header className="top-bar sticky top-0 z-20 border-b">
        <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-5 py-3">
          <div className="flex items-center gap-3">
            <div className="logo-badge grid h-9 w-9 place-items-center rounded-xl text-sm font-semibold">
              PD
            </div>
            <div>
              <h1 className="title-glow text-[15px] font-semibold leading-tight tracking-tight">
                智能产品设计智能体
              </h1>
              <p className="text-xs" style={{ color: "var(--text-faint)" }}>
                需求 → 外观 · 结构 · 硬件 · 仿真，一条流水线出全套方案
              </p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <Link
              href="/home"
              className="rounded-lg border px-2.5 py-1.5 text-xs transition-colors hover:bg-[var(--bg-sunken)]"
              style={{ borderColor: "var(--border)", color: "var(--text-dim)" }}
              title="查看产品设计全流程、商业化方案与全过程演示"
            >
              产品主页
            </Link>
            <span
              className="rounded-full px-2.5 py-1 text-xs"
              title={health?.ok ? `worker 默认模型：${health.model}` : "worker 未启动"}
              style={{
                background: !health?.ok
                  ? "var(--danger-soft)"
                  : settings.apiKey
                    ? "var(--accent-2-soft)"
                    : "var(--warn-soft)",
                color: !health?.ok
                  ? "var(--danger)"
                  : settings.apiKey
                    ? "var(--accent-2)"
                    : "var(--warn)",
              }}
            >
              {!health?.ok
                ? "worker 离线"
                : health.mode === "demo"
                  ? "未配置密钥 · 演示模式"
                  : settings.apiKey
                    ? "已配置密钥 · 实时生成"
                    : "服务端密钥 · 实时生成"}
            </span>
            <span
              className="rounded-full px-2.5 py-1 text-xs"
              title={health?.cad?.info || "结构建模内核（build123d / OpenCASCADE）未就绪"}
              style={{
                background: health?.cad?.ok ? "var(--accent-soft)" : "var(--bg-sunken)",
                color: health?.cad?.ok ? "var(--accent)" : "var(--text-faint)",
              }}
            >
              {health?.cad?.ok ? "结构建模 · 就绪" : "结构建模 · 未就绪"}
            </span>
            {/* 出图链路徽章：设置里填的模型优先（它就是本次会用的），其次才看 worker 侧。
                以前界面上完全看不到出图用的哪个模型，用户改了设置也无从确认生效没有。 */}
            <span
              className="rounded-full px-2.5 py-1 text-xs"
              title={`出图网关 ${health?.image?.host || "agnes"} · ${
                settings.imageModel.trim()
                  ? "取自「模型设置 → 图像」"
                  : health?.image?.source === "recent-run"
                    ? "取自最近一次会话的设置"
                    : "服务端 .env 默认"
              }（密钥 ${health?.image?.key || "未配置"}）`}
              style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
            >
              出图 · {settings.imageModel.trim() || health?.image?.model || "未配置"}
            </span>
            {pendingReview && (
              <span
                className="rounded-full px-2.5 py-1 text-xs"
                title="外观 / 结构做完会暂停等你确认，确认后才会进入下一阶段"
                style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
              >
                ⏸ 待你确认：{STAGE_LABEL[pendingReview.stage] || pendingReview.stage}
              </span>
            )}
            <button
              onClick={() => setHistoryOpen(true)}
              className="magnetic rounded-full px-2.5 py-1 text-xs"
              style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
              title="查看历史会话：所有需求、事件流与产物都存在 SQLite 里"
            >
              历史会话
            </button>
            <button
              onClick={() => setSettingsOpen(true)}
              className="magnetic rounded-full px-2.5 py-1 text-xs"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
              title="配置大模型服务商、API Key 与模型"
            >
              模型：{settings.model || "未设置"}
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1600px] gap-4 px-5 py-5 lg:grid-cols-[330px_minmax(0,1fr)_400px]">
        {/* 左：需求 + 看板 */}
        <section className="flex flex-col gap-4">
          <div className="glass magnetic rounded-2xl p-4">
            <h2 className="mb-2 text-sm font-semibold">产品需求</h2>
            <textarea
              value={req}
              onChange={(e) => setReq(e.target.value)}
              rows={5}
              placeholder="一句话描述你要做的产品，例如：设计一个桌面式温湿度监测小夜灯…"
              className="w-full resize-none rounded-xl p-3 text-sm outline-none transition-all"
              style={{
                background: "var(--bg-sunken)",
                border: "1px solid var(--border)",
                color: "var(--text)",
              }}
            />
            {/* 决策范式选择：默认「自动」，由服务端按任务 DNA 路由 */}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
                决策范式
              </span>
              {PARADIGM_OPTIONS.map((o) => (
                <button
                  key={o.key}
                  onClick={() => setParadigmChoice(o.key)}
                  disabled={status === "running"}
                  className="rounded-full px-2 py-0.5 text-[11px] transition-all"
                  title={o.hint}
                  style={{
                    background: paradigmChoice === o.key ? "var(--accent-soft)" : "var(--bg-sunken)",
                    color: paradigmChoice === o.key ? "var(--accent)" : "var(--text-dim)",
                    border: "1px solid var(--border)",
                    cursor: status === "running" ? "not-allowed" : "pointer",
                  }}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => void runClarify()}
                disabled={status === "running" || !req.trim() || clarifyBusy}
                className="magnetic rounded-xl px-3 py-2.5 text-[13px]"
                style={{
                  background: "var(--bg-sunken)",
                  color: "var(--text-dim)",
                  cursor: req.trim() && !clarifyBusy ? "pointer" : "not-allowed",
                }}
                title="让智能体判断还缺哪些信息，生成待确认清单"
              >
                {clarifyBusy ? "载入清单…" : "补全需求"}
              </button>
              {status === "running" ? (
                <button
                  onClick={() => void stopRun()}
                  className="magnetic flex-1 rounded-xl px-4 py-2.5 text-sm font-medium"
                  style={{
                    background: stale ? "var(--danger)" : "var(--danger-soft)",
                    color: stale ? "#fff" : "var(--danger)",
                    border: "1px solid var(--border)",
                    cursor: "pointer",
                  }}
                  title={
                    stale
                      ? "后端长时间无响应，点这里强制中断；已落盘的产物会保留"
                      : "中断本次生成。已落盘的产物会保留，历史会话里能看到中止原因"
                  }
                >
                  ■ {stale ? "卡住了，强制停止" : "停止生成"}
                </button>
              ) : (
                <button
                  onClick={handleStart}
                  disabled={!req.trim() || clarifyBusy}
                  className="magnetic btn-primary flex-1 rounded-xl px-4 py-2.5 text-sm font-medium"
                  style={{
                    background: "var(--accent)",
                    color: "#fff",
                    cursor: req.trim() && !clarifyBusy ? "pointer" : "not-allowed",
                  }}
                >
                  {clarifyBusy ? "正在生成待确认清单…" : plan ? "用补全信息开始设计" : "开始设计（先提问补全）"}
                </button>
              )}
            </div>
            {status !== "running" && (
              <button
                onClick={handleSkip}
                disabled={!req.trim()}
                className="mt-2 w-full text-[11px]"
                style={{
                  background: "transparent",
                  color: "var(--text-faint)",
                  cursor: req.trim() ? "pointer" : "not-allowed",
                }}
                title="不补充信息，直接让智能体按当前描述和默认假设推进"
              >
                跳过提问，直接按现有描述开始 →
              </button>
            )}
            <div className="mt-3 flex flex-wrap gap-1.5">
              {EXAMPLES.map((x) => (
                <button
                  key={x}
                  onClick={() => setReq(x)}
                  className="magnetic rounded-lg px-2 py-1 text-left text-[11px] leading-snug"
                  style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
                >
                  {x.slice(0, 18)}…
                </button>
              ))}
            </div>
          </div>

          {plan && (
            <div className="glass magnetic enter rounded-2xl p-4">
              <button
                onClick={() => setClarifyOpen((v) => !v)}
                className="flex w-full items-center gap-2 text-left"
              >
                <h2 className="text-sm font-semibold">待补全信息</h2>
                <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
                  {plan.questions.length} 项 · {plan.sections?.length || 0} 个分区 ·{" "}
                  {plan.source === "skeleton+model" ? "充分度" : "需求文本信息量"} {plan.readiness}%
                </span>
                <span className="ml-auto text-[11px]" style={{ color: "var(--text-faint)" }}>
                  {clarifyOpen ? "收起 ▲" : "展开 ▼"}
                </span>
              </button>

              {plan.missing?.length > 0 && (
                <p className="mt-1.5 text-[11px] leading-snug" style={{ color: "var(--warn)" }}>
                  {plan.source === "model" ? "智能体判断缺失" : "必填项"}：{plan.missing.join("、")}
                </p>
              )}
              {plan.source === "skeleton+model" ? (
                <p className="mt-1 text-[11px]" style={{ color: "var(--text-faint)" }}>
                  框架固定，选项由模型按本次需求生成
                </p>
              ) : (
                <p className="mt-1 text-[11px]" style={{ color: "var(--text-faint)" }}>
                  框架固定（跨产品一致），选项按本次需求关键词匹配
                </p>
              )}

              {clarifyOpen && (
                <div className="mt-3">
                  {stickyApplied > 0 && (
                    <p
                      className="mb-2 flex items-center gap-2 text-[11px]"
                      style={{ color: "var(--text-faint)" }}
                    >
                      <span>已带入 {stickyApplied} 项上次填写的稳定偏好</span>
                      <button
                        onClick={clearSticky}
                        className="underline"
                        style={{ color: "var(--accent)" }}
                      >
                        清空记忆
                      </button>
                    </p>
                  )}
                  <ClarifyForm
                    questions={plan.questions}
                    sections={plan.sections}
                    answers={answers}
                    onChange={(k, v) => {
                      const next = { ...answers, [k]: v };
                      setAnswers(next);
                      saveSticky(plan.questions, next);
                    }}
                  />
                  {missingRequired.length > 0 && (
                    <p
                      className="mt-3 rounded-lg px-2.5 py-1.5 text-[11px]"
                      style={{ background: "var(--warn-soft)", color: "var(--warn)" }}
                    >
                      还有 {missingRequired.length} 项必填未填：{missingRequired.join("、")}
                      （不填则由智能体按默认假设推进）
                    </p>
                  )}
                  <button
                    onClick={handleStart}
                    disabled={status === "running"}
                    className="magnetic btn-primary mt-3 w-full rounded-xl px-4 py-2.5 text-sm font-medium"
                    style={{
                      background: status === "running" ? "var(--bg-sunken)" : "var(--accent)",
                      color: status === "running" ? "var(--text-faint)" : "#fff",
                      cursor: status === "running" ? "not-allowed" : "pointer",
                    }}
                  >
                    用补全信息开始设计
                  </button>
                </div>
              )}
            </div>
          )}

          {/* 人工确认闸门：外观 / 结构做完会挂起在这里等用户表态 */}
          {pendingReview && (
            <div
              className="glass enter rounded-2xl p-4"
              style={{ border: "1px solid var(--accent)", background: "var(--accent-soft)" }}
            >
              <div className="mb-2 flex items-center gap-2">
                <span className="pulse-dot h-2 w-2 rounded-full" style={{ background: "var(--accent)" }} />
                <h2 className="text-sm font-semibold">
                  待你确认：{STAGE_LABEL[pendingReview.stage] || pendingReview.stage}
                </h2>
                <span className="ml-auto text-[11px]" style={{ color: "var(--text-dim)" }}>
                  {pendingReview.rounds && pendingReview.rounds > 1 ? `第 ${pendingReview.rounds} 轮` : ""}
                  {" 已暂停等待"}
                </span>
              </div>
              <p className="text-xs leading-relaxed" style={{ color: "var(--text)" }}>
                智能体已暂停在这一步；确认后才会进入下一阶段。
                {pendingReview.summary ? `　${pendingReview.summary}` : ""}
              </p>

              {!!pendingReview.files?.length && (
                <div className="mt-2.5 flex flex-wrap gap-2">
                  {pendingReview.files.slice(0, 8).map((f) => {
                    const isImg = /\.(png|jpe?g|webp|svg)$/i.test(f);
                    return (
                      <a
                        key={f}
                        href={`/api/agent/raw?run_id=${runId}&path=${encodeURIComponent(f)}`}
                        target="_blank"
                        rel="noreferrer"
                        className="block overflow-hidden rounded-lg"
                        style={{ border: "1px solid var(--border)", background: "var(--bg-sunken)" }}
                        title={f}
                      >
                        {isImg ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={`/api/agent/raw?run_id=${runId}&path=${encodeURIComponent(f)}`}
                            alt={f}
                            className="h-16 w-16 object-cover"
                          />
                        ) : (
                          <span className="grid h-16 w-16 place-items-center px-1 text-center text-[10px] leading-tight" style={{ color: "var(--text-dim)" }}>
                            {f.split("/").pop()}
                          </span>
                        )}
                      </a>
                    );
                  })}
                </div>
              )}

              <textarea
                value={reviewNote}
                onChange={(e) => setReviewNote(e.target.value)}
                rows={3}
                placeholder="要改什么就写在这里（例如：换成暖白光、高度压到 40mm、加一个 Type-C 口）。直接点确认则按现状继续。"
                className="mt-3 w-full resize-none rounded-xl p-2.5 text-xs outline-none"
                style={{
                  background: "var(--bg-sunken)",
                  border: "1px solid var(--border)",
                  color: "var(--text)",
                }}
              />
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => void submitReview("approve")}
                  disabled={reviewBusy}
                  className="magnetic btn-primary flex-1 rounded-xl px-3 py-2 text-[13px] font-medium disabled:opacity-60"
                >
                  确认，继续下一步
                </button>
                <button
                  onClick={() => void submitReview("revise")}
                  disabled={reviewBusy}
                  className="magnetic flex-1 rounded-xl px-3 py-2 text-[13px] disabled:opacity-60"
                  style={{ border: "1px solid var(--border-strong)", color: "var(--text)" }}
                >
                  按此反馈重做
                </button>
              </div>
              <p className="mt-1.5 text-[11px]" style={{ color: "var(--text-faint)" }}>
                等待期不计入运行时限，不会超时；长时间不回应会自动放行。
              </p>
            </div>
          )}

          <div className="glass magnetic rounded-2xl p-4">
            <div className="mb-3 flex items-center gap-2">
              <h2 className="text-sm font-semibold">任务规划</h2>
              {runId && (
                <span
                  className="rounded-full px-2 py-0.5 text-[11px]"
                  title="阶段进度按磁盘产物自动判定，不依赖模型自觉回写"
                  style={{
                    background: progress.finished ? "var(--accent-2-soft)" : "var(--accent-soft)",
                    color: progress.finished ? "var(--accent-2)" : "var(--accent)",
                  }}
                >
                  已完成 {progress.done}/{progress.total}
                </span>
              )}
              {paradigm && (
                <span
                  className="rounded-full px-2 py-0.5 text-[11px]"
                  title={`${paradigm.philosophy}\n\n适用边界：${paradigm.boundary}\n\n预算：${paradigm.tool_budget} 次工具 / ${Math.round(paradigm.max_seconds)} 秒${paradigm.fallback ? `\n超时降级：→ ${paradigm.fallback}` : ""}`}
                  style={{
                    background: "var(--bg-sunken)",
                    color: "var(--text-dim)",
                    border: "1px solid var(--border)",
                  }}
                >
                  {paradigm.label}
                </span>
              )}
              <span className="ml-auto text-[11px]" style={{ color: "var(--text-faint)" }}>
                {status === "running"
                  ? `已用 ${fmtDur(elapsed)}${
                      stale
                        ? " · ⚠ 20 秒无任何事件，后端可能卡住"
                        : goalHit
                          ? " · 六阶段已齐，收尾中"
                          : etaLeft
                            ? ` · 预计还需 ~${fmtDur(etaLeft)}`
                            : overAvg
                              ? " · 已超历史均值"
                              : ""
                    }`
                  : elapsed
                    ? `用时 ${fmtDur(elapsed)}`
                    : ""}
              </span>
            </div>

            {/* 阶段进度条：产物落到哪个阶段，一眼看得见 */}
            {runId && (
              <div className="mb-3 flex gap-1">
                {["00_prd", "10_appearance", "20_structure", "30_hardware", "40_firmware", "90_review"].map(
                  (s) => {
                    const isDone = doneMap[s];
                    const isCur = !isDone && status === "running" && progress.label === STAGE_LABEL[s];
                    return (
                      <div
                        key={s}
                        title={STAGE_LABEL[s]}
                        className="h-1.5 flex-1 rounded-full transition-all"
                        style={{
                          background: isDone
                            ? "var(--accent-2)"
                            : isCur
                              ? "var(--accent)"
                              : "var(--border)",
                          opacity: isDone || isCur ? 1 : 0.7,
                        }}
                      />
                    );
                  },
                )}
              </div>
            )}

            {planItems.length === 0 ? (
              <p className="text-xs" style={{ color: "var(--text-faint)" }}>
                启动后由主智能体用 write_todos 生成
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {planItems.map((t, i) => {
                  const done = t.status === "completed";
                  const active = t.status === "in_progress";
                  return (
                    <li key={i} className="flex items-start gap-2 text-xs">
                      <span
                        className={`mt-1 h-2 w-2 shrink-0 rounded-full ${active ? "pulse-dot" : ""}`}
                        style={{
                          background: done
                            ? "var(--accent-2)"
                            : active
                              ? "var(--accent)"
                              : "var(--border-strong)",
                        }}
                      />
                      <span
                        style={{
                          color: done ? "var(--text-faint)" : "var(--text)",
                          textDecoration: done ? "line-through" : "none",
                        }}
                      >
                        {t.content}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>

        {/* 中：事件流 */}
        <section className="glass magnetic flex min-h-[520px] flex-col rounded-2xl">
          <div className="flex items-center gap-2 border-b px-4 py-3" style={{ borderColor: "var(--border)" }}>
            <h2 className="text-sm font-semibold">执行过程</h2>
            {status === "running" && (
              <span className="pulse-dot h-2 w-2 rounded-full" style={{ background: "var(--accent)" }} />
            )}
            <div className="ml-auto flex items-center gap-2.5">
              <div className="flex overflow-hidden rounded-lg" style={{ border: "1px solid var(--border)" }}>
                {(["core", "all"] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => setFeedMode(m)}
                    className="px-2 py-0.5 text-[11px]"
                    style={{
                      background: feedMode === m ? "var(--accent-soft)" : "transparent",
                      color: feedMode === m ? "var(--accent)" : "var(--text-faint)",
                    }}
                  >
                    {m === "core" ? "核心过程" : "全部事件"}
                  </button>
                ))}
              </div>
              <span className="text-xs" style={{ color: "var(--text-faint)" }}>
                {thinkingCount > 0 && `深度思考 ${thinkingCount} · `}
                {feedMode === "core"
                  ? `${shown.length} 步${noiseCount > 0 ? ` · 折叠 ${noiseCount} 条流程记录` : ""}`
                  : `${events.length} 个事件`}
              </span>
            </div>
          </div>
          <div
            ref={feedRef}
            onScroll={onFeedScroll}
            className="scroll-thin flex-1 overflow-y-auto p-4"
          >
            {shown.length === 0 && (
              <div className="grid h-full place-items-center text-center">
                <div>
                  <p className="text-sm" style={{ color: "var(--text-dim)" }}>
                    输入需求，点击「开始设计」
                  </p>
                  <p className="mt-1 text-xs" style={{ color: "var(--text-faint)" }}>
                    先由智能体提问补齐关键信息，再依次推进：需求 → 外观 → 结构 → 硬件 → 固件 → 评审
                  </p>
                </div>
              </div>
            )}
            <div className="flex flex-col gap-2.5">
              {shown.map((it) => {
                if (it.kind === "thinking") {
                  return (
                    <ThinkingBlock
                      key={it.id}
                      agent={it.agent}
                      label={agentLabel(it.agent)}
                      text={it.text}
                      done={it.done}
                    />
                  );
                }
                if (it.kind === "text") {
                  const tint = agentTint(it.agent);
                  return (
                    <div key={it.id} className="enter flex gap-2 text-[13px] leading-relaxed">
                      <span
                        className="mt-0.5 shrink-0 rounded-md px-1.5 py-0.5 text-[10px]"
                        style={{ background: "var(--accent-soft)", color: tint }}
                      >
                        {agentLabel(it.agent)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <MiniMarkdown content={it.text} runId={runId ?? undefined} />
                        {!it.done && <span className="caret" />}
                      </div>
                    </div>
                  );
                }
                if (it.kind === "tool") {
                  // 规划/文件读写类记账动作：仅「全部事件」视图可见，且压成一行
                  if (it.noise) {
                    return (
                      <div
                        key={it.id}
                        className="enter flex items-center gap-2 px-1 text-[11px]"
                        style={{ color: "var(--text-faint)" }}
                      >
                        <span
                          className="rounded px-1.5 py-0.5 font-mono"
                          style={{ background: "var(--bg-sunken)" }}
                        >
                          {it.name}
                          {it.count > 1 ? ` ×${it.count}` : ""}
                        </span>
                        <span>流程记账 · 非设计步骤</span>
                      </div>
                    );
                  }
                  return (
                    <div
                      key={it.id}
                      className="enter rounded-xl p-2.5"
                      style={{ background: "var(--bg-sunken)", border: "1px solid var(--border)" }}
                    >
                      <div className="flex items-center gap-2 text-[11px]">
                        <span
                          className="rounded px-1.5 py-0.5 font-mono"
                          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                        >
                          {it.name}
                          {it.count > 1 ? ` ×${it.count}` : ""}
                        </span>
                        <span style={{ color: "var(--text-faint)" }}>{agentLabel(it.agent)}</span>
                        {!it.done && (
                          <span className="ml-auto flex items-center gap-1" style={{ color: "var(--text-faint)" }}>
                            <span className="pulse-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />
                            执行中
                          </span>
                        )}
                      </div>
                      {it.args && (
                        <pre className="mt-1.5 overflow-x-auto text-[11px]" style={{ color: "var(--text-faint)" }}>
                          {it.args.slice(0, 300)}
                        </pre>
                      )}
                      {it.result ? (
                        <p className="mt-1.5 text-[12px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
                          {it.result.slice(0, 400)}
                        </p>
                      ) : it.done && it.empty ? (
                        // 写入类工具常见：执行成功但没有文本回执，留一句说明而不是空白卡片
                        <p className="mt-1.5 text-[11px]" style={{ color: "var(--text-faint)" }}>
                          已执行 · 无文本输出
                        </p>
                      ) : null}
                    </div>
                  );
                }
                if (it.kind === "artifact") {
                  return (
                    <button
                      key={it.id}
                      onClick={() => openFile(it.path)}
                      className="magnetic enter flex items-center gap-2 rounded-xl px-3 py-2 text-left text-[12px]"
                      style={{
                        background: "var(--accent-2-soft)",
                        color: "var(--accent-2)",
                        border: "1px solid var(--border)",
                      }}
                    >
                      <span>产出</span>
                      <span className="font-mono">{it.path}</span>
                    </button>
                  );
                }
                return (
                  <div
                    key={it.id}
                    className="enter rounded-xl px-3 py-2 text-[12px]"
                    style={{
                      background: it.text.startsWith("出错") ? "var(--danger-soft)" : "var(--bg-sunken)",
                      color: it.text.startsWith("出错") ? "var(--danger)" : "var(--text-dim)",
                    }}
                  >
                    {it.text}
                  </div>
                );
              })}
            </div>
            {summary && (
              <div className="mt-4 rounded-2xl p-4" style={{ background: "var(--bg-sunken)" }}>
                <h3 className="mb-2 text-sm font-semibold">交付汇总</h3>
                <MiniMarkdown content={summary} runId={runId ?? undefined} />
              </div>
            )}
          </div>
        </section>

        {/* 右：产物 —— 按规划标题分组，单文件 / 按阶段打包 / 全量打包 */}
        <section id="artifacts" className="glass magnetic flex min-h-[520px] flex-col rounded-2xl">
          <div className="border-b px-4 py-3" style={{ borderColor: "var(--border)" }}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">设计产物</h2>
                <p className="text-xs" style={{ color: "var(--text-faint)" }}>
                  {files.length
                    ? `${files.length} 个文件 · 按规划标题分组`
                    : "运行开始后实时落盘"}
                </p>
              </div>
              {!!runId && files.length > 0 && (
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <a
                    href={dl({ scope: "all" })}
                    className="magnetic rounded-lg px-2.5 py-1 text-[11px]"
                    style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                  >
                    下载全部
                  </a>
                  {manifest?.next && status !== "running" && (
                    <button
                      onClick={() => resumeRun()}
                      disabled={resumeBusy}
                      className="magnetic rounded-lg px-2.5 py-1 text-[11px] disabled:opacity-50"
                      style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
                      title={`从 ${STAGE_LABEL[manifest.next] || manifest.next} 继续，复用已有产物`}
                    >
                      {resumeBusy ? "启动中…" : `继续 ${STAGE_LABEL[manifest.next] || manifest.next}`}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
          <div className="scroll-thin max-h-[280px] overflow-y-auto border-b p-3" style={{ borderColor: "var(--border)" }}>
            {!manifest?.stages.some((s) => s.files.length) && Object.keys(grouped).length === 0 && (
              <p className="text-xs" style={{ color: "var(--text-faint)" }}>
                暂无产物
              </p>
            )}
            {(manifest?.stages || []).map((st) =>
              !st.files.length ? null : (
                <div key={st.id} className="mb-2.5">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <p className="min-w-0 truncate text-[11px] font-medium" style={{ color: "var(--text-dim)" }}>
                      <span style={{ color: "var(--text-faint)" }}>{st.id} </span>
                      {st.plan_title}
                    </p>
                    {st.id ? (
                      <a
                        href={dl({ stage: st.id })}
                        className="magnetic shrink-0 rounded-md px-2 py-0.5 text-[10px]"
                        style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
                        title={`打包下载 ${st.label} 的 ${st.n_files} 个文件`}
                      >
                        打包下载 {st.n_files}
                      </a>
                    ) : (
                      <span className="shrink-0 text-[10px]" style={{ color: "var(--text-faint)" }}>
                        {st.n_files} 个
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {st.files.map((f) => (
                      <span key={f.path} className="inline-flex items-center gap-1">
                        <button
                          onClick={() => openFile(f.path)}
                          className="magnetic rounded-lg px-2 py-1 font-mono text-[11px]"
                          style={{
                            background: activeFile === f.path ? "var(--accent-soft)" : "var(--bg-sunken)",
                            color: activeFile === f.path ? "var(--accent)" : "var(--text-dim)",
                          }}
                          title={f.title}
                        >
                          {f.name}
                        </button>
                        <a
                          href={dl({ path: f.path })}
                          className="magnetic rounded-md px-1 text-[11px]"
                          style={{ color: "var(--text-faint)" }}
                          title={`下载 ${f.name}`}
                          aria-label={`下载 ${f.name}`}
                        >
                          ↓
                        </a>
                      </span>
                    ))}
                  </div>
                </div>
              ),
            )}
            {/* 后端清单不可用时退回按目录分组，保证产物永远能下载 */}
            {!manifest &&
              Object.entries(grouped).map(([stage, items]) => (
                <div key={stage} className="mb-2.5">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <p className="text-[11px] font-medium" style={{ color: "var(--text-faint)" }}>
                      {STAGE_LABEL[stage] || stage}
                    </p>
                    <a
                      href={dl({ stage })}
                      className="magnetic rounded-md px-2 py-0.5 text-[10px]"
                      style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
                    >
                      打包下载 {items.length}
                    </a>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {items.map((f) => (
                      <span key={f.path} className="inline-flex items-center gap-1">
                        <button
                          onClick={() => openFile(f.path)}
                          className="magnetic rounded-lg px-2 py-1 font-mono text-[11px]"
                          style={{
                            background: activeFile === f.path ? "var(--accent-soft)" : "var(--bg-sunken)",
                            color: activeFile === f.path ? "var(--accent)" : "var(--text-dim)",
                          }}
                        >
                          {f.path.split("/").pop()}
                        </button>
                        <a
                          href={dl({ path: f.path })}
                          className="magnetic rounded-md px-1 text-[11px]"
                          style={{ color: "var(--text-faint)" }}
                          title="下载"
                        >
                          ↓
                        </a>
                      </span>
                    ))}
                  </div>
                </div>
              ))}
          </div>
          <div className="scroll-thin flex-1 overflow-y-auto p-4">
            {!activeFile && (
              <p className="text-xs" style={{ color: "var(--text-faint)" }}>
                点击文件查看内容
              </p>
            )}
            {activeFile && (
              <>
                <p className="mb-2 font-mono text-[11px]" style={{ color: "var(--text-faint)" }}>
                  {activeFile}
                </p>
                {isImg ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={`/api/agent/raw?${new URLSearchParams(
                      { path: activeFile, ...(runId ? { run_id: runId } : {}) }
                    ).toString()}`}
                    alt={activeFile}
                    className="w-full rounded-xl"
                    style={{ border: "1px solid var(--border)" }}
                  />
                ) : isSvg ? (
                  <iframe
                    title={activeFile}
                    srcDoc={content}
                    className="h-[320px] w-full rounded-xl"
                    style={{ background: "#fff", border: "1px solid var(--border)" }}
                  />
                ) : isBinary ? (
                  <div className="rounded-xl p-4 text-xs" style={{ background: "var(--bg-sunken)" }}>
                    <p className="mb-2" style={{ color: "var(--text-dim)" }}>
                      几何文件（{activeFile.split(".").pop()?.toUpperCase()}），浏览器内不直接渲染。
                    </p>
                    <a
                      href={dl({ path: activeFile })}
                      className="magnetic inline-block rounded-lg px-3 py-1.5"
                      style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                    >
                      下载 {activeFile.split("/").pop()}
                    </a>
                  </div>
                ) : isMd ? (
                  <MiniMarkdown content={content} runId={runId ?? undefined} base={docBase} />
                ) : (
                  <pre
                    className="scroll-thin overflow-x-auto rounded-xl p-3 text-[11px] leading-relaxed"
                    style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
                  >
                    {content}
                  </pre>
                )}
              </>
            )}
          </div>
        </section>
      </main>

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={setSettings}
      />

      {errMsg && (
        <div className="mx-auto max-w-[1600px] px-5 pb-6">
          <div
            className="rounded-xl p-3 text-[12px]"
            style={{ background: "var(--danger-soft)", color: "var(--danger)" }}
          >
            {errMsg}
          </div>
        </div>
      )}

      <CompletionDialog
        open={celebrate}
        runId={runId}
        files={files}
        manifest={manifest}
        title={req.trim().slice(0, 40)}
        summary={summary}
        elapsedText={fmtDur(elapsed)}
        onClose={() => setCelebrate(false)}
        onOpenFile={(path) => {
          openFile(path);
          // 弹窗解锁 body 滚动要等一帧，晚一点滚才能让产物面板真正进入视口
          setTimeout(() => {
            document.getElementById("artifacts")?.scrollIntoView({ behavior: "smooth", block: "start" });
          }, 80);
        }}
        downloadHref={dl({ scope: "all" })}
      />

      <HistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        currentRunId={runId}
        refreshKey={historyKey}
        onOpenRun={(id) => void loadRun(id)}
        onDeleted={(id) => {
          if (id !== runId) return;
          setRunId(null);
          setEvents([]);
          setTodos([]);
          setFiles([]);
          setManifest(null);
          setContent("");
          setActiveFile(null);
          setStatus("idle");
        }}
      />
    </div>
  );
}
