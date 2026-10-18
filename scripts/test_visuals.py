"""可视化链路自检：电路原理图 + CAD 工程视图 / 爆炸图。

这两条都是「离线兜底」，必须保证任何机器上都能出图 —— 所以测试里连网络都不碰。
"""
from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def valid_svg(svg: str) -> tuple[bool, str]:
    """能被 XML 解析 + 根元素是 svg，才可能在浏览器里渲染出来。"""
    if not svg:
        return False, "空字符串"
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        return False, f"XML 解析失败：{e}"
    if not root.tag.endswith("svg"):
        return False, f"根节点不是 svg：{root.tag}"
    return True, ""


# ------------------------------------------------------------------ 网表样例
NETLIST = {
    "components": [
        {"id": "U1", "type": "mcu", "value": "ESP32-C3", "note": "主控"},
        {"id": "R1", "type": "resistor", "value": "220ohm"},
        {"id": "D1", "type": "led", "value": "red 5mm"},
        {"id": "C1", "type": "capacitor", "value": "100nF"},
        {"id": "Y1", "type": "crystal", "value": "16MHz"},
        {"id": "J1", "type": "connector", "value": "USB-C"},
        {"id": "S1", "type": "switch", "value": "tact"},
    ],
    "connections": [
        {"from": "U1.GPIO8", "to": "R1.1"},
        {"from": "R1.2", "to": "D1.anode"},
        {"from": "D1.cathode", "to": "GND"},
        {"from": "U1.3V3", "to": "C1.1"},
        {"from": "C1.2", "to": "GND"},
        {"from": "J1.VBUS", "to": "U1.VIN"},
        {"from": "J1.GND", "to": "GND"},
        {"from": "S1.1", "to": "U1.GPIO9"},
        {"from": "S1.2", "to": "GND"},
        {"from": "Y1.1", "to": "U1.XTAL_P"},
    ],
    "supplies": [{"net": "3V3", "voltage": 3.3}],
}


def test_schematic() -> None:
    import schematic

    print("[1] 电路原理图")
    svg = schematic.render(NETLIST, "测试原理图")
    ok, why = valid_svg(svg)
    check("渲染结果是合法 SVG", ok, why)
    check("包含元件标签 U1", "U1" in svg)
    check("包含元件标签 R1", "R1" in svg)
    check("画出了电源轨", "3V3" in svg)
    check("画出了 GND 轨", "GND" in svg)
    check("元件数量统计正确", "7 元件" in svg, svg[:200])
    check("有折线连线（polyline）", "<polyline" in svg)

    # 空网表不能崩
    ok2, why2 = valid_svg(schematic.render({}, "空"))
    check("空网表不崩溃且返回合法 SVG", ok2, why2)

    # 写盘
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "schematic.svg"
        okw, size = schematic.render_to(NETLIST, dest)
        check("写盘成功", okw and size > 1000, f"size={size}")

    # 脏数据：缺 supplies、连线指向不存在的元件
    dirty = {
        "components": [{"id": "R1", "type": "resistor"}, {"id": "D1", "type": "led"}],
        "connections": [{"from": "R1.1", "to": "D1.anode"}, {"from": "XX.9", "to": "D1.k"}],
    }
    ok3, why3 = valid_svg(schematic.render(dirty, "脏数据"))
    check("缺 supplies / 悬空连线不崩溃", ok3, why3)


# ------------------------------------------------------------------ STL 样例
def ascii_stl(tris) -> str:
    out = ["solid part"]
    for a, b, c in tris:
        n = (0.0, 0.0, 1.0)
        out.append(f"  facet normal {n[0]} {n[1]} {n[2]}")
        out.append("    outer loop")
        for p in (a, b, c):
            out.append(f"      vertex {p[0]} {p[1]} {p[2]}")
        out.append("    endloop")
        out.append("  endfacet")
    out.append("endsolid part")
    return "\n".join(out)


def box_tris(x0, y0, z0, x1, y1, z1):
    v = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    faces = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
    ]
    return [(v[a], v[b], v[c]) for a, b, c in faces]


def test_cad_sheets() -> None:
    import cad

    print("[2] CAD 工程视图 / 爆炸图")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "model_shell.stl").write_text(ascii_stl(box_tris(-40, -40, 0, 40, 40, 30)), encoding="utf-8")
        (d / "model_lid.stl").write_text(ascii_stl(box_tris(-38, -38, 30, 38, 38, 36)), encoding="utf-8")
        (d / "model_pcb.stl").write_text(ascii_stl(box_tris(-30, -25, 6, 30, 25, 8)), encoding="utf-8")

        parts = cad.load_parts(d)
        check("读到 3 个零件", len(parts) == 3, f"实际 {len(parts)}")

        sheet = cad.views_sheet(parts, "测试工程视图")
        ok, why = valid_svg(sheet)
        check("四视图图纸合法", ok, why)
        check("四视图含等轴测", "等轴测" in sheet)
        check("四视图含主视", "主视" in sheet)
        check("四视图含俯视", "俯视" in sheet)
        check("四视图含外形尺寸", "外形" in sheet and "mm" in sheet)

        exp = cad.exploded_sheet(parts, "测试爆炸图")
        ok2, why2 = valid_svg(exp)
        check("爆炸图合法", ok2, why2)
        check("爆炸图含引导虚线", "stroke-dasharray" in exp)
        check("爆炸图含零件编号", ">1<" in exp and ">3<" in exp)
        check("爆炸图含尺寸图例", "mm" in exp)

        check("空零件列表返回空串（不生成废图）", cad.views_sheet([]) == "" and cad.exploded_sheet([]) == "")


def test_config_image() -> None:
    import config

    print("[3] 图像网关解析")
    ep, m = config.resolve_image("https://api.siliconflow.cn/v1", "")
    check("siliconflow → FLUX", "siliconflow" in ep and "FLUX" in m, f"{ep} {m}")
    ep, m = config.resolve_image("https://dashscope.aliyuncs.com/compatible-mode/v1", "")
    check("dashscope → wanx", "dashscope" in ep and "wanx" in m, f"{ep} {m}")
    ep, m = config.resolve_image("https://apihub.agnes-ai.com/v1", "")
    check("agnes → agnes-image", "agnes" in ep and "agnes-image" in m, f"{ep} {m}")
    ep, m = config.resolve_image("http://127.0.0.1:11434/v1", "")
    check("自定义网关原样当端点", ep == "http://127.0.0.1:11434/v1", ep)
    ep, m = config.resolve_image("", "")
    check("空配置回落到默认", bool(ep) and bool(m), f"{ep} {m}")

    b, k = config.any_image_key()
    check("any_image_key 返回 (端点, 密钥) 结构", isinstance(b, str) and isinstance(k, str), f"{b} {k}")


def main() -> int:
    test_schematic()
    test_cad_sheets()
    test_config_image()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
