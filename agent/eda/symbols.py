"""参数化生成 KiCad 原理图符号（s-expression 字符串）。

为什么要自己画符号而不是引用官方库：
  官方库要装才存在，用户机器上一句 "symbol not found" 就会让整个产物打不开。
  这里所有符号都内嵌进 .kicad_sch 的 lib_symbols 段 —— 生成的文件 100% 自包含，
  拷到任何一台装了 KiCad 的机器上双击就能开。

格式约定（严格照 KiCad 9/10 的 s-expression，已用 kicad-cli 10.0.7 实机校验）：
  * property 不再有 (id N) 字段（那是 KiCad 6 的写法，7+ 已删除）
  * 字号必须是双值 (size 1.27 1.27)（宽 高）
  * 隐藏属性写 (hide yes)，不是裸 hide
  * 图形放 <名>_0_1 子符号，引脚放 <名>_1_1 子符号，两处不能重复
"""

from __future__ import annotations

import re
from typing import Any

# KiCad 原理图单位是 mm，栅格 1.27mm（50mil）。所有尺寸都对齐到这个栅格。
G = 1.27
PIN_LEN = 1.27

_POWER_RE = re.compile(r"(^|[.\-_])(VCC|VDD|VIN|VBUS|VBAT|5V|3V3|3\.3V|1V8|VSS|AGND|DGND)$", re.I)
_GND_RE = re.compile(r"(^|[.\-_])(GND|GROUND|VSS|AGND|DGND|EPAD)$", re.I)
_OUT_RE = re.compile(r"(^|[.\-_])(VOUT|VREG|VCC_OUT|VO)$", re.I)
_IO_RE = re.compile(r"(GPIO|IO\d|\bTX|\bRX|SDA|SCL|MOSI|MISO|SCK|DATA|D[+-]|INT|ADC)", re.I)


def _f(v: float) -> str:
    """KiCad 坐标：最多两位小数，去掉多余的 0。"""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s and s != "-0" else "0"


def _esc(s: Any) -> str:
    """s-expression 字符串转义。"""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _xy(x: float, y: float) -> str:
    return f"(xy {_f(x)} {_f(y)})"


def _stroke(w: float = 0.254) -> str:
    return f"(stroke (width {_f(w)}) (type default) (color 0 0 0 0))"


def _polyline(pts: list[tuple[float, float]], close: bool = False, fill: bool = False) -> str:
    p = list(pts) + ([pts[0]] if close and pts else [])
    fill_s = "(fill (type outline))" if fill else "(fill (type none))"
    return f"(polyline (pts {' '.join(_xy(x, y) for x, y in p)}) {_stroke()} {fill_s})"


def _rect(x0: float, y0: float, x1: float, y1: float, fill: bool = False) -> str:
    fill_s = "(fill (type background))" if fill else "(fill (type none))"
    return f"(rectangle (start {_f(x0)} {_f(y0)}) (end {_f(x1)} {_f(y1)}) {_stroke()} {fill_s})"


def _circle(cx: float, cy: float, r: float) -> str:
    return f"(circle (center {_f(cx)} {_f(cy)}) (radius {_f(r)}) {_stroke()} (fill (type none)))"


def _arc(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> str:
    return (
        f"(arc (start {_f(a[0])} {_f(a[1])}) (mid {_f(b[0])} {_f(b[1])}) "
        f"(end {_f(c[0])} {_f(c[1])}) {_stroke()} (fill (type none)))"
    )


def _pin(name: str, number: str, x: float, y: float, angle: int, etype: str = "passive",
         length: float = PIN_LEN, hide: bool = False) -> str:
    """引脚。

    hide 参数保留但不再输出关键字 —— KiCad 9/10 的 pin 隐藏已改成 (hide yes)，
    而电源符号用 length 0 + (pin_names (hide yes)) 就够，不写最安全。
    """
    nm = "" if name in ("~", "", None) else str(name)
    return (
        f'(pin {etype} line (at {_f(x)} {_f(y)} {angle}) (length {_f(length)})\n'
        f'        (name "{_esc(nm)}" (effects (font (size 1.27 1.27))))\n'
        f'        (number "{_esc(number)}" (effects (font (size 1.27 1.27))))\n'
        f'      )'
    )


def _etype(pin_name: str, kind: str) -> str:
    # 电源输出要先判（VOUT 否则会落到 bidirectional，ERC 会当成悬空驱动源）
    if _OUT_RE.search(pin_name):
        return "power_out"
    if _GND_RE.search(pin_name) or _POWER_RE.search(pin_name):
        return "power_in"
    if kind in ("resistor", "capacitor", "inductor", "crystal", "fuse", "switch", "connector",
                "diode", "led", "battery", "speaker", "motor", "relay", "antenna"):
        return "passive"
    if _IO_RE.search(pin_name):
        return "bidirectional"
    if kind in ("transistor",):
        return "passive"
    return "bidirectional"


# ---------------------------------------------------------------- 图形
def _body_two_pin(kind: str) -> list[str]:
    """两脚器件的身体图形（引脚统一在 (0,±3.81)）。"""
    if kind == "resistor":
        return [_rect(-1.016, -2.54, 1.016, 2.54, fill=True)]
    if kind in ("capacitor", "battery"):
        return [
            _polyline([(-1.27, -0.762), (1.27, -0.762)]),
            _polyline([(-1.27, 0.762), (1.27, 0.762)]),
        ]
    if kind == "inductor":
        # 三段拱形近似线圈
        segs = []
        n = 3
        for i in range(n):
            x0 = -1.905 + i * 1.27
            segs.append(_arc((x0, -1.27), (x0 + 0.635, -2.159), (x0 + 1.27, -1.27)))
        return segs
    if kind == "crystal":
        return [
            _rect(-1.016, -1.27, 1.016, 1.27, fill=True),
            _polyline([(-2.032, -1.27), (-1.016, -1.27)]),
            _polyline([(1.016, -1.27), (2.032, -1.27)]),
        ]
    if kind == "diode":
        return [
            _polyline([(-1.27, 1.27), (-1.27, -1.27), (1.27, 0)], close=True, fill=True),
            _polyline([(1.27, 1.27), (1.27, -1.27)]),
        ]
    if kind == "led":
        return [
            _polyline([(-1.27, 1.27), (-1.27, -1.27), (1.27, 0)], close=True, fill=True),
            _polyline([(1.27, 1.27), (1.27, -1.27)]),
            _polyline([(-2.54, 2.54), (-1.27, 1.27)]),
            _polyline([(-2.032, 1.778), (-1.27, 1.016)]),
        ]
    if kind == "fuse":
        return [
            _rect(-1.27, -0.762, 1.27, 0.762, fill=True),
            _polyline([(-2.54, 0), (2.54, 0)]),
        ]
    if kind == "switch":
        return [
            _circle(-2.032, -1.27, 0.254),
            _circle(2.032, -1.27, 0.254),
            _polyline([(-2.032, -1.27), (1.778, 1.016)]),
        ]
    if kind == "speaker":
        return [
            _rect(-1.27, -1.27, 0, 1.27, fill=True),
            _polyline([(0, -1.27), (1.27, -2.54), (1.27, 2.54), (0, 1.27)], close=True),
        ]
    if kind == "motor":
        return [_circle(0, 0, 2.032)]
    if kind == "antenna":
        return [
            _polyline([(-1.27, 2.54), (0, -1.27), (1.27, 2.54)]),
            _polyline([(-1.27, 2.54), (1.27, 2.54)]),
        ]
    if kind == "relay":
        return [
            _rect(-1.778, -2.54, 1.778, 2.54, fill=True),
            _polyline([(-1.016, -2.54), (-1.016, 2.54)]),
        ]
    if kind == "transistor":
        return [
            _circle(0, 0, 2.032),
            _polyline([(-0.762, -1.27), (-0.762, 1.27)]),
            _polyline([(-0.762, -0.508), (1.27, -1.27)]),
            _polyline([(-0.762, -0.508), (1.27, 1.016)]),
        ]
    return [_rect(-1.778, -1.778, 1.778, 1.778, fill=True)]


TWO_PIN_KINDS = {
    "resistor", "capacitor", "inductor", "crystal", "diode", "led", "fuse",
    "switch", "speaker", "motor", "antenna", "relay", "transistor", "battery",
}


# ---------------------------------------------------------------- 布局（图形 / 引脚分开返回）
def _layout_two_pin(pins: list[str], kind: str) -> tuple[list[str], list[str], dict[str, tuple[float, float]]]:
    graphics = list(_body_two_pin(kind))
    lines: list[str] = []
    pos: dict[str, tuple[float, float]] = {}
    coords = [(0.0, 3.81, 270), (0.0, -3.81, 90)]
    for i, p in enumerate(pins[:2]):
        x, y, ang = coords[i]
        pos[p] = (x, y)
        lines.append(_pin(p, str(i + 1), x, y, ang, etype=_etype(p, kind)))
    # 多于 2 脚的（如继电器 4 脚）：剩下的排在左右
    extra = pins[2:]
    for j, p in enumerate(extra):
        y = 1.27 * (1 - j)
        x = -3.81 if j % 2 == 0 else 3.81
        ang = 0 if j % 2 == 0 else 180
        pos[p] = (x, y)
        lines.append(_pin(p, str(j + 3), x, y, ang, etype=_etype(p, kind)))
    return graphics, lines, pos


def _layout_ic(pins: list[str], kind: str) -> tuple[list[str], list[str], dict[str, tuple[float, float]]]:
    """IC / 模块：引脚左右分布（奇数个时右边多一个）。

    所有引脚 y 必须落在 1.27mm 栅格上 —— 否则 kicad 的 _q() 量化后
    连线端点与引脚错开不到 1mm，表现为「图能开但所有线都是断的」。
    """
    n = max(len(pins), 2)
    half = (n + 1) // 2
    rows = max(half, n - half)
    h = max(rows * 2.54, 5.08)
    w = 10.16 if kind == "module" else 7.62
    graphics = [_rect(-w / 2, -h / 2, w / 2, h / 2, fill=True)]
    lines: list[str] = []
    pos: dict[str, tuple[float, float]] = {}
    for i, p in enumerate(pins):
        if i < half:
            x, ang, k = -w / 2 - PIN_LEN, 0, i
        else:
            x, ang, k = w / 2 + PIN_LEN, 180, i - half
        y = h / 2 - 1.27 - k * 2.54
        pos[p] = (x, y)
        lines.append(_pin(p, str(i + 1), x, y, ang, etype=_etype(p, kind)))
    return graphics, lines, pos


def _layout_connector(pins: list[str], kind: str) -> tuple[list[str], list[str], dict[str, tuple[float, float]]]:
    """连接器。本体半宽必须取 1.27，引脚才能落在栅格上（1.016 会偏 0.254mm）。"""
    n = max(len(pins), 2)
    h = max(n * 2.54, 5.08)
    graphics = [_rect(-1.27, -h / 2, 1.27, h / 2, fill=True)]
    lines: list[str] = []
    pos: dict[str, tuple[float, float]] = {}
    for i, p in enumerate(pins):
        y = -h / 2 + 1.27 + i * 2.54
        x = -1.27 - PIN_LEN
        pos[p] = (x, y)
        lines.append(_pin(p, str(i + 1), x, y, 0, etype="passive"))
    return graphics, lines, pos


def _layout_power(name: str, kind: str) -> tuple[list[str], list[str], dict[str, tuple[float, float]]]:
    """电源/地符号：引脚落在符号原点（length 0 / hide），图形画在一侧。"""
    if kind == "gnd":
        graphics = [
            _polyline([(-1.27, 0), (1.27, 0)]),
            _polyline([(-0.635, -1.27), (0.635, -1.27)]),
            _polyline([(-0.254, -2.54), (0.254, -2.54)]),
        ]
    else:
        graphics = [
            _polyline([(-1.27, 0), (1.27, 0)]),
            _polyline([(0, 0), (0, 2.54)]),
            _polyline([(-0.635, 1.905), (0, 2.54), (0.635, 1.905)]),
        ]
    pin = _pin(name, "1", 0.0, 0.0, 90, etype="power_in", length=0.0, hide=True)
    return graphics, [pin], {name: (0.0, 0.0)}


def _layout_pwr_flag() -> tuple[list[str], list[str], dict[str, tuple[float, float]]]:
    """PWR_FLAG：告诉 ERC「这个电源网络有驱动源」，否则每条电源网络都会报 not driven。"""
    graphics = [_polyline([(0, 0), (0, 1.27), (-1.016, 1.905), (0, 2.54), (1.016, 1.905), (0, 1.27)],
                          close=True)]
    pin = _pin("~", "1", 0.0, 0.0, 90, etype="power_out", length=0.0)
    return graphics, [pin], {"~": (0.0, 0.0)}


def _layout(kind: str, pins: list[str]) -> tuple[list[str], list[str], dict[str, tuple[float, float]]]:
    if kind == "gnd":
        return _layout_power("GND", "gnd")
    if kind == "power":
        return _layout_power("VCC", "power")
    if kind == "pwr_flag":
        return _layout_pwr_flag()
    if kind == "connector":
        return _layout_connector(pins, kind)
    if kind in ("ic", "module", "box"):
        return _layout_ic(pins, kind)
    return _layout_two_pin(pins, kind)


# ---------------------------------------------------------------- 对外
def build(name: str, kind: str, pins: list[str], value: str = "", ref: str = "",
          keywords: str = "") -> tuple[str, dict[str, tuple[float, float]]]:
    """生成单个符号定义。返回 (s-expression, {引脚名: (x, y)})。

    name：符号在 lib_symbols 里的名字，如 "Gen:R_0402"。
    """
    pins = [p for p in (pins or []) if str(p).strip()] or ["1", "2"]
    ref = ref or kind[:1].upper()
    is_power = kind in ("gnd", "power", "pwr_flag")
    graphics, plines, pos = _layout(kind, pins)
    short = name.split(":")[-1]

    def prop(key: str, val: Any, hide: bool = False) -> str:
        h = "\n      (hide yes)" if hide else ""
        return (
            f'    (property "{key}" "{_esc(val)}" (at 0 0 0){h}\n'
            f'      (effects (font (size 1.27 1.27)))\n'
            f'    )'
        )

    parts = [
        f'  (symbol "{name}"' + ("\n    (power global)" if is_power else ""),
        "    (pin_numbers (hide yes))",
        "    (pin_names (offset 0) (hide yes))",
        "    (exclude_from_sim no)",
        "    (in_bom yes)",
        "    (on_board yes)",
        "    (duplicate_pin_numbers_are_jumpers no)",
        prop("Reference", "#PWR" if is_power else ref),
        prop("Value", value or short),
        prop("Footprint", "", True),
        prop("Datasheet", "", True),
        prop("Description", value or kind, True),
        prop("ki_keywords", keywords or kind, True),
        f'    (symbol "{short}_0_1"',
    ]
    parts += [f"      {g}" for g in graphics]
    parts.append("    )")
    # 引脚单独放第二个子符号（KiCad 惯例：_0_1 放图形，_1_1 放引脚）
    parts.append(f'    (symbol "{short}_1_1"')
    parts += [f"      {p}" for p in plines]
    parts.append("    )")
    parts.append("    (embedded_fonts no)")
    parts.append("  )")
    return "\n".join(parts), pos


def pin_positions(kind: str, pins: list[str]) -> dict[str, tuple[float, float]]:
    """只要引脚坐标（布局时用），不生成完整符号。"""
    pins = [p for p in (pins or []) if str(p).strip()] or ["1", "2"]
    return _layout(kind, pins)[2]


def extent(kind: str, pins: list[str]) -> tuple[float, float]:
    """符号占位宽高（mm），布局排间距用。"""
    p = pin_positions(kind, pins)
    xs = [x for x, _ in p.values()] or [0]
    ys = [y for _, y in p.values()] or [0]
    return (max(abs(x) for x in xs) * 2 + 5.08, max(abs(y) for y in ys) * 2 + 5.08)
