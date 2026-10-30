"""生成一个样例 EDA 工程（ESP32-C3 环境监测器），用于人工/CLI 验证产物可打开。

用法：
    python scripts/make_eda_sample.py [输出目录]
默认输出到 workspace/eda-preview/，同时生成 KiCad 工程、SVG 原理图与 netlist.json。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from eda import board as board_mod, kicad, netlist as nl, pcb  # noqa: E402
import schematic as sch_mod  # noqa: E402

SAMPLE = {
    "components": [
        {"id": "U1", "type": "module", "value": "ESP32-C3-MINI-1"},
        {"id": "U2", "type": "ldo", "value": "AMS1117-3.3"},
        {"id": "J1", "type": "usb-c", "value": "TYPE-C 16P"},
        {"id": "D1", "type": "led", "value": "red 0603"},
        {"id": "R1", "type": "resistor", "value": "220ohm"},
        {"id": "R2", "type": "resistor", "value": "10k"},
        {"id": "C1", "type": "capacitor", "value": "10uF"},
        {"id": "C2", "type": "capacitor", "value": "100nF"},
        {"id": "S1", "type": "tact", "value": "KEY"},
        {"id": "U3", "type": "sensor", "value": "BME280"},
    ],
    "connections": [
        {"from": "J1.VBUS", "to": "5V"}, {"from": "J1.GND", "to": "GND"},
        {"from": "U2.VIN", "to": "5V"}, {"from": "U2.GND", "to": "GND"},
        {"from": "U2.VOUT", "to": "3V3"}, {"from": "U1.3V3", "to": "3V3"},
        {"from": "U1.GND", "to": "GND"}, {"from": "U3.VDD", "to": "3V3"},
        {"from": "U3.GND", "to": "GND"},
        {"from": "U1.GPIO18", "to": "U3.SDA"}, {"from": "U1.GPIO19", "to": "U3.SCL"},
        {"from": "U1.GPIO8", "to": "R1.1"}, {"from": "R1.2", "to": "D1.A"},
        {"from": "D1.K", "to": "GND"},
        {"from": "U1.GPIO9", "to": "R2.1"}, {"from": "R2.2", "to": "S1.1"},
        {"from": "S1.2", "to": "GND"},
        {"from": "C1.1", "to": "3V3"}, {"from": "C1.2", "to": "GND"},
        {"from": "C2.1", "to": "3V3"}, {"from": "C2.2", "to": "GND"},
    ],
    "supplies": [{"net": "5V", "voltage": 5.0}, {"net": "3V3", "voltage": 3.3}],
}

# 样例板级约束：圆角矩形，右上留电池禁布区，USB-C 必须落在左边开孔
BOARD = {
    "board": {
        "name": "env-monitor-main-board",
        "shape": "rect",
        "width_mm": 52.0,
        "height_mm": 34.0,
        "corner_radius_mm": 3.0,
        "thickness_mm": 1.6,
        "layers": 2,
    },
    "mounting_holes": [
        {"name": "H1", "x_mm": -22.0, "y_mm": -12.0, "diameter_mm": 3.2, "plated": False},
        {"name": "H2", "x_mm": 22.0, "y_mm": -12.0, "diameter_mm": 3.2, "plated": False},
        {"name": "H3", "x_mm": 22.0, "y_mm": 12.0, "diameter_mm": 3.2, "plated": False},
        {"name": "H4", "x_mm": -22.0, "y_mm": 12.0, "diameter_mm": 3.2, "plated": False},
    ],
    "keepouts": [
        {"name": "电池仓", "x_mm": 12.0, "y_mm": 8.0, "w_mm": 16.0, "h_mm": 12.0,
         "reason": "结构上盖电池腔，板上不可放件"},
    ],
    "height_limit_mm": {"default_mm": 6.0, "zones": [
        {"name": "显示屏下", "x_mm": -10.0, "y_mm": -10.0, "w_mm": 24.0, "h_mm": 8.0, "max_mm": 3.0},
    ]},
    "edge_constraints": [{"ref": "J1", "edge": "left", "offset_mm": 0.0}],
    "notes": "样例数据：壳体内腔 56×38，单边留 2mm 装配余量",
}


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "workspace" / "eda-preview"
    out.mkdir(parents=True, exist_ok=True)

    net = nl.annotate(nl.normalize(SAMPLE, "ESP32-C3 环境监测器 · 样例"))
    (out / "netlist.json").write_text(
        json.dumps(net, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    res = kicad.write(net, out, "schematic")
    ok, size = sch_mod.render_to(net, out / "schematic.svg", "电路原理图 · ESP32-C3 环境监测器")

    board = board_mod.normalize(BOARD)
    (out / "board.json").write_text(
        json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pres = pcb.write(net, board, out, "board")

    print("kicad:", res)
    print("svg:", ok, size)
    print("pcb:", pres, board["stats"])
    print("dir:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
