/**
 * 任务规划的真实进度推导。
 *
 * 为什么不能只信 `write_todos`：那套清单是**模型自觉**更新的。实测 agnes-2.5-flash
 * 常常只在开头写一次清单，之后不管做到哪都不回写 —— 于是界面上永远只有第一格是完成的，
 * 而旁边产物面板里 PRD / 渲染图 / STEP 早就堆满了。
 *
 * 这里改成「以磁盘产物为准反推阶段状态」：文件是硬证据，模型嘴上说没说不算数。
 * 阶段定位靠产物目录（AGENTS.md 的输出契约），规划项靠关键词匹配到阶段。
 */

export type Todo = { content: string; status: string };
export type PlanItem = Todo & {
  /** true = 状态由产物推导得出，不是模型自己写的 */
  derived: boolean;
};

type Stage = {
  id: string;
  label: string;
  /** 该阶段的「主交付物」判据（相对路径） */
  match: RegExp;
  /** 规划项文案里出现这些词，就认为它指的是这个阶段 */
  kw: RegExp;
};

// 判据用「路径里出现该阶段目录」而不是「以它开头」：模型偶尔会把文件写进
// 自己臆造的子目录（实测出现过 `home/user/repos/prod-xxx/40_firmware/firmware.ino`），
// 严格要求顶层就会把已完成的阶段误判成未完成。
const at = (stage: string, tail: string) => new RegExp(`(?:^|/)${stage}/${tail}`, "i");

export const STAGES: Stage[] = [
  { id: "00_prd", label: "需求定义", match: at("00_prd", "[^/]*\\.md$"), kw: /需求|PRD|规格|澄清|产品定义|立项/ },
  { id: "10_appearance", label: "外观设计", match: at("10_appearance", "[^/]*\\.md$"), kw: /外观|工业设计|造型|渲染|色彩|材质|ID\b/ },
  {
    id: "20_structure",
    label: "结构设计",
    match: /(?:\/|^)20_structure\/(?:structure\.md$|[^/]*\.(?:step|stp|stl)$)/i,
    kw: /结构|机械|CAD|壳体|装配|分件|建模/,
  },
  {
    id: "30_hardware",
    label: "硬件设计",
    match: /(?:\/|^)30_hardware\/(?:[^/]*\.md$|bom\.csv$)/i,
    kw: /硬件|电路|原理图|BOM|元器件|选型|接线|网表/,
  },
  { id: "40_firmware", label: "固件与仿真", match: at("40_firmware", ""), kw: /固件|代码|Arduino|仿真|烧录|编程/ },
  { id: "90_review", label: "交叉评审", match: at("90_review", "[^/]*\\.md$"), kw: /评审|review|复核|汇总|验收|交付/ },
];

export function stagesDone(paths: string[]): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const s of STAGES) {
    out[s.id] = paths.some((p) => s.match.test(p.replace(/\\/g, "/")));
  }
  return out;
}

/** 第一个还没完成、但已经有文件落地的阶段；都没有就返回第一个未完成阶段 */
export function currentStage(done: Record<string, boolean>): Stage | null {
  return STAGES.find((s) => !done[s.id]) || null;
}

/**
 * 用产物推导任务规划状态。
 *
 * - 匹配到**多个**阶段的规划项（例如「并行委派 industrial / mechanical / hardware / firmware」）
 *   必须等这些阶段全部完成才算完成
 * - 匹配不到阶段的规划项（例如「向用户汇总」）保留模型写的状态
 */
export function derivePlan(todos: Todo[], paths: string[]): PlanItem[] {
  const list = todos.length
    ? todos
    : // 历史会话（尤其是回填进来的）没有事件流，拿不到模型写的清单 ——
      // 直接按输出契约造一份，否则面板永远显示「等待生成」
      STAGES.map((s) => ({ content: s.label, status: "pending" }));

  const done = stagesDone(paths);
  const cur = currentStage(done);
  const items: PlanItem[] = [];
  let assignedCurrent = false;

  for (const t of list) {
    const hit = STAGES.filter((s) => s.kw.test(t.content || ""));
    if (!hit.length) {
      items.push({ ...t, derived: false });
      continue;
    }
    const allDone = hit.every((s) => done[s.id]);
    if (allDone) {
      items.push({ ...t, status: "completed", derived: true });
      continue;
    }
    const isCurrent = !!cur && hit.some((s) => s.id === cur.id);
    if (isCurrent && !assignedCurrent) {
      assignedCurrent = true;
      items.push({ ...t, status: "in_progress", derived: true });
    } else {
      items.push({ ...t, status: "pending", derived: true });
    }
  }

  // 兜底：一个 in_progress 都没有时，把第一个未完成项点亮，避免面板「全灰」
  if (!items.some((i) => i.status === "in_progress")) {
    const idx = items.findIndex((i) => i.status !== "completed");
    if (idx >= 0) items[idx] = { ...items[idx], status: "in_progress" };
  }
  return items;
}

/** 阶段进度：给顶部状态条用 */
export function stageProgress(paths: string[]): {
  done: number;
  total: number;
  label: string;
  finished: boolean;
} {
  const d = stagesDone(paths);
  const done = STAGES.filter((s) => d[s.id]).length;
  const cur = currentStage(d);
  return {
    done,
    total: STAGES.length,
    label: cur ? cur.label : "全部完成",
    finished: done === STAGES.length,
  };
}

export function stageLabel(id: string): string {
  return STAGES.find((s) => s.id === id)?.label || id;
}
