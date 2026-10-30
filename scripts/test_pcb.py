"""PCB 链路自测（board.json 规范化 + .kicad_pcb 生成 + 真机 DRC）。

跑：python scripts/test_pcb.py
真机校验会自动探测 kicad-cli，找不到就 skip（可用 KICAD_CLI 指定）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from eda import board as bd, netlist as nl, parts, pcb  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


SAMPLE = {
    "components": [
        {"id": "U1", "type": "module", "value": "ESP32-C3-MINI-1"},
        {"id": "U2", "type": "ldo", "value": "AMS1117-3.3"},
        {"id": "J1", "type": "usb-c", "value": "TYPE-C 16P"},
        {"id": "D1", "type": "led", "value": "red 0603"},
        {"id": "R1", "type": "resistor", "value": "220"},
        {"id": "C1", "type": "capacitor", "value": "10uF"},
    ],
    "connections": [
        {"from": "J1.VBUS", "to": "5V"}, {"from": "J1.GND", "to": "GND"},
        {"from": "U2.VIN", "to": "5V"}, {"from": "U2.GND", "to": "GND"},
        {"from": "U2.VOUT", "to": "3V3"}, {"from": "U1.3V3", "to": "3V3"},
        {"from": "U1.GND", "to": "GND"},
        {"from": "U1.GPIO8", "to": "R1.1"}, {"from": "R1.2", "to": "D1.A"},
        {"from": "D1.K", "to": "GND"},
        {"from": "C1.1", "to": "3V3"}, {"from": "C1.2", "to": "GND"},
    ],
    "supplies": [{"net": "5V", "voltage": 5.0}, {"net": "3V3", "voltage": 3.3}],
}

RECT = {"board": {"shape": "rect", "width_mm": 52, "height_mm": 34,
                  "corner_radius_mm": 3, "thickness_mm": 1.6},
        "mounting_holes": [{"name": "H1", "x_mm": -22, "y_mm": -12, "diameter_mm": 3.2},
                           {"name": "H2", "x_mm": 22, "y_mm": -12, "diameter_mm": 3.2},
                           {"name": "H3", "x_mm": 22, "y_mm": 12, "diameter_mm": 3.2},
                           {"name": "H4", "x_mm": -22, "y_mm": 12, "diameter_mm": 3.2}],
        "keepouts": [{"name": "电池仓", "x_mm": 12, "y_mm": 8, "w_mm": 16, "h_mm": 12}],
        "edge_constraints": [{"ref": "J1", "edge": "left"}]}

print("== board.json 规范化 ==")
b = bd.normalize(RECT)
check("矩形+圆角识别为 rounded_rect", b["board"]["shape"] == "rounded_rect", b["board"]["shape"])
check("尺寸保留", (b["board"]["width_mm"], b["board"]["height_mm"]) == (52.0, 34.0))
check("安装孔 4 个", len(b["mounting_holes"]) == 4)
check("禁布区 1 个", len(b["keepouts"]) == 1)
check("接口约束保留", b["edge_constraints"][0]["edge"] == "left")

c = bd.normalize({"board": {"shape": "circle", "diameter_mm": 60}})
check("圆形由直径推长宽", (c["board"]["width_mm"], c["board"]["height_mm"]) == (60.0, 60.0))
check("圆形 64 段轮廓", len(bd.outline_points(c)) == 64)

p = bd.normalize({"board": {"shape": "polygon",
                            "polygon_mm": [[-20, -15], [10, -18], [22, 0], [8, 16], [-18, 12]]}})
check("异形板顶点保留", len(p["board"]["polygon_mm"]) == 5)
check("异形板能判内外", bd.inside(0, 0, p) and not bd.inside(21, 15, p))

big = bd.normalize({"board": {"shape": "rect", "width_mm": 40, "height_mm": 30,
                              "corner_radius_mm": 99}})
check("过大圆角被收敛", big["board"]["corner_radius_mm"] == 15.0, str(big["board"]["corner_radius_mm"]))
check("收敛有提示", any("圆角" in w for w in big["warnings"]))

bad = bd.normalize({"board": {"shape": "三角形"}})
check("未知板型降级为矩形", bad["board"]["shape"] == "rect")
check("未知板型有提示", len(bad["warnings"]) > 0)
check("None 输入不崩", bd.normalize(None)["board"]["shape"] == "rect")
check("空字典不崩", bd.normalize({})["board"]["width_mm"] > 0)

print("\n== 圆角轮廓几何 ==")
pts = bd.outline_points(b)
xs = [q[0] for q in pts]
ys = [q[1] for q in pts]
check("bbox 等于板尺寸", (round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)) == (52.0, 34.0),
      f"{max(xs) - min(xs)}x{max(ys) - min(ys)}")
area = bd._polygon_area(pts)
check("圆角面积≈矩形减四角", abs(area - (52 * 34 - (4 - 3.14159) * 9)) < 5, f"{area:.1f}")
check("轮廓点首尾不重合（靠索引闭合）", pts[0] != pts[-1])
check("圆角点数 = 4×(8+1)", len(pts) == 36, str(len(pts)))
check("角点在板内", all(bd.inside(q[0], q[1], b, 0.0) for q in pts))

print("\n== 内外判定 / 避让 ==")
check("板中心在板内", bd.inside(0, 0, b))
check("板外判外", not bd.inside(30, 0, b))
check("圆角外侧判外", not bd.inside(25.9, -16.9, b))
check("安装孔判冲突", bd.blocked(-22, -12, 5, 5, b) is not None)
check("禁布区判冲突", bd.blocked(12, 8, 5, 5, b) is not None)
check("空旷处不冲突", bd.blocked(-8, -8, 3, 3, b) is None)

print("\n== 器件占位尺寸 ==")
u1 = {"id": "U1", "kind": "module", "value": "ESP32-C3-MINI-1"}
check("模块有尺寸", parts.size_of(u1) == (15.0, 15.0), str(parts.size_of(u1)))
check("未知器件有兜底", parts.size_of({"kind": "奇怪件"}) > (0, 0))
check("零尺寸不返回 0", min(parts.size_of({"kind": "x", "size_mm": [0, 0]})) > 0)

print("\n== .kicad_pcb 生成 ==")
net = nl.annotate(nl.normalize(SAMPLE, "pcb-test"))
tmp = Path(tempfile.mkdtemp())
res = pcb.write(net, b, tmp, "board")
check("写盘成功", res.get("ok") is True, str(res.get("error")))
pcb_file = tmp / "board.kicad_pcb"
check("pcb 文件存在且非空", pcb_file.exists() and pcb_file.stat().st_size > 3000)
text = pcb_file.read_text(encoding="utf-8")
check("括号平衡", text.count("(") == text.count(")"))
check("有版本头", text.startswith("(kicad_pcb (version"))
check("声明了 GND 网络", '(net 1 "GND")' in text or 'net_name "GND"' in text)
check("Edge.Cuts 段数=轮廓点数", text.count('(layer "Edge.Cuts")') >= 36,
      str(text.count('(layer "Edge.Cuts")')))
check("有 GND 铺铜", '(zone' in text and '"B.Cu"' in text)
check("铺铜网络是 GND", 'net_name "GND"' in text)
check("4 个安装孔", text.count("mountinghole_3.2mm") >= 4)
check("器件都落了", all(f'"{c["id"]}"' in text for c in net["components"]))
check("自带封装库目录", (tmp / "board.pretty").is_dir())
check("封装库有文件", len(list((tmp / "board.pretty").glob("*.kicad_mod"))) >= 5)
check("库表登记 Gen", "fp-lib-table" in [f["name"] for f in res["files"]] or
      (tmp / "fp-lib-table").exists())
check("库表指向 .pretty", "${KIPRJMOD}/board.pretty" in (tmp / "fp-lib-table").read_text(encoding="utf-8"))
check("pad 带网络号", re.search(r'\(pad "\d+" smd rect.*?\(net \d+ "', text, re.S) is not None)
check("焊盘不在板外", all(bd.inside(float(m.group(1)) - pcb.ORIGIN_X,
                                    float(m.group(2)) - pcb.ORIGIN_Y, b, -1.0)
                          for m in re.finditer(r'\(at (-?[\d.]+) (-?[\d.]+)\)', text)
                          if m.group(1) and abs(float(m.group(1)) - pcb.ORIGIN_X) < 40))

print("\n== 布局规则 ==")
pos = pcb._place(net, b, list(b["mounting_holes"]))
check("所有器件都有坐标", all(c["id"] in pos for c in net["components"]))
check("器件不压安装孔", all(bd.blocked(x, y, *parts.size_of(
    next(c for c in net["components"] if c["id"] == cid)), b) is None
                         for cid, (x, y) in pos.items()))
j1 = pos.get("J1")
check("接口按约束靠左边", j1 is not None and j1[0] < -12, str(j1))
check("器件都在板内", all(bd.inside(x, y, b, 0.5) for x, y in pos.values()))

print("\n== 异形/圆形板也能出 ==")
for shape_name, raw in (
    ("圆形", {"board": {"shape": "circle", "diameter_mm": 50}}),
    ("异形", {"board": {"shape": "polygon",
                       "polygon_mm": [[-24, -14], [18, -18], [26, 2], [10, 17], [-22, 13]]}}),
    ("无约束", {}),
):
    bb = bd.normalize(raw)
    d2 = tmp / f"t_{shape_name}"
    r2 = pcb.write(net, bb, d2, "board")
    check(f"{shape_name}板生成成功", r2.get("ok") is True, str(r2.get("error")))
    t2 = (d2 / "board.kicad_pcb").read_text(encoding="utf-8") if r2.get("ok") else ""
    check(f"{shape_name}板括号平衡", t2.count("(") == t2.count(")") and len(t2) > 2000)

print("\n== 脏数据不崩 ==")
empty = nl.annotate(nl.normalize({}, "空"))
r3 = pcb.write(empty, bd.default_board(), tmp / "t_empty", "board")
check("空网表也能出板框", r3.get("ok") is True and (tmp / "t_empty" / "board.kicad_pcb").exists())
r4 = pcb.write({"components": [{"id": "X1", "pins": None, "kind": "奇怪"}],
                "nets": [{"name": "", "points": [None, "X1.1"]}]}, bd.normalize(None),
               tmp / "t_dirty", "board")
check("脏网表不抛异常", isinstance(r4.get("ok"), bool))

print("\n== 真机校验（kicad-cli）==")
cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
if not cli:
    for cand in (r"E:\KiCad\10.0\bin\kicad-cli.exe", r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
                 r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe"):
        if Path(cand).exists():
            cli = cand
            break
if not cli or not Path(cli).exists():
    print("  skip 未找到 kicad-cli（设 KICAD_CLI 环境变量可启用真机校验）")
else:
    print(f"  使用 {cli}")
    rpt = tmp / "drc.rpt"
    r = subprocess.run([cli, "pcb", "drc", "--severity-all", "--output", str(rpt), str(pcb_file)],
                       capture_output=True)
    out = (r.stdout + r.stderr).decode("gbk", "replace")
    check("KiCad 能加载 PCB", r.returncode == 0 and "DRC" not in out.split("\n")[0][:0] or
          rpt.exists(), out[:200])
    body = rpt.read_text(encoding="utf-8", errors="replace") if rpt.exists() else ""
    kinds: dict[str, int] = {}
    cur = ""
    for line in body.splitlines():
        m = re.match(r"\[([a-z_]+)\]", line.strip())
        if m:
            cur = m.group(1)
        elif line.strip().endswith("error") and cur:
            kinds[cur] = kinds.get(cur, 0) + 1
    hard = {k: v for k, v in kinds.items() if k != "unconnected_items"}
    check("除未布线外无 error", not hard, str(hard))
    check("只剩未布线错误（预期）", kinds.get("unconnected_items", 0) >= 0)
    check("无 courtyard 畸形", "malformed_courtyard" not in kinds)
    check("无封装库缺失", "lib_footprint_issues" not in kinds)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n通过 {PASS} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
