"""CAD 链路健壮性回归（2026-10-14 事故单）。

线上症状（run 日志）：
1. 模型反复写 CadQuery 风格链式 `.fillet(...)`（网上教程大多是 CadQuery 写法），
   静态检查拦住后重写还犯 —— 同一行被拦三次
2. 子进程空输出崩溃（rc=1，stdout/stderr 全空）连续三次，连「只有一个 Box」的
   最简脚本都崩 —— 但同脚本单独跑完全正常：是瞬时环境故障，不是几何问题。
   旧文案却 100% 归因为「OCP 几何内核崩了」，误导模型去改没病的脚本
3. save_artifact 只传 content 丢 path → ValidationError 回给它后下一轮还犯，
   token 全烧在同一个坑里

本测试钉死对应的三个修复：
A. normalize_script 把单行链式 .fillet()/.chamfer() 自动改写成函数式
B. 空输出崩溃原地重试一次 + 环境自检区分「环境坏」vs「脚本几何问题」
C. save_artifact 缺 path 时按内容推断落盘，传反自动互换
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import cad  # noqa: E402
import config  # noqa: E402
import tools  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


print("== A. 链式 fillet/chamfer 自动改写 ==")

src = "lid.edges().filter_by(Axis.Z).sort_by(Axis.Z)[-1:].fillet(radius=2)"
out, notes = cad.normalize_script(src)
check("链式 fillet 改写成函数式",
      "fillet(lid.edges().filter_by(Axis.Z).sort_by(Axis.Z)[-1:], radius=2)" in out, out)
check("改写有说明", any("链式" in n for n in notes), str(notes))

out2, _ = cad.normalize_script("part.edges().chamfer(1)")
check("链式 chamfer 位置参数", "chamfer(part.edges(), 1)" in out2, out2)

out3, n3 = cad.normalize_script("fillet(builder.edges().filter_by(Axis.Z), radius=R)")
check("已是函数式不动", out3 == "fillet(builder.edges().filter_by(Axis.Z), radius=R)" and not n3, out3)

out4, _ = cad.normalize_script("x = part.edges().fillet(2)")
check("含等号的行不动（防误改赋值）", out4 == "x = part.edges().fillet(2)", out4)

out5, _ = cad.normalize_script("part.edges().fillet(2)  # 顶边倒圆")
check("行尾注释保留", "fillet(part.edges(), 2)  # 顶边倒圆" in out5, out5)

out6, _ = cad.normalize_script("    base.edges().filter_by(Axis.Z).fillet(radius=R)")
check("缩进保留", "\n    fillet(base.edges().filter_by(Axis.Z), radius=R)" in "\n" + out6, repr(out6))

# 端到端：改完应该能过静态检查
bad_then_fixed = cad.static_check(cad.normalize_script(src)[0])
check("改写后不再被静态检查拦", not bad_then_fixed, str(bad_then_fixed))

print("== B. 空输出崩溃：原地重试 + 环境自检 ==")
# 本组验证的是本机子进程路径；auto 现在优先云端沙箱，这里固定成本机运行时
cad.detect_runner = lambda: ("local", [sys.executable])

tmp = Path(tempfile.mkdtemp(prefix="cadrob_"))
script = "from build123d import *\nwith BuildPart() as p:\n    Box(10, 10, 10)\nparts = {'x': p.part}\n"
marker_line = cad._MARKER + '{"ok": true, "count": 1, "files": ["t_x.step"], "parts": []}'


class Proc:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = rc, out, err


calls = {"n": 0, "model": 0}  # n=全部子进程调用（含 -c 探测/自检），model=真正跑脚本的次数


def flaky_run(cmd, **kw):  # noqa: ANN001
    """第一次空输出崩，第二次正常 —— 瞬时环境故障的典型形态。"""
    calls["n"] += 1
    if "-c" in cmd:  # 环境自检/探测
        return Proc(0, "ENV_OK")
    calls["model"] += 1
    return Proc(1) if calls["model"] == 1 else Proc(0, marker_line)


with mock.patch.object(cad.subprocess, "run", side_effect=flaky_run):
    r = cad.build(script, tmp / "r1", base_name="t")
check("空输出后原地重试成功", r.get("ok") is True, str(r)[:200])
check("恰好两次建模尝试（1崩+1成）", calls["model"] == 2, str(calls))

calls["n"] = 0


def always_empty(cmd, **kw):  # noqa: ANN001
    if "-c" in cmd:
        return Proc(0, "ENV_OK")  # 自检：环境其实没问题
    return Proc(1)  # 建模永远空输出崩


with mock.patch.object(cad.subprocess, "run", side_effect=always_empty):
    r2 = cad.build(script, tmp / "r2", base_name="t")
err2 = r2.get("error") or ""
check("持续空输出 → 报错带环境自检", "环境自检" in err2, err2[-300:])
check("自检通过 → 指向脚本几何", "问题在脚本几何" in err2, err2[-300:])
check("不再一口咬定是几何内核崩", "这不是 API 写法问题，而是 OCP 几何内核" not in err2, err2[:200])

calls["n"] = 0


def env_dead(cmd, **kw):  # noqa: ANN001
    if "-c" in cmd:
        return Proc(1)  # 自检也挂：环境真坏了
    return Proc(1)


with mock.patch.object(cad.subprocess, "run", side_effect=env_dead):
    r3 = cad.build(script, tmp / "r3", base_name="t")
err3 = r3.get("error") or ""
check("自检也挂 → 明确说环境问题、别改脚本", "环境自检失败" in err3 and "不是脚本问题" in err3, err3[-300:])

# 明确报错（有 traceback）绝不重试 —— 重试只会盖掉真因
calls["n"] = 0
calls["model"] = 0


def real_traceback(cmd, **kw):  # noqa: ANN001
    if "-c" in cmd:
        return Proc(0, "ENV_OK")
    calls["model"] += 1
    return Proc(1, "", "Traceback ...\nAttributeError: 'Part' has no attribute 'foo'")


with mock.patch.object(cad.subprocess, "run", side_effect=real_traceback):
    r4 = cad.build(script, tmp / "r4", base_name="t")
check("有明确报错不重试（只跑 1 次）", calls["model"] == 1, str(calls))
check("真实 traceback 透传", "AttributeError" in (r4.get("error") or ""), (r4.get("error") or "")[:200])

print("== C. save_artifact 缺 path 容错 ==")

run = tmp / "run_c"
run.mkdir(parents=True)
config.WORKSPACE = tmp / "ws"
tools.bind_context(run, lambda ev: None)

msg = tools.save_artifact.invoke({"content": "from build123d import *\nwith BuildPart() as p:\n    Box(1,1,1)\nparts={'x':p.part}"})
check("无 path 的 python 脚本 → 20_structure/model.py",
      "20_structure/model.py" in msg and (run / "20_structure" / "model.py").exists(), msg[:150])
check("回执里教它下次带 path", "没传 path" in msg, msg[-120:])

md = "# 桌面小夜灯 — 结构设计文档\n\n## 外形尺寸\nW=60 D=40 H=60\n\n内容足够长，不是占位。"
msg2 = tools.save_artifact.invoke({"content": md})
check("无 path 的结构文档 → 20_structure/structure.md",
      "20_structure/structure.md" in msg2 and (run / "20_structure" / "structure.md").exists(), msg2[:150])

long_content = "# 硬件设计\n\n" + "元件清单……\n" * 30
msg3 = tools.save_artifact.invoke({"path": long_content, "content": "30_hardware/hardware.md"})
check("path/content 传反自动互换",
      "30_hardware/hardware.md" in msg3 and (run / "30_hardware" / "hardware.md").exists(), msg3[:150])

msg4 = tools.save_artifact.invoke({"path": "00_prd/prd.md", "content": ""})
check("空 content 明确拒绝", "content" in msg4 and "为空" in msg4, msg4[:120])

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
