"""KiCad 工程生成链路自测（纯离线，无第三方依赖）。

跑：python scripts/test_kicad.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from eda import kicad, netlist as nl, parts  # noqa: E402

PASS = 0
FAIL = 0
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


# 一份真实形状的样例网表（ESP32-C3 + LDO + USB-C + 外设）
SAMPLE = {
    "components": [
        {"id": "U1", "type": "module", "value": "ESP32-C3-MINI-1"},
        {"id": "U2", "type": "ldo", "value": "AMS1117-3.3"},
        {"id": "J1", "type": "usb-c", "value": "TYPE-C 16P"},
        {"id": "D1", "type": "led", "value": "red 0603"},
        {"id": "R1", "type": "resistor", "value": "220ohm"},
        {"id": "R2", "type": "resistor", "value": "10k"},
        {"id": "C1", "type": "capacitor", "value": "10uF"},
        {"id": "S1", "type": "tact", "value": "KEY"},
        {"id": "U3", "type": "sensor", "value": "BME280"},
    ],
    "connections": [
        {"from": "J1.VBUS", "to": "5V"}, {"from": "J1.GND", "to": "GND"},
        {"from": "U2.VIN", "to": "5V"}, {"from": "U2.GND", "to": "GND"},
        {"from": "U2.VOUT", "to": "3V3"}, {"from": "U1.3V3", "to": "3V3"},
        {"from": "U1.GND", "to": "GND"}, {"from": "U3.VDD", "to": "3V3"},
        {"from": "U3.GND", "to": "GND"}, {"from": "U1.GPIO18", "to": "U3.SDA"},
        {"from": "U1.GPIO19", "to": "U3.SCL"}, {"from": "U1.GPIO8", "to": "R1.1"},
        {"from": "R1.2", "to": "D1.A"}, {"from": "D1.K", "to": "GND"},
        {"from": "U1.GPIO9", "to": "R2.1"}, {"from": "R2.2", "to": "S1.1"},
        {"from": "S1.2", "to": "GND"}, {"from": "C1.1", "to": "3V3"},
        {"from": "C1.2", "to": "GND"},
    ],
    "supplies": [{"net": "5V", "voltage": 5.0}, {"net": "3V3", "voltage": 3.3}],
}


print("== 器件库 ==")
st = parts.stats()
check("库已加载", st["version"] >= 1)
check("条目 >= 40", st["parts"] >= 40, f"实际 {st['parts']}")
check("kind 覆盖 >= 12 种", len(st["by_kind"]) >= 12, str(st["by_kind"]))
check("LCSC 不编造（全空）", st["with_lcsc"] == 0, f"有 {st['with_lcsc']} 条填了编号")
check("每条都有 match/kind", all(p.get("match") and p.get("kind") for p in parts.all_parts()))
check("ESP32-C3 能匹配", (parts.match({"type": "module", "value": "esp32-c3"}) or {}).get("name") == "ESP32-C3-MINI-1")
check("未知名器件回落 defaults", (parts.match({"type": "zzz", "kind": "resistor"}) or {}).get("footprint", "").startswith("Resistor_SMD"))

print("\n== 网表规范化 ==")
net = nl.normalize(SAMPLE, "测试电路")
check("元件数正确", net["stats"]["components"] == 9, str(net["stats"]))
check("连线数正确", net["stats"]["connections"] == 19, str(net["stats"]))
names = {n["name"] for n in net["nets"]}
check("裸端点成网（3V3/5V/GND）", {"3V3", "5V", "GND"} <= names, str(names))
check("GND 归类为 gnd", next(n["kind"] for n in net["nets"] if n["name"] == "GND") == "gnd")
check("3V3 归类为 power", next(n["kind"] for n in net["nets"] if n["name"] == "3V3") == "power")
check("每个元件都有引脚", all(c["pins"] for c in net["components"]))

messy = nl.normalize({
    "components": [{"type": "resistor"}, {"type": "resistor"}, {"id": "U9"}, {"id": 5}],
    "connections": [{"from": "U9.VCC"}, {"from": "X1.1", "to": "X1.1"}, {"from": "R1.1", "to": "R2.1"}],
}, "脏数据")
check("缺 id 自动补位号", all(c["id"] for c in messy["components"]), str([c["id"] for c in messy["components"]]))
check("重名已消解", len({c["id"] for c in messy["components"]}) == len(messy["components"]))
check("残缺连线被跳过", messy["stats"]["connections"] == 1, str(messy["stats"]))
check("孤立元件给了默认引脚", all(c["pins"] for c in messy["components"]))
check("脏数据不抛异常（有 warnings）", isinstance(messy["warnings"], list))
check("空输入不抛异常", nl.normalize({})["stats"]["components"] == 0)
check("None 输入不抛异常", nl.normalize(None)["stats"]["components"] == 0)

polar = nl.normalize({
    "components": [{"id": "C7", "type": "capacitor"}, {"id": "BT1", "type": "battery"},
                   {"id": "D3", "type": "led"}],
    "connections": [{"from": "C7正极", "to": "BT1+"}, {"from": "C7负极", "to": "GND"},
                    {"from": "D3阳极", "to": "C7正极"}],
}, "极性")
pc = {c["id"]: c for c in polar["components"]}
check("漏点号的极性写法已识别（C7正极）", pc["C7"]["pins"] == ["1", "2"], str(pc["C7"]["pins"]))
check("电池极性保留 +", pc["BT1"]["pins"] == ["+"], str(pc["BT1"]["pins"]))
check("LED 阳极映射到 A", "A" in pc["D3"]["pins"], str(pc["D3"]["pins"]))
check("极性端点回写成 位号.引脚名",
      any("C7.1" in n["points"] and "BT1.+" in n["points"] for n in polar["nets"]),
      str([n["points"] for n in polar["nets"]]))
check("极性网络合并成 2 条", polar["stats"]["nets"] == 2,
      str([(n["name"], n["points"]) for n in polar["nets"]]))
check("孤立元件单独列出",
      nl.normalize({"components": [{"id": "R9", "type": "resistor"}]}, "孤立")["unconnected"] == ["R9"])

print("\n== 器件库补齐 ==")
ann = nl.annotate(nl.normalize(SAMPLE, "测试电路"))
u1 = next(c for c in ann["components"] if c["id"] == "U1")
u3 = next(c for c in ann["components"] if c["id"] == "U3")
r1 = next(c for c in ann["components"] if c["id"] == "R1")
check("模块用库里的真实引脚", len(u1["pins"]) >= 15 and "GPIO18" in u1["pins"], str(u1["pins"][:5]))
check("传感器引脚带 SDA/SCL", {"SDA", "SCL"} <= set(u3["pins"]), str(u3["pins"]))
check("封装已补齐", u1["footprint"] and r1["footprint"], f"{u1['footprint']} / {r1['footprint']}")
check("LCSC 留空不臆造", all(not c.get("lcsc") for c in ann["components"]))
check("补齐计数上报", ann["stats"].get("parts_matched", 0) >= 8, str(ann["stats"]))

print("\n== KiCad 原理图 ==")
sch = kicad.build_schematic(ann, "schematic")
check("括号配对", sch.count("(") == sch.count(")"), f'{sch.count("(")} vs {sch.count(")")}')
check("有版本头", sch.startswith("(kicad_sch") and "(version " in sch[:200])
check("有 lib_symbols 段", "(lib_symbols" in sch)
check("有 sheet_instances", "(sheet_instances" in sch)
check("每个元件都有实例", sch.count("(symbol (lib_id") >= 9, str(sch.count("(symbol (lib_id")))
check("生成了连线", sch.count("(wire") >= 10, str(sch.count("(wire")))
check("有电源符号", 'lib_id "Gen:GND"' in sch and "Gen:+3V3" in sch)
check("悬空脚标了 no_connect", "(no_connect" in sch)
check("uuid 合法", len(UUID_RE.findall(sch)) >= 20, str(len(UUID_RE.findall(sch))))
bad_wire = [
    m.group(1)
    for m in re.finditer(r"\(wire \(pts(.+?)\)\n", sch, re.S)
    if len(set(re.findall(r"\(xy (-?[\d.]+) (-?[\d.]+)\)", m.group(1)))) < 2
]
check("无零长度线段", not bad_wire, str(bad_wire[:2]))
check("坐标全部有限", not re.search(r"(xy|at) (-?nan|inf)", sch, re.I))
check("符号内嵌不引用外部库", not re.search(r'lib_id "(?!Gen:)', sch))

print("\n== 工程文件写盘 ==")
pro = kicad.build_project(ann, "schematic")
try:
    json.loads(pro)
    check(".kicad_pro 是合法 JSON", True)
except Exception as e:  # noqa: BLE001
    check(".kicad_pro 是合法 JSON", False, str(e))

with tempfile.TemporaryDirectory() as td:
    res = kicad.write(ann, Path(td) / "30_hardware", "schematic")
    check("写盘成功", res.get("ok") is True, str(res))
    check("产出工程/原理图/符号库/库表四个文件", len(res.get("files") or []) == 4, str(res.get("files")))
    check("文件非空", all(f["size"] > 100 for f in res.get("files") or []))
    names = {f["name"] for f in res.get("files") or []}
    check("文件名齐全", {"schematic.kicad_pro", "schematic.kicad_sch",
                         "schematic.kicad_sym", "sym-lib-table"} <= names, str(names))

print("\n== 与 KiCad 实际解析器对齐（结构约束）==")
sch_text = kicad.build_schematic(ann, "schematic")


def _blocks(text: str, head: str) -> list[str]:
    """取出所有以 head 开头的顶层块（按括号配对切）。"""
    out, i = [], 0
    while True:
        i = text.find(head, i)
        if i < 0:
            return out
        depth, j = 0, i
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(text[i:j + 1])
        i = j + 1


wires = _blocks(sch_text, "(wire ")
check("每根 wire 恰好两个端点", all(w.count("(xy ") == 2 for w in wires),
                                  f"{sum(1 for w in wires if w.count('(xy ') != 2)} 根异常")
check("没有隐藏语法残留（裸 hide）", not re.search(r"\)\s+hide\b", sch_text), "存在裸 hide")

# 引脚坐标必须落在 1.27 栅格上 —— 偏一点就会「图能开但线是断的」
off_grid = []
for c in ann["components"]:
    for x, y in kicad.symbols.pin_positions(c.get("kind") or "box", c.get("pins") or []).values():
        for v in (x, y):
            if abs(round(v / 1.27) * 1.27 - v) > 1e-6:
                off_grid.append((c["id"], v))
check("符号引脚全部落在 1.27 栅格", not off_grid, str(off_grid[:4]))

print("\n== 异常输入不崩 ==")
for label, payload in (
    ("空字典", {}),
    ("None", None),
    ("字符串字段", {"components": "abc", "connections": 5}),
    ("元件是字符串", {"components": ["U1", "R1"], "connections": []}),
    ("超长值", {"components": [{"id": "R1", "type": "resistor", "value": "x" * 500}], "connections": []}),
):
    try:
        n = nl.annotate(nl.normalize(payload, label))
        s = kicad.build_schematic(n, "x")
        check(f"{label} 能出文件", s.startswith("(kicad_sch") and s.count("(") == s.count(")"))
    except Exception as e:  # noqa: BLE001
        check(f"{label} 能出文件", False, f"{type(e).__name__}: {e}")

print("\n== kicad-cli 真机校验（本机装了 KiCad 才跑）==")
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

cands = [os.environ.get("KICAD_CLI") or ""]
cands += [rf"{d}:\KiCad\{v}\bin\kicad-cli.exe"
          for d in ("C", "D", "E") for v in ("10.0", "9.0", "8.0")]
cands += [rf"C:\Program Files\KiCad\{v}\bin\kicad-cli.exe" for v in ("10.0", "9.0", "8.0")]
cli = next((p for p in cands if p and Path(p).exists()), None) or shutil.which("kicad-cli")

if not cli:
    print("  skip 未找到 kicad-cli（设 KICAD_CLI 环境变量可启用）")
else:
    print(f"  使用 {cli}")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "30_hardware"
        kicad.write(ann, d, "schematic")
        sch = d / "schematic.kicad_sch"
        r = subprocess.run([cli, "sch", "export", "svg", "--output", str(Path(td) / "svg"), str(sch)],
                           capture_output=True)
        check("KiCad 能加载并导出 SVG", r.returncode == 0,
              (r.stdout + r.stderr).decode("gbk", "replace")[:200])

        rpt = Path(td) / "erc.rpt"
        subprocess.run([cli, "sch", "erc", "--severity-all", "--output", str(rpt), str(sch)],
                       capture_output=True)
        body = rpt.read_text(encoding="utf-8", errors="replace") if rpt.exists() else ""
        errs = body.count("; error")
        check("KiCad ERC 无 error", rpt.exists() and errs == 0, f"{errs} 条 error")

print(f"\n通过 {PASS} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
