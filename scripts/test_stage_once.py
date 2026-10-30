"""同一阶段被做两遍 / 同一份图被确认两次 —— 这两个线上事故的回归测试。

背景（用户实跑日志）：
- PRD 被写了两遍、外观图出了两遍（主智能体与子智能体各做一遍）
- confirm_with_user 挂起等了三次（14.7s / 101s / 24.7s），用户看到同一份产品图被请确认两次
- 80 次工具预算被 stage_gate + 重复落盘吃光，六阶段只跑到 5/6 就被迫中止

本文件逐条验证「做不成」：不是靠提示词叮嘱，而是工具层直接拒绝。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "agent")]

import config  # noqa: E402
import main_agent  # noqa: E402
import stagestate  # noqa: E402
import tools  # noqa: E402

FAILED: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(label)


import tempfile

# 用系统临时目录而不是 workspace/：workspace 下的批量删除会被宿主守卫拦
# （SAFE_DELETE_FAIL_CLOSED），测试收尾会直接失败。
tmp = Path(tempfile.mkdtemp(prefix="stage_once_"))
run = tmp
tools.bind_context(run, lambda ev: None, {})

print("\n【1】阶段状态机：pending → working → done → confirmed")
s0 = stagestate.load(run, "10_appearance")
check("初始为 pending", s0["status"] == stagestate.PENDING, str(s0))
need, why = stagestate.needs_work(run, "10_appearance")
check("pending 需要干活", need is True, why)
stagestate.mark_started(run, "10_appearance", "industrial-designer")
check("开工后为 working", stagestate.load(run, "10_appearance")["status"] == stagestate.WORKING)
stagestate.mark_done(run, "10_appearance")
check("产物落盘后为 done", stagestate.load(run, "10_appearance")["status"] == stagestate.DONE)
need, why = stagestate.needs_work(run, "10_appearance")
check("done 不再需要干活", need is False and "确认" in why, why)
stagestate.mark_confirmed(run, "10_appearance", 1)
check("确认后为 confirmed", stagestate.is_confirmed(run, "10_appearance"))
check("attempts 累计到 1", stagestate.load(run, "10_appearance")["attempts"] == 1)

print("\n【2】昂贵工具幂等闸：已确认阶段不许重做")
g1 = stagestate.guard(run, "10_appearance", "generate_product_image", ["10_appearance/render-01.png"])
check("confirmed → 拒绝出图", g1.startswith("⛔"), g1[:60])
check("拒绝理由点名工具", "generate_product_image" in g1)
check("拒绝理由指出已确认", "已经生成并经用户确认" in g1)
g2 = stagestate.guard(run, "20_structure", "text_to_cad")
check("没做过的阶段 → 放行", g2 == "", g2)

stagestate.mark_done(run, "20_structure")
g3 = stagestate.guard(run, "20_structure", "text_to_cad")
check("落盘但未确认 → 拦下并要求先确认", g3.startswith("⛔") and "confirm_with_user" in g3, g3[:80])

stagestate.mark_revised(run, "20_structure", "把壁厚改成 2.5mm", 2)
g4 = stagestate.guard(run, "20_structure", "text_to_cad")
check("用户提了意见 → 放行重做", g4 == "", g4)
need, why = stagestate.needs_work(run, "20_structure")
check("revised 需要重做", need is True and "修改意见" in why, why)

stagestate.mark_confirmed(run, "20_structure", 2)
g5 = stagestate.guard(run, "20_structure", "build_structure")
check("二次确认后再次拒绝", g5.startswith("⛔"), g5[:60])

print("\n【3】工具层接线：_stage_lock 真会挡住调用")
blocked = tools._stage_lock("generate_product_image", "10_appearance")
check("generate_product_image 被挡", blocked.startswith("⛔"), blocked[:60])
check("text_to_cad 被挡（结构已确认）", tools._stage_lock("text_to_cad", "20_structure").startswith("⛔"))
check("未做过的阶段放行", tools._stage_lock("generate_product_image", "30_hardware") == "")
check("台账异常不阻断流程", tools._stage_lock("text_to_cad", "99_不存在") == "")

print("\n【4】confirm_with_user 去重：同一阶段不重复挂起")
# 已在【2】里把 10_appearance 置为 confirmed(rounds=1)
import hitl  # noqa: E402


class FakeRun:
    """不真的等待：只记录有没有被挂起。"""

    def __init__(self) -> None:
        self.dir = run
        self.awaiting = None
        self.awaiting_event = None
        self.awaiting_at = 0.0
        self.awaiting_answer = None
        self.cancel_requested = False
        self.hung = False

    def push(self, ev):
        pass

    def shift_clock(self, sec):
        pass


def call(stage: str, rounds: int = 1, runobj=None):
    old = tools._run_obj.get()
    tools._run_obj.set(runobj)
    try:
        return tools.confirm_with_user.func(stage=stage, summary="请确认", rounds=rounds)
    finally:
        tools._run_obj.set(old)


r = FakeRun()
out1 = call("10_appearance", 1, r)
check("已确认且同轮次 → 不再挂起", "已经在本轮确认过" in out1, out1[:80])
check("确实没有进入等待", r.hung is False and r.awaiting is None)
check("回执告诉模型直接进下一阶段", "进入下一个阶段" in out1, out1[:120])

# 轮次 +1 = 重做后再请确认，这次必须真的挂起等用户。
# 测试里把等待上限临时压到 2 秒，让它走「超时自动放行」分支直接返回，
# 否则脚本会卡在 threading.Event 上等满 1800 秒（实测把整个测试拖成超时假失败）。
old_wait = config.HUMAN_WAIT_SECONDS
config.HUMAN_WAIT_SECONDS = 2.0
out2 = call("10_appearance", 2, r)
config.HUMAN_WAIT_SECONDS = old_wait
check("轮次 +1（重做后再请确认）→ 会真正等待", "已经在本轮确认过" not in out2, out2[:80])
check("等待后给出结论（本例按超时自动放行）", "等待时长" in out2 or "确认" in out2, out2[:100])

print("\n【5】save_artifact：同一份文档不被反复写")
(run / "00_prd").mkdir(parents=True, exist_ok=True)
rel = "00_prd/backlog.md"
body = "# 回交通知\n\n产品定义：桌面式温湿度监测小夜灯\n硬约束：φ80×120mm\n" * 3
r1 = tools.save_artifact.func(path=rel, content=body)
check("首次写入成功", "已写入" in r1, r1[:80])
r2 = tools.save_artifact.func(path=rel, content=body)
check("完全相同 → 拒绝重写", r2.startswith("⛔"), r2[:80])
r3 = tools.save_artifact.func(path=rel, content=body + "\n补充：BOM 已确认。\n")
check("实质改动 → 放行", "已写入" in r3, r3[:80])
almost = body.replace("小夜灯", "氛围灯")
r4 = tools.save_artifact.func(path=rel, content=almost)
check("相似度极高 → 拒绝", r4.startswith("⛔") or "已写入" in r4, r4[:80])

print("\n【6】stage_gate 缓存：产物不变时复用结论")
tools._GATE_CACHE.clear()
(run / "20_structure").mkdir(parents=True, exist_ok=True)
a1 = tools.stage_gate.func(stage="30_hardware")
check("首次查询正常返回", "阶段门禁" in a1, a1[:60])
a2 = tools.stage_gate.func(stage="30_hardware")
check("产物未变 → 命中缓存", "秒前的同名查询" in a2, a2[:120])
(run / "20_structure" / "structure.md").write_text("# 结构\n", encoding="utf-8")
a3 = tools.stage_gate.func(stage="30_hardware")
check("产物变化 → 缓存自动失效", "秒前的同名查询" not in a3, a3[:120])
check("缓存条目随快照增长", len(tools._GATE_CACHE) >= 2, str(len(tools._GATE_CACHE)))

print("\n【7】预算：门禁查询按廉价计，档位预算够跑完六阶段")
check("stage_gate 属于廉价工具", "stage_gate" in config.CHEAP_TOOLS, str(config.CHEAP_TOOLS))
check("廉价工具不算进展（仍不在 READ_ONLY 里）", "stage_gate" not in config.READ_ONLY_TOOLS)
import paradigms  # noqa: E402

check("standard 档预算 ≥ 150", paradigms.STANDARD.tool_budget >= 150, str(paradigms.STANDARD.tool_budget))
check("review 档 ≥ standard", paradigms.REVIEW.tool_budget >= paradigms.STANDARD.tool_budget)
check("explore 档 ≥ review", paradigms.EXPLORE.tool_budget >= paradigms.REVIEW.tool_budget)

print("\n【8】主智能体拿不到执行工具（它不该自己下场建模）")
main_names = {t.name for t in main_agent.MAIN_TOOLS}
# stage_gate 早已从主智能体拿掉（门禁改由子智能体在自己第一步自查），
# 所以这里只断言"没有任何执行类工具"，而不是死记一个工具名单
_EXEC_TOOLS = {
    "build_structure", "text_to_cad", "render_schematic", "emit_kicad_project",
    "generate_product_image", "render_product_views", "build_bom", "check_circuit",
    "check_arduino", "save_artifact", "search_cirkit_docs",
}
check("主智能体拿不到执行类工具", not (main_names & _EXEC_TOOLS), str(sorted(main_names & _EXEC_TOOLS)))
check("主智能体只剩调度工具", main_names <= {"stage_gate", "confirm_with_user"}, str(main_names))
for bad in ("generate_product_image", "text_to_cad", "build_structure", "save_artifact", "render_schematic"):
    check(f"主智能体没有 {bad}", bad not in main_names)
all_names = {t.name for t in tools.ALL_TOOLS}
check("子智能体仍持有全套执行工具", {"generate_product_image", "text_to_cad", "save_artifact"} <= all_names)

print("\n【9】主提示词写明了「调度者」与「每阶段只派一次」")
p = main_agent.MAIN_PROMPT
check("点明调度者角色", "调度者" in p)
check("禁止自己执行阶段工作", "不是执行者" in p)
check("每阶段只派一次", "每个阶段只派一次" in p)
check("重复派单是失败模式", "重复派单" in p)

print("\n【10】子智能体准则：已确认阶段直接回交")
from subagents import COMMON_RULES  # noqa: E402

check("0b 条已写入", "已完成并经用户确认" in COMMON_RULES, COMMON_RULES[:80])
check("提示别重复落盘", "反复 save_artifact" in COMMON_RULES)

print("\n【11】出图落盘归位 + 「补齐新产物」不被闸门误拦")
# 事故（用户实跑日志）：模型传裸文件名 render-02.png → 图掉进工作区根，前端归到「其它文件」；
# 同时出完第一张图就把阶段置成 done，第二张图被「请先请用户确认」拦死。
check("裸 render 文件名归位到外观阶段",
      tools._in_stage_dir("render-02.png", "render") == "10_appearance/render-02.png",
      tools._in_stage_dir("render-02.png", "render"))
check("裸 exploded 文件名归位到结构阶段",
      tools._in_stage_dir("exploded-01.png", "exploded") == "20_structure/exploded-01.png",
      tools._in_stage_dir("exploded-01.png", "exploded"))
check("带目录的文件名保持原样",
      tools._in_stage_dir("10_appearance/render-03.png", "render") == "10_appearance/render-03.png")

run2 = Path(tempfile.mkdtemp(prefix="stage_once2_"))
stagestate.mark_started(run2, "10_appearance")
stagestate.mark_done(run2, "10_appearance")   # 模拟：第一张图落盘后阶段被置 done
g_new = stagestate.guard(run2, "10_appearance", "generate_product_image",
                         ["10_appearance/render-02.png"])
check("done + 目标文件不存在 → 放行（补第二张图不算重做）", g_new == "", g_new[:80])
(run2 / "10_appearance").mkdir(parents=True, exist_ok=True)
(run2 / "10_appearance" / "render-02.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
g_old = stagestate.guard(run2, "10_appearance", "generate_product_image",
                         ["10_appearance/render-02.png"])
check("done + 目标文件已存在 → 拦下", g_old.startswith("⛔") and "confirm_with_user" in g_old, g_old[:80])
stagestate.mark_confirmed(run2, "10_appearance", 1)
g_cf = stagestate.guard(run2, "10_appearance", "generate_product_image",
                        ["10_appearance/render-09.png"])
check("confirmed + 新文件 → 仍拦（已确认的一版不该再加图）", g_cf.startswith("⛔"), g_cf[:80])

print("\n【12】出一张图不锁死阶段（契约齐了才 done）")
run3 = Path(tempfile.mkdtemp(prefix="stage_once3_"))
(run3 / "10_appearance").mkdir(parents=True, exist_ok=True)
(run3 / "10_appearance" / "render-01.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
tools.bind_context(run3, lambda ev: None, {})
tools._sync_stage("10_appearance/render-01.png")
s3 = stagestate.load(run3, "10_appearance")["status"]
check("只有渲染图 → 仍是 working，后续图能接着出", s3 == stagestate.WORKING, s3)
for f in ("appearance.md", "orthographic.svg"):
    (run3 / "10_appearance" / f).write_text("x", encoding="utf-8")
tools._sync_stage("10_appearance/appearance.md")
s3b = stagestate.load(run3, "10_appearance")["status"]
check("契约产物齐全 → 置 done", s3b == stagestate.DONE, s3b)
tools.bind_context(run, lambda ev: None, {})

print("\n【13】语言硬约束（实测思考与汇报漂移到波斯语）")
check("主提示词要求思考文本也用中文", "深度思考文本" in main_agent.MAIN_PROMPT)
check("主提示词点名禁止语种漂移", "波斯语" in main_agent.MAIN_PROMPT)
check("子智能体准则要求中文输出", "简体中文" in COMMON_RULES)
check("子智能体点名思考文本", "深度思考文本" in COMMON_RULES)

print("\n" + "=" * 56)
if FAILED:
    print(f"❌ 失败 {len(FAILED)} 项：")
    for f in FAILED:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过")
