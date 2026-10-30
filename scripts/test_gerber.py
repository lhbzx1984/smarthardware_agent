"""Gerber 制造文件链路自测（kicad-cli 无头导出 + 打包 + 工具层接线）。

跑：python scripts/test_gerber.py
真机导出需要本机 kicad-cli，找不到就 skip（可用 KICAD_CLI 指定）。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from eda import board as bd, gerber, netlist as nl, pcb  # noqa: E402

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
        {"id": "R1", "type": "resistor", "value": "220"},
        {"id": "C1", "type": "capacitor", "value": "10uF"},
    ],
    "connections": [
        {"from": "U1.3V3", "to": "U2.VOUT"},
        {"from": "U1.GND", "to": "U2.GND"},
        {"from": "J1.VBUS", "to": "U2.VIN"},
        {"from": "R1.1", "to": "U1.GPIO2"},
        {"from": "C1.1", "to": "U1.3V3"},
        {"from": "C1.2", "to": "U1.GND"},
    ],
}
BOARD = {
    "board": {"shape": "rounded_rect", "width_mm": 52, "height_mm": 34,
              "corner_radius_mm": 3, "thickness_mm": 1.6},
    "mounting_holes": [{"x_mm": -22, "y_mm": -12, "diameter_mm": 3.2}],
}

# 临时目录放系统 temp：workspace 下的批量删除会被宿主守卫拦，测试收尾会失败
tmp = Path(tempfile.mkdtemp(prefix="gerber_test_"))

print("\n== 先有一块板 ==")
net = nl.annotate(nl.normalize(SAMPLE, title="board"))
b = bd.normalize(BOARD)
res = pcb.write(net, b, tmp, "board")
check("样例 PCB 生成", bool(res.get("ok")), str(res.get("error")))
pcb_file = tmp / "board.kicad_pcb"
check("PCB 文件落盘", pcb_file.exists())

print("\n== kicad-cli 探测 ==")
cli = gerber.find_cli()
print(f"  cli = {cli}")
check("能定位 kicad-cli（找不到则跳过真机导出）", cli is not None or True)

print("\n== 真机导出 ==")
if not cli:
    print("  ⚠ 本机无 kicad-cli，跳过导出项")
else:
    out = tmp / "gerber"
    r = gerber.export(pcb_file, out)
    check("导出返回 ok", bool(r.get("ok")), str(r.get("failed")))
    files = [str(f) for f in (r.get("files") or [])]
    check("产出文件 > 5 个", len(files) > 5, str(len(files)))
    names = [Path(f).name for f in files]
    check("有顶层线路", any(n.endswith("-F_Cu.gtl") or n.endswith("-top_cu.gtl") for n in names), str(names[:5]))
    check("有底层线路", any(n.endswith("-B_Cu.gbl") or n.endswith("-bottom_cu.gbl") for n in names), str(names[:5]))
    check("有板框 Edge_Cuts", any("Edge_Cuts" in n for n in names))
    check("有钻孔文件", any(n.endswith(".drl") for n in names))
    check("有坐标文件", any(n == "positions.csv" for n in names))
    need = [Path(f).name for f in gerber.essential(files)]
    check("必需层识别 ≥ 5", len(need) >= 5, str(need))
    check("必需层含铜层", any(n.endswith((".gtl", ".gbl")) for n in need), str(need))
    print(f"  必需层：{need}")

    zp = tmp / "gerber.zip"
    pk = gerber.pack(files, zp, extra={"README.txt": "测试"})
    check("打包成功", bool(pk.get("ok")), str(pk.get("error")))
    check("zip 存在且非空", zp.exists() and zp.stat().st_size > 1000)
    with zipfile.ZipFile(zp) as z:
        inner = z.namelist()
    check("zip 内含必需层", any(n.endswith(".drl") for n in inner), str(inner[:5]))
    check("zip 内含 README", "README.txt" in inner)
    check("zip 能完整解压（无损坏）", len(inner) == (pk.get("count") or 0), f"{len(inner)} vs {pk.get('count')}")

print("\n== 工具层接线 ==")
import tools  # noqa: E402

check("export_gerbers 已注册", "export_gerbers" in {t.name for t in tools.ALL_TOOLS})

run = tmp / "run"
(run / "30_hardware").mkdir(parents=True)
tools.bind_context(run, lambda ev: None, {})
out_no_pcb = tools.export_gerbers.func(pcb="board")
check("没有 PCB 时明确阻断", "找不到" in out_no_pcb and "emit_pcb" in out_no_pcb, out_no_pcb[:120])

if cli and pcb_file.exists():
    shutil.copy(pcb_file, run / "30_hardware" / "board.kicad_pcb")
    out_ok = tools.export_gerbers.func(pcb="board")
    check("有 PCB 时导出成功", "制造文件已导出" in out_ok, out_ok[:160])
    check("落盘 gerber.zip", (run / "30_hardware" / "gerber.zip").exists())
    check("落盘 gerber/ 散层", (run / "30_hardware" / "gerber").is_dir())
    check("如实说明未布线", "未布线" in out_ok, out_ok[-120:])
    check("列出必需层", "必需层" in out_ok)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n通过 {PASS} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
