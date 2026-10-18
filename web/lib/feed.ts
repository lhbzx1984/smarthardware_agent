/**
 * 事件流 → 渲染模型（纯函数，可脱离 React 单独测试）。
 *
 * 两项职责：
 * 1) 把 SSE 逐块推送的 token / thinking 增量按「智能体」聚合成整段，
 *    否则每个字都会被渲染成一张卡片（历史上就是这样碎成一条条的）。
 * 2) 标注工具的「流程噪声」属性：write_todos / ls / read_file 这类记账动作
 *    不属于设计过程，核心视图要折叠掉，只留真正推进设计的关键步骤。
 */

export type AgentEvent = {
  type: string;
  ts?: number;
  agent?: string;
  /** tool_call_id：流式下 args 后到，靠它把 start / end 精确配对 */
  cid?: string;
  text?: string;
  name?: string;
  args?: string;
  result?: string;
  /** 工具执行完但没有文本输出（写入类工具常见） */
  empty?: boolean;
  path?: string;
  size?: number;
  preview?: string;
  items?: { content: string; status: string }[];
  summary?: string;
  message?: string;
  mode?: string;
  requirement?: string;
};

export type FeedItem =
  | { kind: "text"; id: number; agent: string; text: string; done: boolean }
  | { kind: "thinking"; id: number; agent: string; text: string; done: boolean }
  | {
      kind: "tool";
      id: number;
      agent: string;
      name: string;
      args: string;
      result: string;
      /** true = 规划/文件读写类记账动作，核心视图不展示 */
      noise: boolean;
      /** 连续同名调用的合并次数 */
      count: number;
      /** 已收到 tool_end，或已由本轮收口标记为结束 */
      done: boolean;
      /** 工具执行了但没有文本输出 */
      empty: boolean;
    }
  | { kind: "artifact"; id: number; path: string; size: number }
  | { kind: "system"; id: number; text: string };

/** 子智能体展示名：事件里的 agent 是节点名，这里做一次人话映射 */
export const AGENT_LABEL: Record<string, string> = {
  main: "指挥",
  "requirements-analyst": "需求分析",
  "industrial-designer": "工业设计",
  "mechanical-engineer": "结构工程",
  "hardware-engineer": "硬件工程",
  "firmware-engineer": "固件工程",
  "integration-reviewer": "交叉评审",
};

const AGENT_TINT = ["var(--accent)", "var(--accent-2)", "var(--warn)"];

/** 明确属于流程噪声的工具名（TodoList / Filesystem 中间件的记账动作） */
const NOISE_EXACT = new Set([
  "ls",
  "glob",
  "grep",
  "read_file",
  "write_file",
  "edit_file",
  "delete_file",
  "move_file",
  "read_multiple_files",
  "search_files",
  "task",
  "tasks",
]);

export function isNoiseTool(name: string): boolean {
  const n = (name || "").trim().toLowerCase();
  if (!n) return true;
  if (n.includes("todo")) return true; // write_todos / read_todos / todo …
  if (NOISE_EXACT.has(n)) return true;
  return n.endsWith("_file");
}

export function agentLabel(agent: string): string {
  if (AGENT_LABEL[agent]) return AGENT_LABEL[agent];
  const hit = Object.keys(AGENT_LABEL).find((k) => agent.startsWith(k.split("-")[0]));
  return hit ? AGENT_LABEL[hit] : agent.slice(0, 8);
}

export function agentTint(agent: string): string {
  if (agent === "main") return AGENT_TINT[0];
  let h = 0;
  for (let i = 0; i < agent.length; i++) h = (h * 31 + agent.charCodeAt(i)) >>> 0;
  return AGENT_TINT[h % AGENT_TINT.length];
}

/** 增量合并：兼容「全量重推」与「纯增量」两种后端实现 */
function append(buf: string, piece: string): string {
  return buf && piece.startsWith(buf) ? piece : buf + piece;
}

export function buildFeed(events: AgentEvent[]): FeedItem[] {
  const out: FeedItem[] = [];
  let id = 0;
  // 每个 agent 各自维护一条打开的流式块，互不干扰（并发子智能体也不会串）
  const openText = new Map<string, Extract<FeedItem, { kind: "text" }>>();
  const openThink = new Map<string, Extract<FeedItem, { kind: "thinking" }>>();
  const seenArtifacts = new Set<string>();

  const closeText = (agent: string) => {
    const t = openText.get(agent);
    if (t) {
      t.done = true;
      openText.delete(agent);
    }
  };
  const closeThink = (agent: string) => {
    const k = openThink.get(agent);
    if (k) {
      k.done = true;
      openThink.delete(agent);
    }
  };
  /** 非流式事件出现时，所有流式块都必须闭合 —— 否则跨智能体的两轮输出会被黏成一段，顺序错乱 */
  const closeAll = () => {
    openText.forEach((it) => {
      it.done = true;
    });
    openText.clear();
    openThink.forEach((it) => {
      it.done = true;
    });
    openThink.clear();
  };

  /** cid -> 工具卡片，用于把流式后到的 args / result 精确绑回同一条 */
  const toolById = new Map<string, Extract<FeedItem, { kind: "tool" }>>();

  const pushTool = (agent: string, name: string, args: string, cid?: string) => {
    const noise = isNoiseTool(name);
    const prev = out[out.length - 1];
    // 只合并「流程记账」类动作（write_todos / ls …），它们成对出现十几次，逐条展示纯噪声；
    // 实质设计动作（save_artifact / check_circuit …）必须逐条保留，不做合并。
    if (
      prev &&
      prev.kind === "tool" &&
      prev.noise &&
      noise &&
      prev.agent === agent &&
      prev.name === name
    ) {
      prev.count += 1;
      prev.result = ""; // 清空结果，让随后的 tool_end 绑定到合并后的这一条
      prev.done = false;
      prev.empty = false;
      if (args) prev.args = args;
      if (cid) toolById.set(cid, prev);
      return prev;
    }
    const it: Extract<FeedItem, { kind: "tool" }> = {
      kind: "tool",
      id: id++,
      agent,
      name,
      args,
      result: "",
      noise,
      count: 1,
      done: false,
      empty: false,
    };
    out.push(it);
    if (cid) toolById.set(cid, it);
    return it;
  };

  for (const ev of events) {
    const agent = ev.agent || "main";

    if (ev.type === "thinking" && ev.text) {
      closeText(agent); // 同一智能体内，思考先于正文
      const cur = openThink.get(agent);
      if (cur) cur.text = append(cur.text, ev.text);
      else {
        const it = { kind: "thinking" as const, id: id++, agent, text: ev.text, done: false };
        out.push(it);
        openThink.set(agent, it);
      }
      continue;
    }

    if (ev.type === "token" && ev.text) {
      closeThink(agent);
      const cur = openText.get(agent);
      if (cur) cur.text = append(cur.text, ev.text);
      else {
        const it = { kind: "text" as const, id: id++, agent, text: ev.text, done: false };
        out.push(it);
        openText.set(agent, it);
      }
      continue;
    }

    closeAll();

    if (ev.type === "tool_start") {
      pushTool(agent, ev.name || "tool", ev.args || "", ev.cid);
    } else if (ev.type === "tool_end") {
      // 优先按 cid 精确配对；拿不到 cid（老事件 / 匿名调用）时按「同智能体 + 未收口」兜底
      const hit =
        (ev.cid ? toolById.get(ev.cid) : undefined) ??
        ([...out].reverse().find(
          (o) =>
            o.kind === "tool" && !o.done && o.agent === agent && (!ev.name || o.name === ev.name),
        ) as Extract<FeedItem, { kind: "tool" }> | undefined);
      if (hit) {
        hit.result = ev.result || "";
        hit.done = true;
        hit.empty = Boolean(ev.empty) || !hit.result.trim();
        if (ev.args) hit.args = ev.args; // args 在流式中后到，这里补回卡片
      }
    } else if (ev.type === "artifact" && ev.path) {
      if (seenArtifacts.has(ev.path)) continue; // 同一产物只报一次
      seenArtifacts.add(ev.path);
      out.push({ kind: "artifact", id: id++, path: ev.path, size: ev.size || 0 });
    } else if (ev.type === "run_start") {
      out.push({
        kind: "system",
        id: id++,
        text: `任务已启动（${ev.mode === "demo" ? "演示模式" : "真实智能体"}）`,
      });
    } else if (ev.type === "error") {
      out.push({ kind: "system", id: id++, text: `出错：${ev.message || "未知错误"}` });
    } else if (ev.type === "warn") {
      out.push({ kind: "system", id: id++, text: ev.message || "警告" });
    }
  }

  // 收尾：仍在流式中的块标记完成；未收到结果的工具标记为空输出，
  // 这样前端显示「已执行（无文本输出）」而不是永远转圈。
  openText.forEach((it) => {
    it.done = true;
  });
  openThink.forEach((it) => {
    it.done = true;
  });
  out.forEach((it) => {
    if (it.kind === "tool" && !it.done) {
      it.done = true;
      it.empty = !it.result.trim();
    }
  });
  return out;
}

/** 核心视图：只保留推进设计的关键步骤（思考 / 结论 / 关键工具 / 产物） */
export function coreOnly(feed: FeedItem[]): FeedItem[] {
  return feed.filter((it) => !(it.kind === "tool" && it.noise));
}
