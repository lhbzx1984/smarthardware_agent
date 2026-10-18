"""网表 → 电路原理图 SVG（纯离线，零第三方依赖）。

为什么不用现成库（schemdraw / netlistsvg）：它们要么要 Node 运行时，要么依赖
graphviz 二进制，在 Windows 上装不上就得整条硬件链路瘫痪。这里手写布局与符号，
几百行换来"任何机器上都一定出图"。

布局策略是按 Arduino 类小产品的实际网表形状设计的：
  - 顶部一条电源轨（5V / 3V3 / VIN），底部一条 GND 轨
  - 元件的电源脚垂直向上接电源轨，地脚垂直向下接 GND 轨
  - 信号脚在左右两侧，net 内用曼哈顿折线（H-V-H）连接
这样出来的图接近 datasheet 里的参考设计，而不是随机拓扑图。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

GRID = 20
COL_W = 230          # 列间距
ROW_H = 200          # 行间距
MARGIN = 90          # 左右留白
RAIL_TOP = 70        # 电源轨 y
COL_COUNT = 4        # 每行元件数

INK = "#1f2933"
WIRE = "#33475b"
ACCENT = "#0f7b6c"
RAIL_5V = "#c0392b"
RAIL_GND = "#2c3e50"
LABEL = "#52606d"
GRID_LINE = "#e4e7eb"

POWER_RE = re.compile(r"(^|\.)(VCC|VDD|VIN|VBUS|5V|3V3|3\.3V|\+5V|\+3V3)$", re.I)
GND_RE = re.compile(r"(^|\.)(GND|GROUND|VSS|AGND|DGND)$", re.I)

# 元件类型 → (显示类型, 引脚数下限)。识别不到就按盒子画。
TYPE_ALIASES = {
    "resistor": "resistor", "res": "resistor", "r": "resistor",
    "capacitor": "capacitor", "cap": "capacitor", "c": "capacitor",
    "electrolytic": "capacitor", "ceramic": "capacitor",
    "led": "led", "diode": "diode", "d": "diode",
    "schottky": "diode", "zener": "diode",
    "inductor": "inductor", "l": "inductor",
    "crystal": "crystal", "xtal": "crystal", "oscillator": "crystal",
    "switch": "switch", "button": "switch", "tact": "switch",
    "transistor": "transistor", "mosfet": "transistor", "bjt": "transistor",
    "connector": "connector", "header": "connector", "jst": "connector",
    "terminal": "connector", "jack": "connector", "usb": "connector",
    "sensor": "ic", "module": "ic", "mcu": "ic", "ic": "ic", "u": "ic",
    "regulator": "ic", "ldo": "ic", "amp": "ic", "driver": "ic",
    "motor": "motor", "servo": "motor", "relay": "relay", "fuse": "fuse",
    "battery": "battery", "speaker": "speaker", "buzzer": "speaker",
}


def _kind(comp: dict[str, Any]) -> str:
    raw = str(comp.get("type") or comp.get("kind") or "").strip().lower()
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    head = re.sub(r"[^a-z]", "", raw)
    if head in TYPE_ALIASES:
        return TYPE_ALIASES[head]
    cid = str(comp.get("id") or "").upper()
    if cid.startswith("R"):
        return "resistor"
    if cid.startswith("C"):
        return "capacitor"
    if cid.startswith("D"):
        return "led" if "led" in raw or "led" in str(comp.get("note", "")).lower() else "diode"
    if cid.startswith("L"):
        return "inductor"
    if cid.startswith("J") or cid.startswith("P") or cid.startswith("CN"):
        return "connector"
    if cid.startswith("Q"):
        return "transistor"
    if cid.startswith("U") or cid.startswith("IC"):
        return "ic"
    if cid.startswith("Y") or cid.startswith("X"):
        return "crystal"
    if cid.startswith("S") or cid.startswith("K"):
        return "switch"
    if cid.startswith("M"):
        return "motor"
    return "box"


# ---------------------------------------------------------------- 引脚抽取
def _pins_of(comps: dict[str, dict], conns: list[dict]) -> dict[str, list[str]]:
    """每个元件涉及的引脚名（去掉元件前缀），按自然序排。"""
    pins: dict[str, set[str]] = {cid: set() for cid in comps}
    for c in conns:
        for end in (c.get("from"), c.get("to")):
            if not end or "." not in end:
                continue
            cid, pin = end.split(".", 1)
            if cid in pins:
                pins[cid].add(pin)

    def key(p: str) -> tuple[int, Any]:
        low = p.lower()
        for i, name in enumerate(("vcc", "vin", "5v", "3v3", "vdd", "vbus")):
            if low == name:
                return (0, i)
        if low in ("gnd", "vss"):
            return (2, 0)
        n = re.sub(r"\D", "", p)
        return (1, int(n) if n else 999)

    return {cid: sorted(v, key=key) for cid, v in pins.items() if v}


def _nets(comps: dict[str, dict], conns: list[dict]) -> list[list[tuple[str, str]]]:
    """并查集聚合：把引脚级连线合成一张张 net。"""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for c in conns:
        a, b = c.get("from"), c.get("to")
        if a and b:
            union(a, b)

    groups: dict[str, list[str]] = {}
    for node in list(parent):
        groups.setdefault(find(node), []).append(node)
    out = []
    for members in groups.values():
        pts = []
        for m in sorted(set(members)):
            if "." in m:
                cid, pin = m.split(".", 1)
                if cid in comps:
                    pts.append((cid, pin))
        if len(pts) >= 2:
            out.append(pts)
    return out


# ---------------------------------------------------------------- 符号绘制
def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _sym_resistor(x: float, y: float, w: float) -> str:
    bw, bh = w * 0.42, 18
    return (
        f'<rect x="{x - bw/2:.1f}" y="{y - bh/2:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
        f'fill="#fff" stroke="{INK}" stroke-width="1.6" rx="2"/>'
    )


def _sym_capacitor(x: float, y: float, w: float) -> str:
    g, hh = 9, 15
    return (
        f'<line x1="{x - g/2:.1f}" y1="{y - hh:.1f}" x2="{x - g/2:.1f}" y2="{y + hh:.1f}" stroke="{INK}" stroke-width="2.4"/>'
        f'<line x1="{x + g/2:.1f}" y1="{y - hh:.1f}" x2="{x + g/2:.1f}" y2="{y + hh:.1f}" stroke="{INK}" stroke-width="2.4"/>'
    )


def _sym_led(x: float, y: float, w: float, filled: bool = True) -> str:
    s = 13
    tri = f"{x - s:.1f},{y - s:.1f} {x - s:.1f},{y + s:.1f} {x + s*0.7:.1f},{y:.1f}"
    body = (
        f'<polygon points="{tri}" fill="{ACCENT if filled else "#fff"}" stroke="{INK}" stroke-width="1.6"/>'
        f'<line x1="{x + s*0.7:.1f}" y1="{y - s:.1f}" x2="{x + s*0.7:.1f}" y2="{y + s:.1f}" stroke="{INK}" stroke-width="1.8"/>'
    )
    if filled:
        body += (
            f'<line x1="{x - s*1.5:.1f}" y1="{y - s*1.4:.1f}" x2="{x - s*0.4:.1f}" y2="{y - s*2.1:.1f}" stroke="{ACCENT}" stroke-width="1.4" marker-end="url(#arrow)"/>'
            f'<line x1="{x - s*1.9:.1f}" y1="{y - s*0.7:.1f}" x2="{x - s*0.8:.1f}" y2="{y - s*1.4:.1f}" stroke="{ACCENT}" stroke-width="1.4" marker-end="url(#arrow)"/>'
        )
    return body


def _sym_diode(x: float, y: float, w: float) -> str:
    return _sym_led(x, y, w, filled=False)


def _sym_inductor(x: float, y: float, w: float) -> str:
    n, r, span = 4, 7, w * 0.44
    parts = []
    for i in range(n):
        cx = x - span / 2 + span * (i + 0.5) / n
        parts.append(f'<path d="M {cx - r/2:.1f} {y:.1f} a {r/2:.1f} {r/2:.1f} 0 0 1 {r:.1f} 0" fill="none" stroke="{INK}" stroke-width="1.6"/>')
    return "".join(parts)


def _sym_crystal(x: float, y: float, w: float) -> str:
    bw, bh = 20, 22
    return (
        f'<rect x="{x - bw/2:.1f}" y="{y - bh/2:.1f}" width="{bw}" height="{bh}" fill="#fff" stroke="{INK}" stroke-width="1.6"/>'
        f'<line x1="{x - bw/2 - 4:.1f}" y1="{y:.1f}" x2="{x - bw/2:.1f}" y2="{y:.1f}" stroke="{INK}" stroke-width="1.6"/>'
        f'<line x1="{x + bw/2:.1f}" y1="{y:.1f}" x2="{x + bw/2 + 4:.1f}" y2="{y:.1f}" stroke="{INK}" stroke-width="1.6"/>'
        f'<line x1="{x - 6:.1f}" y1="{y - bh/2 - 5:.1f}" x2="{x + 6:.1f}" y2="{y - bh/2 - 5:.1f}" stroke="{INK}" stroke-width="1.6"/>'
    )


def _sym_switch(x: float, y: float, w: float) -> str:
    return (
        f'<circle cx="{x - 16:.1f}" cy="{y:.1f}" r="3" fill="{INK}"/>'
        f'<circle cx="{x + 16:.1f}" cy="{y:.1f}" r="3" fill="{INK}"/>'
        f'<line x1="{x - 16:.1f}" y1="{y:.1f}" x2="{x + 12:.1f}" y2="{y - 11:.1f}" stroke="{INK}" stroke-width="1.8"/>'
    )


def _sym_transistor(x: float, y: float, w: float) -> str:
    return (
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="none" stroke="{INK}" stroke-width="1.6"/>'
        f'<line x1="{x:.1f}" y1="{y - 9:.1f}" x2="{x:.1f}" y2="{y + 9:.1f}" stroke="{INK}" stroke-width="1.8"/>'
        f'<line x1="{x:.1f}" y1="{y - 4:.1f}" x2="{x + 12:.1f}" y2="{y - 11:.1f}" stroke="{INK}" stroke-width="1.6"/>'
        f'<line x1="{x:.1f}" y1="{y - 4:.1f}" x2="{x + 12:.1f}" y2="{y + 6:.1f}" stroke="{INK}" stroke-width="1.6"/>'
        f'<path d="M {x + 12:.1f} {y - 11:.1f} L {x + 17:.1f} {y - 13:.1f}" stroke="{INK}" stroke-width="1.6"/>'
    )


def _sym_connector(x: float, y: float, w: float, pins: int) -> str:
    bh = max(24, pins * 15)
    return (
        f'<rect x="{x - 11:.1f}" y="{y - bh/2:.1f}" width="22" height="{bh:.1f}" fill="#fff" stroke="{INK}" stroke-width="1.6" rx="2"/>'
        + "".join(
            f'<line x1="{x - 11:.1f}" y1="{y - bh/2 + (i + 0.5) * bh / max(pins,1):.1f}" '
            f'x2="{x - 3:.1f}" y2="{y - bh/2 + (i + 0.5) * bh / max(pins,1):.1f}" stroke="{INK}" stroke-width="1.2"/>'
            for i in range(max(pins, 1))
        )
    )


def _sym_motor(x: float, y: float, w: float) -> str:
    return (
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="#fff" stroke="{INK}" stroke-width="1.6"/>'
        f'<text x="{x:.1f}" y="{y + 5:.1f}" font-size="13" text-anchor="middle" fill="{INK}" font-family="monospace">M</text>'
    )


def _sym_speaker(x: float, y: float, w: float) -> str:
    return (
        f'<rect x="{x - 14:.1f}" y="{y - 9:.1f}" width="14" height="18" fill="{INK}"/>'
        f'<path d="M {x:.1f} {y - 12:.1f} L {x + 12:.1f} {y - 22:.1f} L {x + 12:.1f} {y + 22:.1f} L {x:.1f} {y + 12:.1f} Z" fill="none" stroke="{INK}" stroke-width="1.6"/>'
    )


def _sym_fuse(x: float, y: float, w: float) -> str:
    return (
        f'<rect x="{x - 16:.1f}" y="{y - 7:.1f}" width="32" height="14" fill="#fff" stroke="{INK}" stroke-width="1.6"/>'
        f'<line x1="{x - 10:.1f}" y1="{y:.1f}" x2="{x + 10:.1f}" y2="{y:.1f}" stroke="{INK}" stroke-width="1.6"/>'
    )


def _sym_battery(x: float, y: float, w: float) -> str:
    return (
        f'<line x1="{x - 10:.1f}" y1="{y - 14:.1f}" x2="{x + 10:.1f}" y2="{y - 14:.1f}" stroke="{INK}" stroke-width="2.4"/>'
        f'<line x1="{x - 4:.1f}" y1="{y - 4:.1f}" x2="{x + 4:.1f}" y2="{y - 4:.1f}" stroke="{INK}" stroke-width="2.4"/>'
        f'<line x1="{x - 10:.1f}" y1="{y + 6:.1f}" x2="{x + 10:.1f}" y2="{y + 6:.1f}" stroke="{INK}" stroke-width="2.4"/>'
    )


def _sym_ic(x: float, y: float, w: float, h: float, name: str) -> str:
    return (
        f'<rect x="{x - w/2:.1f}" y="{y - h/2:.1f}" width="{w:.1f}" height="{h:.1f}" fill="#fff" '
        f'stroke="{INK}" stroke-width="1.8" rx="3"/>'
        f'<text x="{x:.1f}" y="{y + 4:.1f}" font-size="12" text-anchor="middle" fill="{INK}" '
        f'font-family="monospace" font-weight="bold">{_esc(name)}</text>'
    )


def _sym_box(x: float, y: float, w: float, name: str) -> str:
    return (
        f'<rect x="{x - 26:.1f}" y="{y - 16:.1f}" width="52" height="32" fill="#fff" stroke="{INK}" stroke-width="1.6" rx="3"/>'
        f'<text x="{x:.1f}" y="{y + 4:.1f}" font-size="11" text-anchor="middle" fill="{INK}" font-family="monospace">{_esc(name)}</text>'
    )


# ---------------------------------------------------------------- 主渲染
def render(netlist: dict[str, Any], title: str = "电路原理图") -> str:
    """把 netlist 渲染成 SVG 字符串。缺字段时尽力而为，绝不抛异常。"""
    comps_list = [c for c in (netlist.get("components") or []) if c.get("id")]
    comps = {str(c.get("id")): c for c in comps_list}
    conns = [c for c in (netlist.get("connections") or []) if c.get("from") and c.get("to")]
    supplies = netlist.get("supplies") or []

    if not comps:
        return _empty_svg(title, "网表里没有元件")

    pins = _pins_of(comps, conns)

    # ---- 元件分域：电源脚/地脚单独处理，其余落在中间网格
    grid_comp, rail_power, rail_gnd = [], [], []
    for cid, c in comps.items():
        ps = pins.get(cid) or []
        if any(POWER_RE.search(p) for p in ps) and len(ps) <= 2 and _kind(c) not in ("ic", "connector"):
            rail_power.append(cid)
        elif any(GND_RE.search(p) for p in ps) and len(ps) <= 2 and _kind(c) not in ("ic", "connector"):
            rail_gnd.append(cid)
        else:
            grid_comp.append(cid)

    # 布局：中间网格
    pos: dict[str, tuple[float, float]] = {}
    for i, cid in enumerate(grid_comp):
        col, row = i % COL_COUNT, i // COL_COUNT
        pos[cid] = (MARGIN + col * COL_W + COL_W / 2, RAIL_TOP + 130 + row * ROW_H)

    rows = max(1, math.ceil(len(grid_comp) / COL_COUNT))
    body_bottom = RAIL_TOP + 130 + (rows - 1) * ROW_H + 60
    gnd_y = body_bottom + 90

    # 电源/地轨上的元件（去耦电容等）排在左右两侧
    for i, cid in enumerate(rail_power):
        pos[cid] = (MARGIN - 60 + (i % 2) * 40, RAIL_TOP + 40 + (i // 2) * 46)
    for i, cid in enumerate(rail_gnd):
        pos[cid] = (MARGIN - 60 + (i % 2) * 40, gnd_y - 40 - (i // 2) * 46)

    # ---- 引脚锚点
    anchors: dict[str, tuple[float, float]] = {}

    def place(cid: str) -> None:
        c = comps[cid]
        x, y = pos[cid]
        ps = pins.get(cid) or []
        k = _kind(c)
        if k == "ic":
            n = max(4, len(ps))
            half = math.ceil(n / 2)
            h = max(56, half * 26 + 16)
            w = 104
            for idx, p in enumerate(ps):
                if POWER_RE.search(p):
                    anchors[f"{cid}.{p}"] = (x, y - h / 2 - 14)
                elif GND_RE.search(p):
                    anchors[f"{cid}.{p}"] = (x, y + h / 2 + 14)
                elif idx % 2 == 0:
                    anchors[f"{cid}.{p}"] = (x - w / 2 - 14, y - h / 2 + 16 + (idx // 2) * 26)
                else:
                    anchors[f"{cid}.{p}"] = (x + w / 2 + 14, y - h / 2 + 16 + ((idx - 1) // 2) * 26)
        elif k == "connector":
            n = max(2, len(ps))
            bh = max(24, n * 15)
            for idx, p in enumerate(ps):
                if POWER_RE.search(p):
                    anchors[f"{cid}.{p}"] = (x, y - bh / 2 - 14)
                elif GND_RE.search(p):
                    anchors[f"{cid}.{p}"] = (x, y + bh / 2 + 14)
                else:
                    anchors[f"{cid}.{p}"] = (x - 25, y - bh / 2 + (idx + 0.5) * bh / n)
        else:
            # 两引脚器件：左进右出；电源脚朝上、地脚朝下
            half = 46
            for idx, p in enumerate(ps):
                if POWER_RE.search(p):
                    anchors[f"{cid}.{p}"] = (x, y - 34)
                elif GND_RE.search(p):
                    anchors[f"{cid}.{p}"] = (x, y + 34)
                else:
                    anchors[f"{cid}.{p}"] = (x - half if idx % 2 == 0 else x + half, y)

    for cid in comps:
        if cid in pos:
            place(cid)

    width = int(max(MARGIN * 2 + COL_COUNT * COL_W, 720))
    height = int(gnd_y + 120)
    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        f'<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">'
        f'<path d="M0,0 L6,3 L0,6 Z" fill="{ACCENT}"/></marker></defs>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]

    # 背景网格
    grid = []
    for gx in range(0, width, GRID):
        grid.append(f"M {gx} 0 L {gx} {height}")
    for gy in range(0, height, GRID):
        grid.append(f"M 0 {gy} L {width} {gy}")
    svg.append(f'<path d="{"".join(grid)}" stroke="{GRID_LINE}" stroke-width="0.5" fill="none"/>')

    # 标题
    svg.append(
        f'<text x="{MARGIN - 50}" y="34" font-size="17" font-weight="bold" fill="{INK}" '
        f'font-family="system-ui,sans-serif">{_esc(title)}</text>'
    )
    n_comp, n_net = len(comps), len(_nets(comps, conns))
    svg.append(
        f'<text x="{MARGIN - 50}" y="54" font-size="11" fill="{LABEL}" font-family="monospace">'
        f'{n_comp} 元件 · {len(conns)} 连线 · {n_net} 网络</text>'
    )

    # ---- 电源轨 / 地轨
    rail_names = [str(s.get("net") or "") for s in supplies if isinstance(s, dict)]
    if not rail_names:
        rail_names = ["5V"] if any(POWER_RE.search(p) for cid in pins for p in pins[cid]) else []
    for i, rn in enumerate(rail_names[:3]):
        ry = RAIL_TOP + i * 0  # 单轨：多条电源也画在同一条上，用标签区分
        svg.append(f'<line x1="{MARGIN - 70}" y1="{RAIL_TOP}" x2="{width - 40}" y2="{RAIL_TOP}" stroke="{RAIL_5V}" stroke-width="2.4"/>')
        svg.append(
            f'<text x="{width - 36}" y="{RAIL_TOP + 4}" font-size="12" fill="{RAIL_5V}" '
            f'font-family="monospace" font-weight="bold">{_esc(rn)}</text>'
        )
        svg.append(f'<circle cx="{MARGIN - 70}" cy="{RAIL_TOP}" r="3.5" fill="{RAIL_5V}"/>')
    svg.append(f'<line x1="{MARGIN - 70}" y1="{gnd_y}" x2="{width - 40}" y2="{gnd_y}" stroke="{RAIL_GND}" stroke-width="2.6"/>')
    svg.append(f'<text x="{width - 36}" y="{gnd_y - 8}" font-size="12" fill="{RAIL_GND}" font-family="monospace" font-weight="bold">GND</text>')
    for gx in range(MARGIN - 60, width - 40, 46):
        svg.append(f'<line x1="{gx}" y1="{gnd_y}" x2="{gx - 6}" y2="{gnd_y + 9}" stroke="{RAIL_GND}" stroke-width="1.5"/>')
        svg.append(f'<line x1="{gx}" y1="{gnd_y}" x2="{gx + 6}" y2="{gnd_y + 9}" stroke="{RAIL_GND}" stroke-width="1.5"/>')

    # ---- 连线（先画线后画元件，元件盖在上面）
    wires: list[str] = []
    for net in _nets(comps, conns):
        pts = [anchors[f"{cid}.{pin}"] for cid, pin in net if f"{cid}.{pin}" in anchors]
        if len(pts) < 2:
            continue
        # 电源/地 net：各点垂直上拉/下拉到轨，不画折线
        is_pwr = any(POWER_RE.search(p) for _, p in net)
        is_gnd = any(GND_RE.search(p) for _, p in net)
        if is_pwr or is_gnd:
            ry = RAIL_TOP if is_pwr else gnd_y
            color = RAIL_5V if is_pwr else RAIL_GND
            for px, py in pts:
                wires.append(
                    f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{px:.1f}" y2="{ry:.1f}" '
                    f'stroke="{color}" stroke-width="1.6"/>'
                )
                wires.append(f'<circle cx="{px:.1f}" cy="{ry:.1f}" r="2.6" fill="{color}"/>')
            continue
        # 信号 net：以重心为 hub 的星型 + 曼哈顿折线
        hx = sum(p[0] for p in pts) / len(pts)
        hy = sum(p[1] for p in pts) / len(pts)
        for px, py in pts:
            mx = (px + hx) / 2
            wires.append(
                f'<polyline points="{px:.1f},{py:.1f} {mx:.1f},{py:.1f} {mx:.1f},{hy:.1f} {hx:.1f},{hy:.1f}" '
                f'fill="none" stroke="{WIRE}" stroke-width="1.5"/>'
            )
        wires.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="2.8" fill="{WIRE}"/>')
    svg += wires

    # ---- 元件
    for cid, c in comps.items():
        if cid not in pos:
            continue
        x, y = pos[cid]
        k = _kind(c)
        ps = pins.get(cid) or []
        label = str(c.get("id"))
        value = str(c.get("value") or c.get("note") or "")[:18]

        # 引脚引线
        for p in ps:
            a = anchors.get(f"{cid}.{p}")
            if not a:
                continue
            ax, ay = a
            if abs(ax - x) > abs(ay - y):
                sx = x - math.copysign(24, x - ax)
                svg.append(f'<line x1="{sx:.1f}" y1="{y:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" stroke="{INK}" stroke-width="1.5"/>')
            else:
                sy = y - math.copysign(30, y - ay)
                svg.append(f'<line x1="{x:.1f}" y1="{sy:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" stroke="{INK}" stroke-width="1.5"/>')

        body = ""
        if k == "resistor":
            body = _sym_resistor(x, y, 60)
        elif k == "capacitor":
            body = _sym_capacitor(x, y, 60)
        elif k == "led":
            body = _sym_led(x, y, 60)
        elif k == "diode":
            body = _sym_diode(x, y, 60)
        elif k == "inductor":
            body = _sym_inductor(x, y, 60)
        elif k == "crystal":
            body = _sym_crystal(x, y, 60)
        elif k == "switch":
            body = _sym_switch(x, y, 60)
        elif k == "transistor":
            body = _sym_transistor(x, y, 60)
        elif k == "connector":
            body = _sym_connector(x, y, 60, max(2, len(ps)))
        elif k == "motor":
            body = _sym_motor(x, y, 60)
        elif k in ("speaker", "buzzer"):
            body = _sym_speaker(x, y, 60)
        elif k == "fuse":
            body = _sym_fuse(x, y, 60)
        elif k == "battery":
            body = _sym_battery(x, y, 60)
        elif k == "ic":
            n = max(4, len(ps))
            half = math.ceil(n / 2)
            hh = max(56, half * 26 + 16)
            body = _sym_ic(x, y, 104, hh, label)
        else:
            body = _sym_box(x, y, 60, label)
        svg.append(body)

        # 标签：ID + 值
        ty = y - 40 if k == "ic" else y - 30
        svg.append(
            f'<text x="{x:.1f}" y="{ty:.1f}" font-size="12" text-anchor="middle" fill="{INK}" '
            f'font-family="monospace" font-weight="bold">{_esc(label)}</text>'
        )
        if value:
            svg.append(
                f'<text x="{x:.1f}" y="{y + (46 if k == "ic" else 34):.1f}" font-size="10.5" '
                f'text-anchor="middle" fill="{LABEL}" font-family="monospace">{_esc(value)}</text>'
            )

    svg.append("</svg>")
    return "".join(svg)


def _empty_svg(title: str, msg: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 200" width="640" height="200">'
        f'<rect width="640" height="200" fill="#fff"/>'
        f'<text x="320" y="90" font-size="16" text-anchor="middle" fill="{INK}" font-family="system-ui">{_esc(title)}</text>'
        f'<text x="320" y="118" font-size="12" text-anchor="middle" fill="{LABEL}" font-family="monospace">{_esc(msg)}</text>'
        f"</svg>"
    )


def render_to(netlist: dict[str, Any], dest: Path, title: str = "电路原理图") -> tuple[bool, int]:
    """渲染并写盘。返回 (成功, 字节数)。"""
    try:
        svg = render(netlist, title)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(svg, encoding="utf-8")
        return True, dest.stat().st_size
    except Exception:  # noqa: BLE001  画图失败不能拖垮整个硬件阶段
        return False, 0


def load_netlist(path: Path) -> dict[str, Any] | None:
    """宽容读取：JSON 坏了返回 None，让调用方决定怎么报。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
