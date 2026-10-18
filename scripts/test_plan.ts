/**
 * 任务规划推导的单测（零依赖，node --experimental-strip-types 直接跑）。
 *
 * 用例来自真实运行产物：f1e978347fd6 那次把固件写进了臆造的子目录，
 * 正好覆盖「阶段目录不在顶层」这个坑。
 */
import { derivePlan, stageProgress, stagesDone, STAGES } from "../web/lib/plan.ts";

let pass = 0;
let fail = 0;

function check(name: string, actual: unknown, expected: unknown) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    pass++;
    console.log(`  ok   ${name}`);
  } else {
    fail++;
    console.log(`  FAIL ${name}\n       期望 ${e}\n       实际 ${a}`);
  }
}

// ---- 真实产物清单（含嵌套子目录的固件文件）----
const REAL = [
  "00_prd/prd.md",
  "10_appearance/appearance.md",
  "10_appearance/orthographic.svg",
  "10_appearance/render-01.png",
  "10_appearance/render-02.png",
  "10_appearance/render-prompts.md",
  "20_structure/ecoglow_shell_p1.step",
  "20_structure/ecoglow_shell_p1.stl",
  "20_structure/ecoglow_shell_p2.step",
  "20_structure/ecoglow_shell_preview.svg",
  "20_structure/exploded-01.png",
  "20_structure/model.py",
  "20_structure/structure.md",
  "30_hardware/bom.csv",
  "90_review/structure_summary.md",
  "home/user/00_prd/prd.md",
  "home/user/repos/prod-EcoGlow-8094f6/40_firmware/firmware.ino",
  "root/workspace/10_appearance/appearance.md",
  "workspace/30_hardware/netlist.json",
];

console.log("阶段判定（真实产物）:");
const done = stagesDone(REAL);
for (const s of STAGES) {
  check(`${s.id} 已完成`, done[s.id], true);
}

console.log("\n阶段进度:");
const prog = stageProgress(REAL);
check("done", prog.done, 6);
check("total", prog.total, 6);
check("finished", prog.finished, true);

console.log("\n规划项推导:");
// 模型只写了一次清单，之后从不回写 —— 状态必须靠产物反推
const TODOS = [
  { content: "需求分析 - 委派 requirements-analyst 产出 PRD", status: "in_progress" },
  { content: "外观设计 - 委派 industrial-designer 产出外观方案", status: "pending" },
  { content: "结构设计 - 委派 mechanical-engineer 产出结构方案", status: "pending" },
  { content: "硬件设计 - 委派 hardware-engineer 产出硬件方案", status: "pending" },
  { content: "固件设计 - 委派 firmware-engineer 产出固件方案", status: "pending" },
  { content: "交叉评审 - 委派 integration-reviewer 交叉评审", status: "pending" },
  { content: "检查工作区目录是否可写", status: "pending" },
];
const items = derivePlan(TODOS, REAL);
check("全部阶段项标记为已完成", items.slice(0, 6).map((i) => i.status), [
  "completed", "completed", "completed", "completed", "completed", "completed",
]);
check("derived 标记为真", items.slice(0, 6).every((i) => i.derived), true);
check("匹配不到阶段的项保留模型状态", items[6].derived, false);

console.log("\n空产物时（刚启动）:");
const empty = derivePlan(TODOS, ["AGENTS.md"]);
check("第一项在推进中", empty[0].status, "in_progress");
check("其余待办", empty.slice(1, 6).map((i) => i.status), [
  "pending", "pending", "pending", "pending", "pending",
]);

console.log("\n只出完 PRD 时:");
const half = derivePlan(TODOS, ["00_prd/prd.md"]);
check("需求已完成", half[0].status, "completed");
check("外观转为推进中", half[1].status, "in_progress");

console.log("\n回填会话（无事件流、拿不到 todo）:");
const noTodos = derivePlan([], REAL);
check("按输出契约造出 6 项", noTodos.length, 6);
check("全部已完成", noTodos.map((i) => i.status), [
  "completed", "completed", "completed", "completed", "completed", "completed",
]);
const noTodosEmpty = derivePlan([], ["AGENTS.md"]);
check("无产物时第一项推进中", noTodosEmpty[0].status, "in_progress");

console.log(`\n通过 ${pass} 项，失败 ${fail} 项`);
if (fail) process.exit(1);
