"""网表规范化 —— EDA 链路的唯一事实源。

模型产出的 netlist.json 字段可能缺、可能拼错（type 写成 "MCU 模块"、"5V 电源"），
这里做三件事，让下游（KiCad writer / SVG 渲染 / ERC / BOM）拿到的永远是同一份结构化数据：

1. 补全：id / kind / value / footprint 缺失时按器件库与位号前缀推断
2. 归一：引脚排序、网络命名、电源与地识别
3. 聚合：并查集把引脚级连线合成 net（支持 "U1.3V3" → "3V3" 这类裸网络端点）

铁律：**宽容**。任何奇怪输入都不许抛异常 —— 硬件阶段已经够容易失败了，
规范化这一步绝不能成为新的失败点。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

NETLIST_VERSION = 1

POWER_RE = re.compile(r"(^|[.\-_])(VCC|VDD|VIN|VBUS|VBAT|5V|3V3|3\.3V|\+5V|\+3V3|\+3\.3V|1V8|1\.8V)$", re.I)
GND_RE = re.compile(r"(^|[.\-_])(GND|GROUND|VSS|AGND|DGND|EP|EPAD)$", re.I)

# type 字段 → 规范 kind。匹配不到就退回位号前缀，再不行就是 box。
KIND_ALIASES: dict[str, str] = {
    "resistor": "resistor", "res": "resistor", "r": "resistor",
    "capacitor": "capacitor", "cap": "capacitor", "c": "capacitor",
    "electrolytic": "capacitor", "ceramic": "capacitor", "tantalum": "capacitor",
    "led": "led", "diode": "diode", "d": "diode", "schottky": "diode", "zener": "diode", "tvs": "diode",
    "inductor": "inductor", "l": "inductor", "ferrite": "inductor", "bead": "inductor",
    "crystal": "crystal", "xtal": "crystal", "oscillator": "crystal",
    "switch": "switch", "button": "switch", "tact": "switch", "key": "switch", "encoder": "switch",
    "transistor": "transistor", "mosfet": "transistor", "bjt": "transistor", "nmos": "transistor", "pmos": "transistor",
    "connector": "connector", "header": "connector", "jst": "connector", "terminal": "connector",
    "jack": "connector", "usb": "connector", "socket": "connector",
    "sensor": "ic", "module": "module", "mcu": "ic", "ic": "ic", "u": "ic",
    "regulator": "ic", "ldo": "ic", "amp": "ic", "driver": "ic", "optocoupler": "ic",
    "motor": "motor", "servo": "motor", "relay": "relay", "fuse": "fuse",
    "battery": "battery", "speaker": "speaker", "buzzer": "speaker", "antenna": "antenna",
}

# kind → 位号前缀（模型没给 id 时用来生成）
KIND_PREFIX: dict[str, str] = {
    "resistor": "R", "capacitor": "C", "inductor": "L", "led": "D", "diode": "D",
    "crystal": "Y", "switch": "S", "transistor": "Q", "connector": "J",
    "ic": "U", "module": "U", "motor": "M", "relay": "K", "fuse": "F",
    "battery": "B", "speaker": "SP", "antenna": "A", "box": "X",
}

# kind → 完全没有连线时的默认引脚（保证符号至少有脚可画）
DEFAULT_PINS: dict[str, list[str]] = {
    "resistor": ["1", "2"], "capacitor": ["1", "2"], "inductor": ["1", "2"],
    "led": ["K", "A"], "diode": ["K", "A"], "crystal": ["1", "2"],
    "switch": ["1", "2"], "battery": ["-", "+"], "speaker": ["-", "+"],
    "fuse": ["1", "2"], "antenna": ["1", "GND"], "motor": ["-", "+"],
    "transistor": ["G", "D", "S"], "connector": ["1", "2"],
    "ic": ["VCC", "GND", "1", "2"], "module": ["3V3", "GND", "1", "2"],
    "relay": ["COIL1", "COIL2", "NO", "COM"], "box": ["1", "2"],
}

# 引脚排序：电源在前、地最后、其余按数字自然序（进符号时左右分布才好看）
_PIN_ORDER = ("VCC", "VIN", "VBUS", "VBAT", "5V", "3V3", "3.3V", "1V8", "VDD", "+", "A", "1")


def _clean(v: Any, limit: int = 80) -> str:
    return str(v or "").strip()[:limit]


def kind_of(comp: dict[str, Any]) -> str:
    """从 type / kind / 位号前缀推断规范 kind。"""
    raw = _clean(comp.get("type") or comp.get("kind") or "").lower()
    if raw in KIND_ALIASES:
        return KIND_ALIASES[raw]
    # "MCU 模块" / "5V 稳压 LDO" 这类：逐个片段试
    for frag in re.split(r"[^a-z0-9]+", raw):
        if frag in KIND_ALIASES:
            return KIND_ALIASES[frag]
    cid = _clean(comp.get("id")).upper()
    for prefix, kind in (
        ("R", "resistor"), ("C", "capacitor"), ("L", "inductor"), ("D", "led"),
        ("Y", "crystal"), ("X", "crystal"), ("S", "switch"), ("K", "relay"),
        ("Q", "transistor"), ("J", "connector"), ("P", "connector"), ("CN", "connector"),
        ("M", "motor"), ("F", "fuse"), ("B", "battery"), ("SP", "speaker"),
        ("A", "antenna"), ("U", "ic"), ("IC", "ic"),
    ):
        if cid.startswith(prefix):
            if prefix == "D" and "led" not in raw and "led" not in _clean(comp.get("note")).lower():
                return "diode"
            return kind
    return "box"


def _pin_sort_key(pin: str) -> tuple[int, int, str]:
    low = pin.lower()
    if GND_RE.search(pin):
        return (9, 0, pin)
    for i, name in enumerate(_PIN_ORDER):
        if low == name.lower():
            return (0, i, pin)
    for i, name in enumerate(("gnd", "vss", "epad")):
        if low == name:
            return (9, i, pin)
    n = re.sub(r"\D", "", pin)
    return (5, int(n) if n else 9999, pin)


def _sort_pins(pins: list[str]) -> list[str]:
    return sorted(dict.fromkeys(pins), key=_pin_sort_key)


# 中文/符号极性 → 规范标记。模型极爱写 "C2正极"（漏了点号）：
# 老实现把整串当成一条只有单个端点的裸网络，那只电容就彻底掉链（PCB 上没有飞线）。
_POLARITY: dict[str, str] = {
    "正极": "+", "正": "+", "+": "+", "＋": "+", "阳极": "+", "正端": "+",
    "pos": "+", "positive": "+", "plus": "+",
    "负极": "-", "负": "-", "-": "-", "－": "-", "阴极": "-", "负端": "-",
    "neg": "-", "negative": "-", "minus": "-",
}
# 极性标记落到各类器件真实存在的引脚名上（电容/电阻是 1/2，LED/二极管是 A/K）
_POLARITY_PINS: dict[str, tuple[str, str]] = {
    "battery": ("+", "-"), "speaker": ("+", "-"), "motor": ("+", "-"),
    "led": ("A", "K"), "diode": ("A", "K"),
}
_POLARITY_PINS_DEFAULT = ("1", "2")
_REF_RE = re.compile(r"^[A-Za-z]{1,4}\d+$")


def _polarity(pin: str) -> str:
    """引脚名 → '+' / '-'；不是极性写法返回空串。"""
    key = _clean(pin, 12)
    return _POLARITY.get(key) or _POLARITY.get(key.lower(), "")


def _split_endpoint(end: str) -> tuple[str, str]:
    """'U1.GPIO8' → ('U1','GPIO8')；'3V3' → ('', '3V3')（裸网络名）。

    额外容忍漏点号的极性写法：'C2正极' / 'D1阳极' / 'BAT1-' → ('C2','+') 等。
    剥掉极性词后剩下的必须长得像位号（字母+数字），否则仍当裸网络名，
    避免把 '电池正极' 这类纯文字网络误判成器件。
    """
    end = _clean(end, 120)
    if "." in end:
        cid, pin = end.split(".", 1)
        cid, pin = cid.strip(), pin.strip()
        return cid, (_polarity(pin) or pin)
    for alias in sorted(_POLARITY, key=len, reverse=True):
        if end.lower().endswith(alias) and len(end) > len(alias):
            cid = end[: -len(alias)].strip()
            if _REF_RE.match(cid):
                return cid, _POLARITY[alias]
    return "", end


def _pin_on(comps: dict[str, dict[str, Any]], cid: str, pin: str) -> str:
    """把 '+'/'-' 换成该器件真实存在的引脚名。"""
    kind = (comps.get(cid) or {}).get("kind") or ""
    plus, minus = _POLARITY_PINS.get(kind, _POLARITY_PINS_DEFAULT)
    if pin == "+":
        return plus
    if pin == "-":
        return minus
    return pin


def normalize(raw: dict[str, Any] | None, title: str = "") -> dict[str, Any]:
    """把任意形状的 netlist 变成规范结构。"""
    raw = raw if isinstance(raw, dict) else {}
    warnings: list[str] = []

    def as_list(v: Any) -> list[Any]:
        return v if isinstance(v, list) else []

    raw_comps = [c for c in as_list(raw.get("components")) if isinstance(c, dict)]
    raw_conns = [c for c in as_list(raw.get("connections")) if isinstance(c, dict)]
    supplies = [s for s in as_list(raw.get("supplies")) if isinstance(s, dict)]

    # ---------- 元件
    comps: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    used_ids: set[str] = set()
    for i, c in enumerate(raw_comps):
        cid = _clean(c.get("id") or c.get("ref") or c.get("name"), 24)
        if not cid:
            kind0 = kind_of(c)
            cid = f"{KIND_PREFIX.get(kind0, 'X')}{i + 1}"
        cid = re.sub(r"\s+", "", cid)
        if cid in used_ids:  # 重名：补后缀，绝不丢元件
            n = 2
            while f"{cid}_{n}" in used_ids:
                n += 1
            warnings.append(f"元件 {cid} 重名，已重命名为 {cid}_{n}")
            cid = f"{cid}_{n}"
        used_ids.add(cid)
        kind = kind_of(c)
        comps[cid] = {
            "id": cid,
            "kind": kind,
            "type": _clean(c.get("type") or c.get("kind")),
            "value": _clean(c.get("value") or c.get("note") or c.get("name")),
            "footprint": _clean(c.get("footprint")),
            "lcsc": _clean(c.get("lcsc") or c.get("part") or c.get("mpn")),
            "note": _clean(c.get("note")),
            "pins": [],
        }
        order.append(cid)

    if not comps:
        return {
            "version": NETLIST_VERSION,
            "title": title or "未命名电路",
            "components": [], "connections": [], "nets": [], "supplies": supplies,
            "stats": {"components": 0, "connections": 0, "nets": 0, "unconnected": 0},
            "unconnected": [],
            "warnings": ["网表里没有元件"],
        }

    # ---------- 连线（端点缺失/自环直接丢，并记账）
    conns: list[dict[str, str]] = []
    fixed: list[str] = []
    for c in raw_conns:
        a, b = _clean(c.get("from")), _clean(c.get("to"))
        if not a or not b or a == b:
            warnings.append(f"连线端点无效，已跳过：{a} → {b}")
            continue
        ca, pa = _split_endpoint(a)
        cb, pb = _split_endpoint(b)
        if ca and ca not in comps:
            warnings.append(f"连线引用了不存在的元件 {ca}，已保留为网络端点")
        if cb and cb not in comps:
            warnings.append(f"连线引用了不存在的元件 {cb}，已保留为网络端点")
        # 极性落地成真实引脚名，并**回写端点**：网络是按端点字符串聚合的，
        # 只映射不回写的话 'C2.+' 和 C2 自己的 ['1','2'] 仍然对不上，仍然没飞线。
        if ca in comps:
            canon = f"{ca}.{_pin_on(comps, ca, pa)}"
            if a != canon:
                fixed.append(f"{a}→{canon}")
                a = canon
        if cb in comps:
            canon = f"{cb}.{_pin_on(comps, cb, pb)}"
            if b != canon:
                fixed.append(f"{b}→{canon}")
                b = canon
        conns.append({"from": a, "to": b, "wire_color": _clean(c.get("wire_color"), 24)})
    if fixed:
        warnings.append(
            f"已按极性补全 {len(fixed)} 个引脚端点（{'、'.join(fixed[:4])}…）："
            "引脚请写成 位号.引脚名，别写 C2正极"
        )

    # ---------- 引脚
    pins: dict[str, list[str]] = {cid: [] for cid in comps}
    for c in conns:
        for end in (c["from"], c["to"]):
            cid, pin = _split_endpoint(end)
            if cid in pins and pin:
                pins[cid].append(pin)
    isolated: list[str] = []
    for cid in order:
        ps = _sort_pins(pins[cid])
        if not ps:
            ps = list(DEFAULT_PINS.get(comps[cid]["kind"], ["1", "2"]))
            isolated.append(cid)
            warnings.append(
                f"元件 {cid} 未出现在任何连线中，按默认引脚 {ps} 放置（PCB 上不会有飞线）"
            )
        comps[cid]["pins"] = ps

    # ---------- 网络聚合（并查集）
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
        union(c["from"], c["to"])

    groups: dict[str, list[str]] = {}
    for node in list(parent):
        groups.setdefault(find(node), []).append(node)

    nets: list[dict[str, Any]] = []
    auto = 0
    for members in groups.values():
        pts = sorted(set(members))
        bare = [_split_endpoint(m)[1] for m in pts if "." not in m]
        comp_pts = [(m.split(".", 1)[0], m.split(".", 1)[1]) for m in pts if "." in m]

        # 网络命名：裸端点名 > 电源/地引脚 > 自动生成
        name = ""
        if bare:
            name = sorted(bare, key=len)[0]
        else:
            for cid, pin in comp_pts:
                if POWER_RE.search(pin) or GND_RE.search(pin):
                    name = re.sub(r"^[^A-Za-z0-9]+", "", pin).upper()
                    break
                # 电池正极那条线既不是 VCC 也不是 GND，按惯例叫 VBAT
                if pin == "+" and (comps.get(cid) or {}).get("kind") == "battery":
                    name = "VBAT"
                    break
        if not name:
            auto += 1
            name = f"N${auto:03d}"

        is_gnd = bool(GND_RE.search(name)) or any(GND_RE.search(p) for _, p in comp_pts)
        is_pwr = bool(POWER_RE.search(name)) or any(POWER_RE.search(p) for _, p in comp_pts)
        if is_gnd:
            kind = "gnd"
            name = "GND"
        elif is_pwr:
            kind = "power"
        else:
            kind = "signal"

        nets.append({
            "name": name,
            "kind": kind,
            "points": pts,
            "components": sorted({m.split(".", 1)[0] for m in pts if "." in m}),
        })

    nets.sort(key=lambda n: (n["kind"] != "power", n["kind"] != "gnd", n["name"]))

    components = [comps[cid] for cid in order]
    return {
        "version": NETLIST_VERSION,
        "title": title or _clean(raw.get("title")) or "电路原理图",
        "components": components,
        "connections": conns,
        "nets": nets,
        "supplies": supplies,
        # 没有出现在任何连线里的元件：PCB 上不会有飞线，工具层要显式告诉用户
        "unconnected": isolated,
        "stats": {
            "components": len(components),
            "connections": len(conns),
            "nets": len(nets),
            "pins": sum(len(c["pins"]) for c in components),
            "unconnected": len(isolated),
        },
        "warnings": warnings,
    }


def load(path: Path | str) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def annotate(net: dict[str, Any]) -> dict[str, Any]:
    """用器件库补齐 footprint / lcsc 缺失项（就地补，找不到就留空不臆造）。"""
    from . import parts  # 延迟导入：器件库是可选增强，不该成为导入期依赖

    comps = net.get("components") or []
    filled = 0
    for i, c in enumerate(comps):
        before_fp = c.get("footprint")
        comps[i] = parts.resolve(c)
        if comps[i].get("footprint") and not before_fp:
            filled += 1
    net["components"] = comps
    net.setdefault("stats", {})["parts_matched"] = filled
    return net
