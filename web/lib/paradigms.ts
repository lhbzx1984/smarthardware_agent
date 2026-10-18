/**
 * 决策范式（Orchestrator 的执行档位）。
 *
 * 四种范式不是并列的名词，而是四块不同的「思考肌肉」，各有明确的适用边界：
 * - ReAct              —— 边做边想，短链查询，快但看不到全局
 * - Plan-and-Execute   —— 先画施工图再施工，全局视野，慢一点但阶段可验证
 * - Reflexion          —— 失败后沉淀可复用规则，适合高频重复任务
 * - Tree of Thoughts   —— 单层分支 + 打分择优，适合多约束权衡
 *
 * 服务端会在 run 开始时推一条 paradigm 事件，前端据此显示当前档位；
 * 用户也可以手动覆盖（「自动」= 由服务端按任务 DNA 路由）。
 */
export type ParadigmInfo = {
  key: string;
  label: string;
  philosophy: string;
  boundary: string;
  stages: string[];
  tool_budget: number;
  max_seconds: number;
  reflect: boolean;
  branches: number;
  fallback: string;
  recursion_limit: number;
};

export const PARADIGM_OPTIONS: { key: string; label: string; hint: string }[] = [
  { key: "", label: "自动", hint: "按任务 DNA 三基因（时效 / 容错 / 进化）自动选型" },
  { key: "lean", label: "ReAct", hint: "边做边想：短查询、≤5 步工具链、错了无所谓" },
  { key: "standard", label: "Plan-Execute", hint: "先画施工图：完整六阶段，阶段可验证" },
  { key: "review", label: "Reflexion", hint: "加反思层：评审后返工一轮，并沉淀规则" },
  { key: "explore", label: "ToT", hint: "分支择优：需求模糊 / 多目标冲突时先出 3 方案打分" },
];

/** 连续这么久收不到任何事件（含心跳）就认为后端假死 */
export const STALE_MS = 20000;
