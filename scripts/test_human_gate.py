"""回归：人工确认闸门（外观 / 结构做完必须停下来等人）。

用户诉求：产品图、效果图、爆炸图生成后要**暂停**等待反馈或修改提示词；
结构生成完同样要等人确认，再进入下一步。结构还必须与外观同源。

这道闸不是「提示词里叮嘱一句」就算数，做成三层：
1. confirm_with_user 工具真正**阻塞**整条流（不是 sleep 假装等待）
2. 下游 stage_gate 读磁盘确认记录，没确认就不放行（模型漏调也绕不过去）
3. 等待期间护栏放行、时钟后拨 —— 等人看到一半被判超时是最荒唐的失败
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import guards  # noqa: E402
import store  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


store.create_run = lambda *a, **k: None          # type: ignore[assignment]
store.append = lambda *a, **k: None              # type: ignore[assignment]
store.recall = lambda *a, **k: []                # type: ignore[assignment]

import hitl  # noqa: E402
import pipeline  # noqa: E402
import server  # noqa: E402
import stages  # noqa: E402
import skillspec  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="hitl_"))
run_dir = tmp / "run1"
for d in ("00_prd", "10_appearance", "20_structure", "30_hardware"):
    (run_dir / d).mkdir(parents=True)
(run_dir / "00_prd" / "prd.md").write_text("# PRD\n\n尺寸 60×35×15mm", encoding="utf-8")
(run_dir / "10_appearance" / "appearance.md").write_text("# 外观\n\n圆角矩形小夜灯", encoding="utf-8")
(run_dir / "10_appearance" / "prompts.json").write_text(
    '{"prompts":[{"prompt":"圆角矩形小夜灯，60x35x15mm"}]}', encoding="utf-8"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * (40 * 1024)
(run_dir / "10_appearance" / "render-01.png").write_bytes(PNG)
(run_dir / "20_structure" / "structure.md").write_text(
    "# 结构\n\n外形 60×35×15mm，内腔 56×31×11mm", encoding="utf-8"
)
(run_dir / "20_structure" / "consistency.md").write_text("# 一致性\n\n60×35×15", encoding="utf-8")


def new_run() -> "server.Run":
    r = server.Run("设计一个儿童坐姿提醒器")
    r.dir = run_dir
    return r


print("\n【1】配置与技能")
check("默认闸门阶段 = 外观 + 结构",
      tuple(config.HUMAN_GATE_STAGES) == ("10_appearance", "20_structure"),
      str(config.HUMAN_GATE_STAGES))
check("闸门默认开启", config.HUMAN_GATE is True, "")
check("有等待上限（不会无限挂死）", config.HUMAN_WAIT_SECONDS > 0, str(config.HUMAN_WAIT_SECONDS))
b1 = skillspec.block("10_appearance")
b2 = skillspec.block("20_structure")
check("外观技能文档已加载", "agnes" in b1.lower() and "confirm_with_user" in b1, str(len(b1)))
check("结构技能文档已加载", "build123d" in b2 and "confirm_with_user" in b2, str(len(b2)))
check("技能索引列出两条", len(skillspec.catalog()) == 2, str(skillspec.catalog()))
check("未绑定阶段返回空", skillspec.block("30_hardware") == "", "")

print("\n【2】确认记录落盘与读取")
st0 = hitl.state_of(run_dir, "10_appearance")
check("没确认过 → done=False", st0["done"] is False, str(st0))
check("approved() 为假", hitl.approved(run_dir, "10_appearance") is False, "")
hitl.save_record(run_dir, "10_appearance", hitl.REVISE, "颜色太暗，换成暖白",
                 ["10_appearance/render-01.png"], rounds=1)
st1 = hitl.state_of(run_dir, "10_appearance")
check("提修改意见后仍未通过", st1["done"] is False and "暖白" in str(st1.get("feedback")), str(st1))
hitl.save_record(run_dir, "10_appearance", hitl.APPROVE, "可以，继续", rounds=2)
check("确认后 done=True", hitl.approved(run_dir, "10_appearance") is True, "")
check("轮次被记录", hitl.state_of(run_dir, "10_appearance").get("rounds") == 2, "")

print("\n【3】确认记录不算交付产物")
hitl.save_record(run_dir, "20_structure", hitl.APPROVE, "结构可以")
paths = stages.scan_dir(run_dir)
check("_human_review.json 不进产物清单",
      not any(p.endswith("_human_review.json") for p in paths), str(paths))

print("\n【4】门禁：下游必须等上游确认")
hitl.save_record(run_dir, "10_appearance", hitl.REVISE, "重做")   # 退回未确认
g = pipeline.gate(run_dir, "20_structure")
check("外观未确认 → 结构不许开工", g["ready"] is False, str(g["missing"]))
check("提示里点名 confirm_with_user",
      any("confirm_with_user" in m for m in g["missing"]), str(g["missing"]))
check("带上了上一轮用户意见",
      any("重做" in m for m in g["missing"]), str(g["missing"]))
hitl.save_record(run_dir, "10_appearance", hitl.APPROVE, "")
g2 = pipeline.gate(run_dir, "20_structure")
check("外观确认后 → 结构放行", g2["ready"] is True, str(g2["missing"]))
check("门禁回传确认状态",
      g2["human_review"] and g2["human_review"][0]["done"] is True, str(g2.get("human_review")))

hitl.save_record(run_dir, "20_structure", hitl.REVISE, "壁厚太薄")
g3 = pipeline.gate(run_dir, "30_hardware")
check("结构未确认 → 硬件不许开工",
      any("还没经用户确认" in m for m in g3["missing"]), str(g3["missing"]))
hitl.save_record(run_dir, "20_structure", hitl.APPROVE, "")
g4 = pipeline.gate(run_dir, "30_hardware")
check("结构确认后 → 硬件不再被这道闸卡住",
      not any("还没经用户确认" in m for m in g4["missing"]), str(g4["missing"]))
g5 = pipeline.gate(run_dir, "00_prd")
check("上游阶段不受闸门约束",
      not any("还没经用户确认" in m for m in g5["missing"]), str(g5["missing"]))

print("\n【5】等待真的会阻塞，用户回应后放行")
run = new_run()
box: dict = {}


def waiter() -> None:
    box["res"] = hitl.request(run, "10_appearance", "三张图已出，请确认",
                              ["10_appearance/render-01.png"], rounds=1)


t = threading.Thread(target=waiter, daemon=True)
t.start()
deadline = time.time() + 5
while time.time() < deadline and run.awaiting is None:
    time.sleep(0.02)
check("调用后进入等待态", run.awaiting is not None, str(run.awaiting))
check("等待态能被前端查到",
      (hitl.pending_of(run) or {}).get("stage") == "10_appearance", str(hitl.pending_of(run)))
check("线程仍卡着（没自己放行）", t.is_alive(), "")
check("respond 成功唤醒", hitl.respond(run, "revise", "比例不对，矮一点") is True, "")
t.join(timeout=10)
res = box.get("res") or {}
check("返回的是修改决策", res.get("decision") == hitl.REVISE, str(res))
check("反馈原样带回来", "比例不对" in str(res.get("feedback")), str(res))
check("等待结束后清空等待态", run.awaiting is None, str(run.awaiting))
check("结论写进磁盘", "比例不对" in str(hitl.state_of(run_dir, "10_appearance").get("feedback")), "")

print("\n【6】等待期间不消耗运行时限")
run2 = new_run()
box2: dict = {}


def waiter2() -> None:
    box2["res"] = hitl.request(run2, "20_structure", "模型已出", ["20_structure/model.step"])


t2 = threading.Thread(target=waiter2, daemon=True)
t2.start()
while run2.awaiting is None:
    time.sleep(0.02)
time.sleep(0.5)
# 模拟护栏在等待期间被触发：等待不是空转，不能中止
try:
    run2.started = time.time() - 99999     # 早该超时了
    run2._guard("tool_start", {"name": "read_file", "args": '{"path":"/a.md"}', "ts": time.time()})
    guard_ok = True
except guards.RunAborted as e:
    guard_ok = False
    print("     护栏抛了：", e)
check("等待期间护栏放行（等人不算超时）", guard_ok, "")
# 上面为了验护栏把 started 拨到很早，这里重新取一次基准再放行
started0, prog0 = run2.started, run2.last_progress
hitl.respond(run2, "approve", "")
t2.join(timeout=10)
check("等待时长被从运行时钟里扣掉", run2.started > started0, f"{run2.started - started0:.2f}s")
check("停滞判据的时钟同样后拨", run2.last_progress > prog0, f"{run2.last_progress - prog0:.2f}s")

print("\n【7】用户点停止 / 一直不回应 —— 两条兜底")
run3 = new_run()
box3: dict = {}


def waiter3() -> None:
    box3["res"] = hitl.request(run3, "10_appearance", "再确认一次")


t3 = threading.Thread(target=waiter3, daemon=True)
t3.start()
while run3.awaiting is None:
    time.sleep(0.02)
run3.cancel()
t3.join(timeout=10)
r3 = box3.get("res") or {}
check("停止后立刻唤醒，不再干等", not t3.is_alive(), "")
check("停止时按放行处理并记录原因", "停止" in str(r3.get("feedback")), str(r3))

old_wait, old_beat = config.HUMAN_WAIT_SECONDS, config.HUMAN_BEAT_SECONDS
config.HUMAN_WAIT_SECONDS, config.HUMAN_BEAT_SECONDS = 0.3, 0.1
run4 = new_run()
t4 = time.time()
r4 = hitl.request(run4, "20_structure", "超时场景")
config.HUMAN_WAIT_SECONDS, config.HUMAN_BEAT_SECONDS = old_wait, old_beat
check("无人回应到点自动放行", r4["decision"] == hitl.APPROVE and r4["auto"] is True, str(r4))
check("放行耗时接近上限", 0.2 <= (time.time() - t4) < 3, f"{time.time() - t4:.2f}s")
check("自动放行写进了记录", hitl.approved(run_dir, "20_structure") is True, "")

print("\n【8】闸门可关闭（离线批处理场景）")
config.HUMAN_GATE = False
run5 = new_run()
t5 = time.time()
r5 = hitl.request(run5, "10_appearance", "闸门关闭场景")
check("关闭时不阻塞", (time.time() - t5) < 0.5, f"{time.time() - t5:.2f}s")
check("关闭时按确认处理", r5["decision"] == hitl.APPROVE and r5["auto"] is True, str(r5))
g6 = pipeline.gate(run_dir, "20_structure")
check("关闭时门禁不再卡人工确认",
      not any("还没经用户确认" in m for m in g6["missing"]), str(g6["missing"]))
config.HUMAN_GATE = True

print("\n【9】工具层接线")
import tools  # noqa: E402

names = [getattr(t, "name", "") for t in tools.ALL_TOOLS]
check("confirm_with_user 已注册", "confirm_with_user" in names, str(names))
check("工具数量 = 12", len(tools.ALL_TOOLS) == 12, str(len(tools.ALL_TOOLS)))
check("阶段别名能解析", tools._stage_id("结构设计") == "20_structure", tools._stage_id("结构设计"))
check("中文标签能解析", tools._stage_id("外观") == "10_appearance", tools._stage_id("外观"))
check("ID 原样返回", tools._stage_id("20_structure") == "20_structure", "")

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'=' * 46}\n通过 {PASS} 项，失败 {FAIL} 项\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
