/**
 * 验证事件聚合：碎片化的逐块推送必须被合并成整段。
 * 运行：node --experimental-strip-types scripts/test_feed.ts
 */
import { agentLabel, buildFeed, coreOnly, type AgentEvent } from "../web/lib/feed.ts";

let failed = 0;
function check(name: string, cond: boolean, extra = "") {
  console.log(`${cond ? "  ✓" : "  ✗"} ${name}${extra ? "  " + extra : ""}`);
  if (!cond) failed++;
}

// ---- 用例 1：逐字推送 60 个 token，必须只产出 1 个文本块 ----
{
  const sentence = "我将为您设计一个桌面式温湿度监测小夜灯，首先让我建立任务清单再逐阶段委派子智能体。";
  const events: AgentEvent[] = [
    { type: "run_start", mode: "live" },
    ...[...sentence].map((ch) => ({ type: "token", agent: "main", text: ch })),
  ];
  const feed = buildFeed(events);
  const texts = feed.filter((f) => f.kind === "text");
  console.log("\n[用例1] 逐字推送");
  check("60 个字符合并为 1 个文本块", texts.length === 1, `实际 ${texts.length} 块 / 共 ${texts.reduce((n, t) => n + (t as { text: string }).text.length, 0)} 字`);
  check("文本内容无损", texts.length === 1 && (texts[0] as { text: string }).text === sentence);
  check("块被标记完成", texts.length === 1 && (texts[0] as { done: boolean }).done === true);
  check("系统消息独立成项", feed.filter((f) => f.kind === "system").length === 1);
}

// ---- 用例 2：思考增量分块推送，合并为 1 个思考块 ----
{
  const chunks = ["我先判断", "用户要的核心场景，", "再决定传感选型：", "DHT11 便宜但慢，", "SHT30 稳但贵。"];
  const events: AgentEvent[] = [
    ...chunks.map((t) => ({ type: "thinking", agent: "main", text: t })),
    { type: "token", agent: "main", text: "开始拆解。" },
  ];
  const feed = buildFeed(events);
  const thinks = feed.filter((f) => f.kind === "thinking");
  console.log("\n[用例2] 思考流合并");
  check("5 段思考合并为 1 块", thinks.length === 1, `实际 ${thinks.length} 块`);
  check("思考内容完整", thinks.length === 1 && (thinks[0] as { text: string }).text === chunks.join(""));
  check("后续文本另起一块", feed.filter((f) => f.kind === "text").length === 1);
}

// ---- 用例 3：兼容「全量重推」的后端 ----
{
  const events: AgentEvent[] = [
    { type: "thinking", agent: "main", text: "甲" },
    { type: "thinking", agent: "main", text: "甲乙" },
    { type: "thinking", agent: "main", text: "甲乙丙" },
  ];
  const feed = buildFeed(events);
  const t = feed.find((f) => f.kind === "thinking") as { text: string } | undefined;
  console.log("\n[用例3] 全量重推去重");
  check("全量重推不重复累加", t?.text === "甲乙丙", `实际 ${JSON.stringify(t?.text)}`);
}

// ---- 用例 4：并发子智能体各自聚合，互不串台 ----
{
  const events: AgentEvent[] = [];
  for (const ch of "外观走极简圆润路线") events.push({ type: "token", agent: "industrial-designer", text: ch });
  for (const ch of "结构用上下壳卡扣") events.push({ type: "token", agent: "mechanical-engineer", text: ch });
  const feed = buildFeed(events);
  const byAgent = new Map<string, string>();
  feed.filter((f) => f.kind === "text").forEach((f) => {
    const x = f as { agent: string; text: string };
    byAgent.set(x.agent, x.text);
  });
  console.log("\n[用例4] 并发子智能体隔离");
  check("两个 agent 各 1 块", byAgent.size === 2, `实际 ${byAgent.size}`);
  check("外观文本正确", byAgent.get("industrial-designer") === "外观走极简圆润路线");
  check("结构文本正确", byAgent.get("mechanical-engineer") === "结构用上下壳卡扣");
  check("标签映射", agentLabel("industrial-designer") === "工业设计");
}

// ---- 用例 5：工具调用打断流式块 ----
{
  const events: AgentEvent[] = [
    { type: "token", agent: "main", text: "先查库。" },
    { type: "tool_start", agent: "hardware-engineer", name: "search_cirkit_docs", args: "DHT11" },
    { type: "tool_end", agent: "hardware-engineer", name: "search_cirkit_docs", result: "命中 3 条" },
    { type: "token", agent: "main", text: "查到了。" },
  ];
  const feed = buildFeed(events);
  const tools = feed.filter((f) => f.kind === "tool") as { result: string }[];
  console.log("\n[用例5] 工具调用");
  check("文本被工具调用打断为 2 块", feed.filter((f) => f.kind === "text").length === 2);
  check("工具结果回填", tools.length === 1 && tools[0].result === "命中 3 条");
  check("首块已闭合", (feed.find((f) => f.kind === "text") as { done: boolean }).done === true);
}

// ---- 用例 6：规划/记账类工具被标记为噪声，且连续同名调用合并 ----
{
  const events: AgentEvent[] = [
    { type: "tool_start", agent: "main", name: "write_todos", args: "[{...}]" },
    { type: "tool_end", agent: "main", name: "write_todos", result: "已更新" },
    { type: "tool_start", agent: "main", name: "write_todos", args: "[{...}]" },
    { type: "tool_end", agent: "main", name: "write_todos", result: "已更新" },
    { type: "tool_start", agent: "main", name: "write_todos", args: "[{...}]" },
    { type: "tool_end", agent: "main", name: "write_todos", result: "已更新" },
    { type: "tool_start", agent: "main", name: "ls", args: "/" },
    { type: "tool_start", agent: "hardware-engineer", name: "check_circuit", args: "ERC" },
    { type: "tool_end", agent: "hardware-engineer", name: "check_circuit", result: "通过" },
  ];
  const feed = buildFeed(events);
  const tools = feed.filter((f) => f.kind === "tool") as {
    name: string;
    noise: boolean;
    count: number;
  }[];
  console.log("\n[用例6] 流程噪声标记与合并");
  check("3 次 write_todos 合并成 1 条", tools.filter((t) => t.name === "write_todos").length === 1);
  const wt = tools.find((t) => t.name === "write_todos");
  check("合并计数为 3", wt?.count === 3, `实际 ${wt?.count}`);
  check("write_todos/ls 标记为噪声", tools.filter((t) => t.noise).length === 2);
  check("check_circuit 不是噪声", tools.find((t) => t.name === "check_circuit")?.noise === false);
}

// ---- 用例 7：核心视图只留关键步骤 ----
{
  const events: AgentEvent[] = [
    { type: "run_start", mode: "live" },
    { type: "thinking", agent: "main", text: "先判断传感选型。" },
    { type: "tool_start", agent: "main", name: "write_todos", args: "[]" },
    { type: "tool_end", agent: "main", name: "write_todos", result: "ok" },
    { type: "tool_start", agent: "main", name: "read_file", args: "/AGENTS.md" },
    { type: "tool_end", agent: "main", name: "read_file", result: "..." },
    { type: "token", agent: "requirements-analyst", text: "PRD 已定稿。" },
    { type: "tool_start", agent: "hardware-engineer", name: "search_cirkit_docs", args: "DHT11" },
    { type: "tool_end", agent: "hardware-engineer", name: "search_cirkit_docs", result: "命中 3 条" },
    { type: "artifact", path: "30_hardware/bom.csv", size: 500 },
    { type: "artifact", path: "30_hardware/bom.csv", size: 500 },
  ];
  const feed = buildFeed(events);
  const core = coreOnly(feed);
  console.log("\n[用例7] 核心过程视图");
  check("原始视图含噪声工具", feed.some((f) => f.kind === "tool" && f.noise));
  check("核心视图剔除全部噪声工具", !core.some((f) => f.kind === "tool" && f.noise));
  check("核心视图保留关键工具", core.some((f) => f.kind === "tool" && f.name === "search_cirkit_docs"));
  check("核心视图保留思考块", core.some((f) => f.kind === "thinking"));
  check("核心视图保留智能体结论", core.some((f) => f.kind === "text"));
  check("核心视图保留产物", core.some((f) => f.kind === "artifact"));
  check("重复产物只报一次", feed.filter((f) => f.kind === "artifact").length === 1);
  check("核心步数 = 5", core.length === 5, `实际 ${core.length}`);
  check("折叠条数 = 2", feed.length - core.length === 2, `实际 ${feed.length - core.length}`);
}

console.log(failed === 0 ? "\n全部通过 ✓" : `\n${failed} 项失败 ✗`);
process.exit(failed === 0 ? 0 : 1);
