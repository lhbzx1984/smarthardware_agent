"""验证 CAD 脚本的自动改写路径：
1) bbox.x_length/y_length/z_length（0.13 的 BoundBox 没有这些属性）
2) 线上真实失败的那段脚本（workspace/b5258537540f/20_structure/_cad_debug/model_failed.py）
3) Cylinder(r=, h=) 参数名改写 + 圆柱类常量 DIAMETER/TOTAL_H 的尺寸解析（兜底模板要用）
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import cad  # noqa: E402

ok, ver = cad.available()
print("runtime:", ok, ver)
assert ok, "CAD 运行时不可用，测不了"
fail = 0


def run(tag: str, script: str):
    global fail
    with tempfile.TemporaryDirectory() as td:
        res = cad.build(script, Path(td) / "20_structure", base_name="model")
    print(f"\n=== {tag} ===")
    print("ok:", res.get("ok"))
    for n in res.get("notes") or []:
        print("note:", n)
    if res.get("ok"):
        print("parts:", [(p["name"], p["size_mm"], p["volume_cm3"]) for p in res.get("parts", [])])
    else:
        fail += 1
        print("error:", (res.get("error") or "")[:600])
        print("hint:", res.get("hint"))


# 1) 最小复现
run("bbox.*_length", '''from build123d import *
with BuildPart() as shell:
    Box(40, 30, 20)
parts = {"壳": shell.part}
b = shell.part.bounding_box()
print(b.x_length, b.y_length, b.z_length)
''')

# 2) 线上真实失败脚本
real = ROOT / "workspace" / "b5258537540f" / "20_structure" / "_cad_debug" / "model_failed.py"
if real.exists():
    run("线上失败脚本", real.read_text(encoding="utf-8"))
else:
    print("\n(跳过：找不到线上失败脚本)")

# 3) Cylinder 参数名
run("Cylinder(r=, h=)", '''from build123d import *
with BuildPart() as p:
    Cylinder(r=20, h=15)
parts = {"柱": p.part}
''')

# 4) 尺寸解析：圆柱类常量
dims = cad.parse_dims("DIAMETER = 90.0\nTOTAL_H = 48.0\nWALL = 2.5\n")
print("\n=== parse_dims(DIAMETER/TOTAL_H) ===")
print(dims)
if not (dims.get("W") == 90.0 and dims.get("D") == 90.0 and dims.get("H") == 48.0):
    fail += 1
    print("❌ 尺寸解析不对")

# 5) 平台补全 math 导入 + 报错行号还原
bad_math = "\n".join([
    "from build123d import *",            # 1
    "with BuildPart() as p:",             # 2
    "    Box(10, 10, 10)",                # 3
    "parts = {'壳': p.part}",             # 4
    "print(radians(90))",                 # 5
    "b = p.part.bounding_box()",          # 6
    "print(b.nope)",                      # 7  ← 期望报错就报在这一行
])
with tempfile.TemporaryDirectory() as td:
    res = cad.build(bad_math, Path(td) / "20_structure", base_name="model")
print("\n=== math 补全 + 行号还原 ===")
print("ok:", res.get("ok"))
err = res.get("error") or ""
print("error:", err[:400])
print("hint:", (res.get("hint") or "")[:200])
if "line 7" not in err:
    fail += 1
    print("❌ 行号没还原成用户脚本的行号（应出现 line 7）")
if "radians" in err and "NameError" in err:
    fail += 1
    print("❌ radians 没有被平台补上")

print("\n=== hint_for(AttributeError) ===")
for s in ("AttributeError: 'BoundBox' object has no attribute 'x_length'",
          "AttributeError: 'Solid' object has no attribute 'foo'",
          "AttributeError: 'Weird' object has no attribute 'bar'"):
    print(" -", cad.hint_for(s).split("；")[0])

sys.exit(1 if fail else 0)
