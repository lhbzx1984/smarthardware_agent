"""回归：建模失败必须给出真实报错，而不是模板；写不对时参数化兜底要能交出 STEP/STL。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import cad  # noqa: E402
import tools  # noqa: E402

# 本测的是本机运行时的报错链路；auto 现在优先云端沙箱（且 .env 用了 override=True，
# 命令行传 AGENT_CAD_RUNTIME 也盖不住），所以这里直接固定运行时
cad.detect_runner = lambda: ("local", [sys.executable])

TMP = Path(tempfile.mkdtemp(prefix="cadtest_"))
N = [0]
FAILED = []


def check(name: str, cond: bool, extra: str = "") -> None:
    N[0] += 1
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name} {extra}")


print("== 静态检查（不启动子进程就能拦下的写法）==")
BAD = {
    "Workplanes": "from build123d import *\nwith Workplanes((0,0,5)):\n    Box(1,1,1)\n",
    "Cylinder r=": "from build123d import *\nCylinder(r=5, h=3)\n",
    "链式 fillet": "from build123d import *\nwith BuildPart() as p:\n    Box(10,10,10).fillet(radius=1)\nparts={'x': p.part}\n",
    "offset 负向": "from build123d import *\noffset(amount=-2)\n",
    "脚本内导出": "from build123d import *\nexport_stl(p, 'a.stl')\n",
    "show()": "from build123d import *\nshow()\n",
    # 注意是**代码里的**中文标点：注释里的中文括号/逗号完全合法（见下面的不误伤用例）
    "中文标点": "from build123d import *\nwith BuildPart() as p:\n    Box(宽，深，高)\nparts={'x': p.part}\n",
    "没有 parts": "from build123d import *\nwith BuildPart() as p:\n    Box(10,10,10)\n",
}
for label, s in BAD.items():
    hits = cad.static_check(s)
    check(f"拦下：{label}", bool(hits), f"（未命中）{s[:60]!r}")
    if hits:
        check(f"  带行号：{label}", hits[0].strip().startswith("第") or "行" in hits[0] or "没有" in hits[0])

GOOD = cad.shell_script(40, 20, 150, wall=2.0, r=8.0)
check("正常模板不被误伤", cad.static_check(GOOD) == [], str(cad.static_check(GOOD))[:200])

# 注释与字符串字面量里的中文标点是合法的：旧版把它们也判成错误，
# 模型只好删光中文注释重写脚本（实测白烧一轮，脚本还更简陋）
CN_NOTES = (
    "from build123d import *\n"
    "W, D, H = 60.0, 35.0, 15.0   # 外壳尺寸（取自外观提示词）\n"
    "with BuildPart() as lower:\n"
    "    Box(W, D, H)   # 掏内腔（放 PCB、电池）\n"
    "parts = {'下壳（含内腔）': lower.part}\n"
)
check("注释里的中文标点不误伤", cad.static_check(CN_NOTES) == [], str(cad.static_check(CN_NOTES))[:200])

print("\n== 真实报错不能被模板盖住 ==")
out = TMP / "build"
# Cylinder(r=, h=) 现在是**平台自动改写**（比静态拦下更省一轮），断言相应放宽
r = cad.build("from build123d import *\nwith BuildPart() as p:\n    Cylinder(r=5, h=3)\nparts={'x': p.part}\n", out, base_name="t1")
rewritten = any("Cylinder" in n for n in (r.get("notes") or []))
check("Cylinder(r=,h=) 被拦下或自动改写",
      ((not r.get("ok")) and r.get("static") is True) or (r.get("ok") and rewritten), str(r)[:200])
check("改写时明确说明改了什么", (not rewritten) or "radius=" in "".join(r.get("notes") or []),
      str(r.get("notes"))[:160])

# 静态拦截单独用一条仍会报错的写法验证（这个平台不改写，只能拦下）
r1b = cad.build("from build123d import *\nwith BuildPart() as p:\n    Box(10,10,10)\n    p.export_stl('a.stl')\nparts={'x': p.part}\n", out, base_name="t1b")
check("静态拦截生效", (not r1b.get("ok")) and r1b.get("static") is True, str(r1b)[:200])
check("报错指向具体写法", "export" in (r1b.get("error") or "").lower(), (r1b.get("error") or "")[:160])
check("失败脚本已留档", bool(r1b.get("script_path")) and (out / "_cad_debug" / "t1b_failed.py").exists(), str(r1b.get("script_path")))
check("留档路径是工作区相对路径", str(r1b.get("script_path", "")).startswith("build/_cad_debug/"), str(r1b.get("script_path")))

r2 = cad.build("from build123d import *\nwith BuildPart() as p:\n    Box(10,10,10)\n    with Locations((0,0,5)):\n        Box(5,5,5, mode=Mode.SUBTRACT)\n    undefined_name\nparts={'x': p.part}\n", out, base_name="t2")
err2 = r2.get("error") or ""
check("运行时错误带 traceback 关键字", ("Traceback" in err2) or ("NameError" in err2), err2[:200])
check("运行时错误标注退出码", "退出码" in err2, err2[:160])
check("运行时错误不写成模板开头", not err2.lstrip().startswith("【build123d"), err2[:120])
check("报错来自主运行时而非回落版本", r2.get("runner") == "local", str(r2.get("runner")))

h = cad.hint_for("something totally unknown happened")
check("未命中时提示模板是示例不是报错", "不是你的报错" in h, h[:80])
check("已知错误给出针对性建议", "radius=" in cad.hint_for("unexpected keyword argument 'r'"))

print("\n== 崩溃诊断（子进程无输出）==")
note = cad.crash_note(-1073741819, "local")
check("Windows 访问违规可识别", "0xC0000005" in note, note[:80])
check("给出几何诱因", "圆角" in note and "壁厚" in note, note[:120])
check("POSIX SIGSEGV 可识别", "SIGSEGV" in cad.crash_note(-11, "local"))

print("\n== 尺寸解析 ==")
d = cad.parse_dims("W, D, H = 40.0, 20.0, 150.0\nWALL, R, BASE_H = 2.0, 8.0, 150.0\n")
check("解析批量赋值 W/D/H", d.get("W") == 40.0 and d.get("D") == 20.0 and d.get("H") == 150.0, str(d))
check("解析壁厚与圆角", d.get("WALL") == 2.0 and d.get("R") == 8.0, str(d))
d2 = cad.parse_dims("WIDTH = 150.0\nDEPTH = 40.0\nHEIGHT = 20.0\n")
check("解析别名 WIDTH/DEPTH/HEIGHT", d2.get("W") == 150.0 and d2.get("D") == 40.0 and d2.get("H") == 20.0, str(d2))
check("单行赋值也能解析", cad.parse_dims("W = 10.0\nD = 5.0\nH = 3.0\n").get("H") == 3.0)
check("没有尺寸时不脑补", cad.parse_dims("parts = {}\n") == {}, str(cad.parse_dims("parts = {}\n")))
check("忽略含变量的赋值", cad.parse_dims("W, D, H = 40.0, D*2, 150.0\n") == {}, str(cad.parse_dims("W, D, H = 40.0, D*2, 150.0\n")))

print("\n== 参数化兜底建模（真实跑一遍）==")
for dims in ((40.0, 20.0, 150.0), (150.0, 40.0, 20.0)):
    W, D, H = dims
    res = cad.build_shell(W, D, H, TMP / "fb", base_name=f"fb{int(W)}", wall=2.0, r=8.0)
    check(f"兜底建模成功 {dims}", bool(res.get("ok")), (res.get("error") or "")[:200])
    files = res.get("files", [])
    check(f"  有 STEP/STL {dims}", any(f.endswith(".step") for f in files) and any(f.endswith(".stl") for f in files), str(files))
    if res.get("parts"):
        sz = res["parts"][0]["size_mm"]
        check(f"  尺寸对得上 {dims}", abs(sz[0] - W) < 0.6 and abs(sz[1] - D) < 0.6 and abs(sz[2] - H) < 0.6, str(sz))
res_hole = cad.build_shell(100, 80, 40, TMP / "fb", base_name="fbhole", wall=2.0, r=8.0, holes=[(0, 12, 34, 18)])
check("带开孔的兜底也能建", bool(res_hole.get("ok")), (res_hole.get("error") or "")[:200])

print("\n== 工具层：失败后自动兜底 ==")
sink: list = []
tools.bind_context(TMP / "tool", lambda e: sink.append(e))
bad_script = "from build123d import *\nW, D, H = 40.0, 20.0, 150.0\nWALL, R = 2.0, 8.0\nundefined_thing\n"
res_bad = cad.build(bad_script, TMP / "tool", base_name="moisture")
text, parts = tools._cad_fail_text(res_bad, bad_script, TMP / "tool", "moisture")
check("失败文案含真实报错", "NameError" in text or "Traceback" in text, text[:200])
check("解析到尺寸后兜底成功", bool(parts), text[-300:])
check("兜底时写了 model.py", (TMP / "tool" / "model.py").exists())
check("兜底时发了 artifact 事件", any(e.get("type") == "artifact" for e in sink), str([e.get("type") for e in sink])[:120])
check("兜底说明里给了尺寸", "40.0×20.0×150.0" in text or "40×20×150" in text, text[-300:])

_, parts_none = tools._cad_fail_text(
    cad.build("from build123d import *\nundefined_thing\n", TMP / "tool2", base_name="x"),
    "from build123d import *\nundefined_thing\n", TMP / "tool2", "x")
check("拿不到尺寸时不硬兜底", not parts_none)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASS' if not FAILED else 'FAILED'}: {N[0] - len(FAILED)}/{N[0]}")
if FAILED:
    print("失败项：", FAILED)
    sys.exit(1)
