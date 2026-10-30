"""嘉立创 EDA 专业版 `.epro` 的**原理图段**：`SYMBOL/*.esym` + `SHEET/**/*.esch`。

格式依据跟 PCB 段一样，来自用户客户端导出的真实工程（v2.2），不是规范文档。

要点（踩过的坑都记在这）：

- **单位不同**：PCB 段是 mil，原理图段是 **1/100 英寸**（A4 = 1170×825），栅格 10。
  两套单位别混用。
- **网络靠名字连，不靠线几何**：给一根线挂一个属性
  `["ATTR", eid, 线eid, "NET", 网名, 0, 1, x, y, 0, "st1", 0]`
  就是网络标签。真机里 `I2C_SDA` 有 13 根互不相接的短线，全都挂同名 NET ——
  说明编辑器是按名字并网的。**所以每根引脚只画一小截引出线 + 一个 NET 标签**，
  不需要跨图走线，也就没有"线与线相交算不算连通"这个雷。
- `COMPONENT` 行 9 段：`[COMPONENT, eid, 部件名, x, y, 旋转, 镜像, {}, 0]`，
  旋转取 0/90/180/270，镜像 0/1，坐标是该页的绝对坐标。
- `ATTR` 行 12 段：`[ATTR, eid, 宿主eid, 键, 值, 0, 可见, dx, dy, 旋转, 字体, 0]`。
  挂在器件上的坐标是**相对器件原点**的偏移（真机大量是 0,0 = 用符号默认位置）；
  挂在线上的按绝对坐标给（两种解释下都落在图纸内，最坏只是标签偏一点，
  不影响连通 —— 连通靠 ATTR 的宿主 eid，不靠坐标）。
- `PIN` 行 11 段：`[PIN, eid, 1, null, x, y, 线长, 角度, null, 0, 0]`，
  **(x,y) 是外接点**（线要连到这里），线体朝角度方向延伸 线长。
  角度 **0=向右、180=向左、90=向下、270=向上**，这跟标准版 EasyEDA 一致。
- 每个符号文件结尾要有一个 `Designator` 属性（默认位号，如 `R?`），
  页里的器件会覆盖成真实位号。

符号从哪来
----------
优先用**立创官方元件库**（`lib.py`）里的真符号：电阻就是电阻的样子、LED 有三角和
箭头、IC 有引脚名 —— 之前所有器件都画成同一个矩形，用户一眼就看出来了。
库取不到（离线 / 型号搜不到）时退回本文件的 `_fallback_geom()`，
按器件种类画可辨识的图形，**绝不再统一成一个方框**。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import lib as lib_mod

GRID = 10                 # 图纸栅格（1/100 英寸）
PIN_LEN = 20              # 引脚线长（兜底符号）
STUB = 30                 # 从引脚外接点再往外引一小截，用来挂网络标签
X0, Y0 = 150.0, 130.0     # 排版失败时的落点兜底

# 引脚朝向 → 单位向量（外接点往外引的方向）
_DIR = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}


def _uid(text: str) -> str:
    """稳定 uuid：同样的输入永远得到同样的 id，方便增量重生成。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


class _Eid:
    """元素 id 发号器。真机就是 e1、e2、e3… 顺序递增。"""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"e{self.n}"


# ---------------------------------------------------------------- 兜底符号几何

def _pin(num: str, x: float, y: float, rot: int, length: float = 20.0) -> dict[str, Any]:
    return {"num": str(num), "name": str(num), "name_show": True,
            "x": x, "y": y, "rot": rot, "len": length}


def _poly(pts: list[tuple[float, float]], closed: bool = False) -> dict[str, Any]:
    return {"t": "poly", "pts": pts, "closed": closed, "fill": False, "w": 1.0}


def _rect(x: float, y: float, w: float, h: float) -> dict[str, Any]:
    return {"t": "rect", "x": x, "y": y, "w": w, "h": h, "fill": False, "w_": 1.0}


def _ellipse(cx: float, cy: float, rx: float, ry: float) -> dict[str, Any]:
    return {"t": "ellipse", "x": cx, "y": cy, "rx": rx, "ry": ry, "fill": False}


def _fallback_geom(kind: str, pins: list[str]) -> dict[str, Any]:
    """库取不到时画的兜底符号：按**器件种类**给不同外形。

    两脚器件左右各一脚，本体在中间，外形按 kind 区分；多脚器件画成 IC 方框，
    1 脚位置点一个圆点（照顾极性）。
    """
    kind = (kind or "").lower()
    names = [str(p) for p in pins] or ["1", "2"]
    n = len(names)

    if n == 2:
        pl = [_pin(names[0], -40.0, 0.0, 180), _pin(names[1], 40.0, 0.0, 0)]
        items: list[dict[str, Any]] = []
        if kind in ("resistor", "res", "fuse"):
            items.append(_rect(-20, -8, 40, 16))
            if kind.startswith("fuse"):
                items.append(_poly([(0, -8), (0, 8)]))
        elif kind in ("capacitor", "cap"):
            items.append(_poly([(-8, -14), (-8, 14)]))
            items.append(_poly([(8, -14), (8, 14)]))
        elif kind in ("inductor", "inductance"):
            for i in range(3):
                x = -18 + i * 12
                items.append(_poly([(x, 0), (x + 3, -7), (x + 9, -7), (x + 12, 0)]))
        elif kind in ("led",):
            items.append(_poly([(6, -15), (6, 15), (-10, 0)], closed=True))
            items.append(_poly([(-10, -15), (-10, 15)]))
            items.append(_poly([(12, -14), (20, -20)]))        # 发光箭头
            items.append(_poly([(18, -20), (20, -20), (20, -18)]))
        elif kind in ("diode", "schottky", "zener"):
            items.append(_poly([(10, -15), (10, 15), (-8, 0)], closed=True))
            items.append(_poly([(-8, -15), (-8, 15)]))
        elif kind in ("battery", "cell"):
            items.append(_poly([(-8, -18), (-8, 18)]))
            items.append(_poly([(8, -9), (8, 9)]))
            items.append(_poly([(-16, 0), (-8, 0)]))
            items.append(_poly([(8, 0), (16, 0)]))
        elif kind in ("crystal", "oscillator", "xtal"):
            items.append(_rect(-10, -16, 20, 32))
            items.append(_poly([(-16, -12), (-16, 12)]))
            items.append(_poly([(16, -12), (16, 12)]))
        elif kind in ("switch", "button", "key"):
            items.append(_ellipse(-10, -8, 2.5, 2.5))
            items.append(_ellipse(10, -8, 2.5, 2.5))
            items.append(_poly([(-9, -9), (8, -16)]))
        elif kind in ("buzzer", "speaker", "beeper"):
            items.append(_poly([(-12, -14), (4, -14), (12, -6),
                                (12, 6), (4, 14), (-12, 14)], closed=True))
        elif kind in ("motor", "fan"):
            items.append(_ellipse(0, 0, 16, 16))
            items.append(_poly([(-9, 3), (9, -3)]))
        elif kind in ("antenna",):
            items.append(_poly([(0, 6), (0, -6), (-8, -16)]))
            items.append(_poly([(0, -6), (8, -16)]))
        else:
            items.append(_rect(-18, -9, 36, 18))
        geom = {"pins": pl, "items": items}

    else:
        pitch = 20.0 if n <= 16 else 16.0
        half = (n + 1) // 2
        h = max(40.0, half * pitch)
        pl = []
        for i, nm in enumerate(names[:half]):
            y = -(half - 1) / 2.0 * pitch + i * pitch
            pl.append(_pin(nm, -60.0, y, 180))
        for j, nm in enumerate(names[half:]):
            k = len(names[half:]) - 1 - j
            y = -(len(names[half:]) - 1) / 2.0 * pitch + k * pitch
            pl.append(_pin(nm, 60.0, y, 0))
        items = [_rect(-40, -h / 2.0, 80, h)]
        if kind in ("connector", "header", "socket"):
            items = [_rect(-30, -h / 2.0, 60, h)]
            for i in range(n):
                y = -(n - 1) / 2.0 * pitch + i * pitch
                items.append(_rect(-30, y - 4, 10, 8))
        elif kind in ("module", "display", "sensor"):
            # 模块类（屏 / 传感器 / 无线模块）画**双线外框**表示外壳，
            # 跟裸 IC 的单线方框区分开 —— 兜底图形至少不能让人认错种类
            items.append(_rect(-34, -h / 2.0 + 6, 68, h - 12))
            items.append(_ellipse(-30, -h / 2.0 + 8, 3.0, 3.0))
        else:
            items.append(_ellipse(-30, -h / 2.0 + 8, 3.0, 3.0))   # 1 脚标记
        geom = {"pins": pl, "items": items}

    xs = [p["x"] for p in geom["pins"]] + _item_xs(items)
    ys = [p["y"] for p in geom["pins"]] + _item_ys(items)
    geom["bbox"] = [round(min(xs) - 4, 3), round(min(ys) - 4, 3),
                    round(max(xs) + 4, 3), round(max(ys) + 4, 3)]
    geom["w"] = round(max(xs) - min(xs), 3)
    geom["h"] = round(max(ys) - min(ys), 3)
    geom["kind"] = kind
    return geom


def _item_xs(items: list[dict[str, Any]]) -> list[float]:
    out = []
    for it in items:
        if it["t"] == "poly":
            out += [p[0] for p in it["pts"]]
        elif it["t"] == "rect":
            out += [it["x"], it["x"] + it["w"]]
        elif it["t"] == "ellipse":
            out += [it["x"] - it["rx"], it["x"] + it["rx"]]
    return out


def _item_ys(items: list[dict[str, Any]]) -> list[float]:
    out = []
    for it in items:
        if it["t"] == "poly":
            out += [p[1] for p in it["pts"]]
        elif it["t"] == "rect":
            out += [it["y"], it["y"] + it["h"]]
        elif it["t"] == "ellipse":
            out += [it["y"] - it["ry"], it["y"] + it["ry"]]
    return out


def _prefix(ref: str, kind: str) -> str:
    head = "".join(ch for ch in str(ref) if ch.isalpha())
    return (head or ("U" if kind in ("ic", "module", "mcu") else "X"))[:4]


def _fallback_sym_rows(title: str, geom: dict[str, Any], prefix: str) -> list[list]:
    """兜底符号 → `.esym` 行（跟库符号走同一套图元，只是几何自己算）。"""
    eid = _Eid()
    rows: list[list] = [
        ["DOCTYPE", "SYMBOL", "1.1"],
        ["HEAD", {"originX": 0, "originY": 0, "version": "2", "symbolType": 2}],
        ["PART", title, {"BBOX": [round(v, 3) for v in geom["bbox"]]}],
        ["LINESTYLE", "s_line", None, 0, None, 1, 1],
        ["FONTSTYLE", "s_text", None, None, None, 8, 0, 0, 0, 0, 2, 0],
    ]
    for it in geom["items"]:
        if it["t"] == "rect":
            rows.append(["RECT", eid(), round(it["x"], 3), round(it["y"], 3),
                         round(it["x"] + it["w"], 3), round(it["y"] + it["h"], 3),
                         0, 0, 0, "s_line", 0])
        elif it["t"] == "ellipse":
            rows.append(["ELLIPSE", eid(), round(it["x"], 3), round(it["y"], 3),
                         round(it["rx"], 3), round(it["ry"], 3), 0, "s_line", 0])
        else:
            seq: list[float] = []
            for x, y in it["pts"]:
                seq += [round(x, 3), round(y, 3)]
            rows.append(["POLY", eid(), seq, bool(it.get("closed")), "s_line", 0])

    for p in geom["pins"]:
        pe = eid()
        rows.append(["PIN", pe, 1, None, round(p["x"], 3), round(p["y"], 3),
                     round(p.get("len") or PIN_LEN, 3), int(p["rot"]), None, 0, 0])
        rows.append(["ATTR", eid(), pe, "NAME", p["name"], 0, 0, None, None, 0, "s_text", 0])
        rows.append(["ATTR", eid(), pe, "NUMBER", p["num"], 0, 0, None, None, 0, "s_text", 0])
        rows.append(["ATTR", eid(), pe, "Pin Type", "PASSIVE", 0, 0, None, None, 0, "s_text", 0])

    hh = geom["bbox"][3]
    rows.append(["ATTR", eid(), "", "Designator", f"{prefix}?", 0, 1, 0, round(hh + 6, 3), 0, "s_text", 0])
    rows.append(["ATTR", eid(), "", "Name", title, 0, 1, 0, round(-hh - 6, 3), 0, "s_text", 0])
    return rows


# ---------------------------------------------------------------- 排版

# 功能流排序：按信号从「进板」到「外设」的阅读顺序聚团，
# 而不是按位号字母序把电阻和 ESP32 混在一起。
_FLOW = {
    "connector": 0, "header": 0, "socket": 0,
    "battery": 10,
    "ic": 20,
    "module": 30, "sensor": 32,
    "crystal": 40, "resistor": 50, "capacitor": 52, "inductor": 54,
    "diode": 60, "led": 62,
    "switch": 70, "buzzer": 72, "motor": 74, "antenna": 76,
}


def _place(items: list[tuple[str, dict[str, Any]]]) -> dict[str, tuple[float, float]]:
    """在 A4 可打印区内排版。

    旧版按「图纸无限」一路往右铺、超宽才换行，22 个器件直接铺到 y=1211
    （A4 只有 825），打开就是一堆跑出纸外的符号。现在改为：
      1. 按功能流排序，同类聚团；
      2. 从 1 行试到 n 行，取第一个能完整落进 A4 的（行高自适应、行内均布）；
      3. 实在放不下（器件太多）才压缩行距兜底，绝不超纸。
    """
    A4_W, A4_H = 1170.0, 825.0          # Pro 原理图单位 1/100 英寸
    M = 55.0                            # 页边距
    avail_w, avail_h = A4_W - 2 * M, A4_H - 2 * M
    GAP_X, gap_y = 70.0, 60.0

    def order(it: tuple[str, dict[str, Any]]) -> tuple[int, str, int]:
        ref, g = it
        head, num = _sort_key(ref)[0], _sort_key(ref)[1]
        return (_FLOW.get(str(g.get("kind") or ""), 90), head, num)

    items = sorted(items, key=order)
    n = len(items)
    if not n:
        return {}

    def layout(rows_n: int, gap_y: float) -> tuple[list, float, float]:
        per = -(-n // rows_n)
        rowsd = [items[i:i + per] for i in range(0, n, per)]
        need_w = max(sum(g["w"] + 2 * STUB for _, g in r) + GAP_X * (len(r) - 1) for r in rowsd)
        need_h = sum(max(g["h"] for _, g in r) for r in rowsd) + gap_y * (len(rowsd) - 1)
        return rowsd, need_w, need_h

    rowsd, widest, need_h = layout(1, gap_y)
    for rows_n in range(1, n + 1):
        rowsd, widest, need_h = layout(rows_n, gap_y)
        if widest <= avail_w and need_h <= avail_h:
            break
    else:
        # 兜底：器件太多，把行距压到最小，宁可挤也不出纸
        gap_y = max(8.0, (avail_h - sum(max(g["h"] for _, g in r) for r in rowsd)) / max(1, len(rowsd) - 1))
        rowsd, widest, need_h = layout(len(rowsd), gap_y)

    pos: dict[str, tuple[float, float]] = {}
    total_h = sum(max(g["h"] for _, g in r) for r in rowsd) + gap_y * (len(rowsd) - 1)
    y = M + max(0.0, (avail_h - total_h) / 2.0)         # 竖向居中
    for r in rowsd:
        row_h = max(g["h"] for _, g in r)
        widths = [g["w"] + 2 * STUB for _, g in r]
        step = (avail_w - sum(widths)) / max(1, len(r))  # 行内均分剩余空白
        x = M + step / 2.0
        for (ref, _g), w in zip(r, widths):
            pos[ref] = (x + w / 2.0, y + row_h / 2.0)
            x += w + step
        y += row_h + gap_y
    return pos
    return pos


def _sort_key(ref: str) -> tuple[str, int, str]:
    head = "".join(ch for ch in ref if ch.isalpha())
    num = "".join(ch for ch in ref if ch.isdigit())
    return (head, int(num) if num else 0, ref)


# ---------------------------------------------------------------- 装配

def build(net: dict[str, Any], name: str,
          dev_of: dict[str, str],
          lib_of: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """生成原理图段。返回给 `easyeda.write()` 直接塞进 zip / project.json 的东西。

    dev_of: 位号 → PCB 段那份 device 的 uuid（原理图复用同一个 device，
            只是把它的 Symbol 属性填上，这样库元件才是"符号+封装"齐全的）。
    lib_of: 位号 → 立创官方库取回的器件（`lib.resolve()` 的结果）。
            有就用真符号（电阻长得像电阻），没有才走兜底图形。
    """
    lib_of = lib_of or {}
    comps = [c for c in (net.get("components") or []) if str(c.get("id") or "")]
    comps.sort(key=lambda c: _sort_key(str(c.get("id"))))

    # 位号.引脚 → 网名
    pin_net: dict[tuple[str, str], str] = {}
    for n in net.get("nets") or []:
        nm = str(n.get("name") or "")
        if not nm:
            continue
        for pt in n.get("points") or []:
            s = str(pt)
            if "." in s:
                r, p = s.split(".", 1)
                pin_net[(r, p)] = nm

    sym_of: dict[str, str] = {}
    sym_files: dict[str, list[list]] = {}
    sym_index: dict[str, dict[str, Any]] = {}
    geoms: dict[str, dict[str, Any]] = {}
    wire_pins: dict[str, list[tuple[str, float, float, int]]] = {}
    lib_hits = 0

    for c in comps:
        ref = str(c.get("id"))
        pins = [str(p) for p in (c.get("pins") or [])] or ["1", "2"]
        value = str(c.get("value") or "")
        kind = str(c.get("kind") or "")
        part = lib_of.get(ref)
        title = (part.get("mpn") if part else "") or value or kind or ref

        if part:
            geom = dict(part["sym"])
            geom["kind"] = kind
            key = f"lib:{part['lcsc']}"
            lib_hits += 1
        else:
            geom = _fallback_geom(kind, pins)
            key = f"gen:{kind}:{len(pins)}"

        uuid = _uid(f"{name}:sym:{key}:{len(pins)}")
        sym_of[ref] = uuid
        geoms[ref] = geom
        if uuid not in sym_files:
            if part:
                sym_files[uuid] = lib_mod.sym_rows(part, title, _prefix(ref, kind))
            else:
                sym_files[uuid] = _fallback_sym_rows(title, geom, _prefix(ref, kind))
            sym_index[uuid] = {"uuid": uuid, "title": title, "display_title": title,
                               "docType": 2, "type": 2, "description": "",
                               "desc": "", "source": "", "version": "1"}

        # 网表里的引脚名 → 符号上的那个脚（库器件靠 NAME→NUMBER 映射，否则对不上）
        by_num = {p["num"]: p for p in geom["pins"]}
        wp: list[tuple[str, float, float, int]] = []
        for pin in pins:
            pnum = lib_mod.pin_number(part, pin) if part else pin
            p = by_num.get(pnum)
            if p is None and len(wp) < len(geom["pins"]):
                p = geom["pins"][len(wp)]
            if p is None:
                continue
            wp.append((pin, p["x"], p["y"], int(p["rot"])))
        wire_pins[ref] = wp

    pos = _place([(str(c.get("id")), geoms[str(c.get("id"))]) for c in comps])
    rows = _sheet_rows(comps, pos, geoms, sym_of, dev_of, pin_net, wire_pins)

    sch_uuid = _uid(f"{name}:sch")
    sheet_uuid = _uid(f"{name}:sheet1")
    return {
        "sch_uuid": sch_uuid,
        "sheet_uuid": sheet_uuid,
        "sym_of": sym_of,
        "sym_files": sym_files,
        "sym_index": sym_index,
        "sheet_file": f"SHEET/{sch_uuid}/1.esch",
        "sheet_rows": rows,
        "schematic_index": {
            sch_uuid: {"name": "Schematic1", "sheets": [
                {"uuid": sheet_uuid, "id": 1, "name": "MAIN", "display_title": "MAIN"}]},
        },
        "stats": {"components": len(comps), "symbols": len(sym_files),
                  "from_lib": lib_hits,
                  "net_labels": sum(1 for r in rows if r[0] == "ATTR" and r[3] == "NET")},
    }


def _sheet_rows(comps: list[dict[str, Any]],
                pos: dict[str, tuple[float, float]],
                geoms: dict[str, dict[str, Any]],
                sym_of: dict[str, str],
                dev_of: dict[str, str],
                pin_net: dict[tuple[str, str], str],
                wire_pins: dict[str, list[tuple[str, float, float, int]]]) -> list[list]:
    eid = _Eid()
    rows: list[list] = [
        ["DOCTYPE", "SCH", "1.1"],
        ["HEAD", {"originX": 0, "originY": 0, "version": "2"}],
        ["FONTSTYLE", "st1", None, None, None, 8, 0, 0, 0, 0, 2, 0],
        ["LINESTYLE", "st2", None, 0, None, 1, 1],
    ]

    for c in comps:
        ref = str(c.get("id"))
        x, y = pos.get(ref, (X0, Y0))
        geom = geoms[ref]
        ce = eid()
        # 部件名写型号，编辑器里一眼能认出是什么料（之前全是 "ic"/"resistor" 这种种类名）
        label = str(c.get("part") or c.get("value") or c.get("kind") or ref)
        rows.append(["COMPONENT", ce, label, round(x, 3), round(y, 3), 0, 0, {}, 0])
        rows.append(["ATTR", eid(), ce, "Device", dev_of.get(ref, ""),
                     0, 0, None, None, 0, "st1", 0])
        rows.append(["ATTR", eid(), ce, "Symbol", sym_of.get(ref, ""),
                     0, 0, None, None, 0, "st1", 0])
        top = geom["bbox"][3]
        rows.append(["ATTR", eid(), ce, "Designator", ref, 0, 1,
                     0, round(-top - 30, 3), 0, "st1", 0])
        rows.append(["ATTR", eid(), ce, "Name", str(c.get("value") or ""), 0, 1,
                     0, round(top + 14, 3), 0, "st1", 0])

        # 每个引脚：一小截引出线 + 一个 NET 标签。同名 NET 自动并成一条网络。
        for pname, px, py, rot in wire_pins.get(ref, []):
            netname = pin_net.get((ref, pname), "")
            ax, ay = x + px, y + py
            ux, uy = _DIR.get(rot, (1.0, 0.0))
            bx, by = ax + ux * STUB, ay + uy * STUB
            we = eid()
            rows.append(["WIRE", we,
                         [[round(ax, 3), round(ay, 3), round(bx, 3), round(by, 3)]],
                         "st2", 0])
            if netname:
                rows.append(["ATTR", eid(), we, "NET", netname, 0, 1,
                             round(bx, 3), round(by - 12, 3), 0, "st1", 0])
    return rows


def lines(rows: list[list]) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
