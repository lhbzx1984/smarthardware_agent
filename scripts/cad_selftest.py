# -*- coding: utf-8 -*-
"""cad.py 自检：用一个真实的「桌面温湿度小夜灯」结构脚本跑通 build -> STEP/STL/SVG。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import cad  # noqa: E402

SCRIPT = r'''
from build123d import *

W, D, H = 100.0, 80.0, 40.0      # 外形尺寸 mm
WALL = 2.0                        # 壁厚
R = 8.0                           # 倒圆角
BASE_H = 30.0                     # 下壳高度

# --- 下壳：圆角矩形底盘，向上开口的 2mm 薄壁壳体 ---
with BuildPart() as shell:
    Box(W, D, BASE_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(shell.edges().filter_by(Axis.Z), radius=R)
    # 挖内腔：底面留 WALL，四周留 WALL，顶面开口
    with Locations((0, 0, WALL)):
        Box(W - 2 * WALL, D - 2 * WALL, BASE_H - WALL,
            align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    # 顶面 OLED 视窗（0.96" 屏 34x18mm）
    with Locations((0, 12, BASE_H - WALL / 2)):
        Box(34, 18, WALL, mode=Mode.SUBTRACT)
    # 右侧 USB-C 开口
    with Locations((W / 2 - WALL / 2, 0, 10)):
        Box(WALL, 9, 4, mode=Mode.SUBTRACT)

# --- 上盖：透光罩，向下开口的 1.2mm 薄壳 ---
with BuildPart() as lid:
    Box(W, D, H - BASE_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(lid.edges().filter_by(Axis.Z), radius=R)
    with Locations((0, 0, 0)):
        Box(W - 2.4, D - 2.4, (H - BASE_H) - 1.2,
            align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)

# --- 主控 PCB ---
with BuildPart() as pcb:
    Box(70, 55, 1.6, align=(Align.CENTER, Align.CENTER, Align.MIN))

with BuildPart() as sensor:
    Cylinder(radius=5, height=3, align=(Align.CENTER, Align.CENTER, Align.MIN))

parts = {
    "下壳": shell.part,
    "上盖": lid.part.moved(Location((0, 0, BASE_H))),
    "主控板": pcb.part.moved(Location((0, 0, 6))),
    "传感器": sensor.part.moved(Location((-30, -25, 8))),
}
'''

def main() -> int:
    out = Path(__file__).resolve().parents[1] / "workspace" / "_cadtest"
    out.mkdir(parents=True, exist_ok=True)
    print("runner:", cad.detect_runner())
    print("available:", cad.available())
    if not cad.available():
        print("CAD 运行时不可用，跳过几何自检")
        return 2
    res = cad.build(SCRIPT, str(out), "lamp")
    print(json.dumps(res, ensure_ascii=False, indent=2)[:2000])
    for p in sorted(out.iterdir()):
        print(f"  {p.name:32s} {p.stat().st_size:>10,d} B")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
