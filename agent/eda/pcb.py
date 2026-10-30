"""网表 + 板级约束 → KiCad PCB（.kicad_pcb），纯文本生成，零第三方依赖。

P1 能力边界（**刻意不做自动布线**）：
  ✅ 板框（含圆角 / 圆形 / 异形多边形）
  ✅ 器件布局（主控居中、接口靠边、被动件网格填充、避让安装孔与禁布区）
  ✅ 安装孔（金属化 / 非金属化）
  ✅ GND 铺铜（B.Cu 整板）
  ❌ 信号线走线 —— 交给 EDA 自带布线器或 Freerouting，产物里会明说

焊盘是**参数化近似**（按引脚数与占位尺寸推算），不是官方封装库的精确几何。
布线前请在 KiCad 里把 footprint 替换成官方库对应封装 —— 这一点会在工具返回里写明，
不藏着：给一张"看起来能打样其实焊不了"的板子，比明说"需替换封装"伤害大得多。
"""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

from . import board as board_mod
from . import parts as parts_mod

PCB_VERSION = 20241229          # KiCad 9/10 板文件格式
GENERATOR_VERSION = "10.0"

ORIGIN_X, ORIGIN_Y = 100.0, 100.0   # 板中心在 PCB 坐标里的落点（保证坐标全为正）
# 器件离板边最小距离。不能只按本体算：位号文字在本体外 1.4mm、字高约 1mm，
# 外沿实际到 1.9mm —— 取 1.5 时靠边的器件丝印会被板边裁掉（silk_edge_clearance）。
EDGE_MARGIN = 2.4
# 器件间距同理：位号在上沿外 1.9mm，值在下沿（F.Fab 不参与丝印检查），
# 所以垂直方向至少要 2.2mm，取 3.0 留余量，否则 silk_overlap。
PART_GAP = 3.0
HOLE_CLEARANCE = 1.5            # 安装孔周围禁布
GRID_STEP = 0.5                 # 网格扫描步长
MIN_PITCH = 1.15                # 密脚器件的最小引脚节距（焊盘 + 间隙，不够就把本体撑高）
MIN_PAD_CLEARANCE = 0.25        # 相邻焊盘之间至少留这么多（DRC 默认铜箔间距 0.2mm）


def _uid() -> str:
    return str(uuid.uuid4())


def _f(v: float) -> str:
    r = round(float(v), 4)
    return f"{r:g}"


def _esc(s: Any) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------- 网络表
def _net_table(net: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    """返回 {net_name: net_id} 与按顺序的网络名列表（0 号是空网络）。"""
    names: list[str] = []
    for n in net.get("nets") or []:
        nm = str(n.get("name") or "").strip()
        if nm and nm not in names:
            names.append(nm)
    return {nm: i + 1 for i, nm in enumerate(names)}, names


def _pin_net_map(net: dict[str, Any], ids: dict[str, int]) -> dict[str, int]:
    """{"U1.GPIO8": net_id} —— 引脚端点字符串 → 网络号。"""
    out: dict[str, int] = {}
    for n in net.get("nets") or []:
        nid = ids.get(str(n.get("name") or ""))
        if not nid:
            continue
        for ep in n.get("points") or []:
            if "." in str(ep):
                out[str(ep)] = nid
    return out


def _gnd_and_power(net: dict[str, Any], ids: dict[str, int]) -> tuple[int | None, int | None]:
    gnd = pwr = None
    for n in net.get("nets") or []:
        nm = str(n.get("name") or "")
        nid = ids.get(nm)
        if not nid:
            continue
        kind = str(n.get("kind") or "")
        if kind == "gnd" and gnd is None:
            gnd = nid
        elif kind == "power" and pwr is None:
            pwr = nid
    if gnd is None:
        for nm, nid in ids.items():
            if nm.upper() in ("GND", "GROUND", "VSS"):
                gnd = nid
                break
    return gnd, pwr


# ---------------------------------------------------------------- 自动安装孔
def _auto_holes(b: dict[str, Any]) -> list[dict[str, Any]]:
    """board.json 没给安装孔时，在四角内缩处补 4 个 M3 非金属化孔。"""
    if b.get("mounting_holes"):
        return []
    w = b["board"]["width_mm"]
    h = b["board"]["height_mm"]
    if min(w, h) < 30.0:
        return []
    inset = 3.5
    rx, ry = w / 2.0 - inset, h / 2.0 - inset
    if rx <= 2 or ry <= 2:
        return []
    holes = []
    for i, (x, y) in enumerate([(-rx, -ry), (rx, -ry), (rx, ry), (-rx, ry)]):
        if not board_mod.inside(x, y, b, inset * 0.6):
            continue
        holes.append({"name": f"H{i + 1}", "x_mm": round(x, 3), "y_mm": round(y, 3),
                      "diameter_mm": 3.2, "plated": False, "slot_len_mm": 0.0,
                      "auto": True})
    return holes


# ---------------------------------------------------------------- 布局
def _place(net: dict[str, Any], b: dict[str, Any],
           holes: list[dict[str, Any]],
           notes: list[str] | None = None) -> dict[str, tuple[float, float]]:
    """器件坐标（板坐标系，中心原点）。返回 {ref: (x, y)}。

    notes：收集放不下的器件。真实会话里器件尺寸是模型自己写的（见过 26×26mm 的"电池座"），
    板子又是结构给死的，放不下是常态 —— 这时不能硬塞板中心（会压住主控，一次报 5 条
    courtyards_overlap），而是逐级放宽间距重试，最后仍失败才贴中心并如实记一笔。
    """
    comps = [c for c in (net.get("components") or []) if c.get("id")]
    placed: list[tuple[float, float, float, float]] = []   # x, y, w, h
    for mh in holes:
        need = mh["diameter_mm"] / 2.0 + HOLE_CLEARANCE
        placed.append((mh["x_mm"], mh["y_mm"], need * 2, need * 2))
    for ko in b.get("keepouts") or []:
        placed.append((ko["x_mm"], ko["y_mm"], ko["w_mm"], ko["h_mm"]))

    pos: dict[str, tuple[float, float]] = {}
    w_b, h_b = b["board"]["width_mm"], b["board"]["height_mm"]

    def free(x: float, y: float, w: float, h: float,
             gap: float = PART_GAP, margin: float = EDGE_MARGIN) -> bool:
        if not board_mod.inside(x, y, b, margin):
            return False
        if not board_mod.inside(x - w / 2, y - h / 2, b, margin * 0.5):
            return False
        if not board_mod.inside(x + w / 2, y + h / 2, b, margin * 0.5):
            return False
        if board_mod.blocked(x, y, w, h, b) is not None:
            return False
        for px, py, pw, ph in placed:
            if abs(x - px) < (w + pw) / 2 + gap and abs(y - py) < (h + ph) / 2 + gap:
                return False
        return True

    def commit(cid: str, x: float, y: float, w: float, h: float) -> None:
        pos[cid] = (round(x, 3), round(y, 3))
        placed.append((x, y, w, h))

    # 候选点：从板中心向外扩散（按到中心距离排序）。
    # 不能从左上角横扫 —— 那样主控被禁布区顶开后会一路挤到左上，
    # 后面靠边的接口反而没位置了，整体看起来像"抱团在角落"。
    cands: list[tuple[float, float]] = []
    nx = int(w_b / GRID_STEP / 2)
    ny = int(h_b / GRID_STEP / 2)
    for j in range(-ny, ny + 1):
        for i in range(-nx, nx + 1):
            cands.append((i * GRID_STEP, j * GRID_STEP))
    cands.sort(key=lambda p: math.hypot(p[0], p[1] * 1.2))   # Y 稍微加权，横向优先铺开

    def scan(w: float, h: float, gap: float = PART_GAP,
             margin: float = EDGE_MARGIN) -> tuple[float, float] | None:
        for x, y in cands:
            if free(x, y, w, h, gap, margin):
                return x, y
        return None

    def scan_relaxed(w: float, h: float, cid: str) -> tuple[float, float]:
        """标准间距 → 收紧间距 → 贴边，实在放不下才回落到板中心。"""
        # 间距可以让，离板边的距离不能让：位号文字在本体外 1.9mm，
        # margin 一降到 1.2 就会被板边裁掉（silk_edge_clearance）。
        for gap, margin in ((PART_GAP, EDGE_MARGIN), (1.2, EDGE_MARGIN), (0.6, 2.0)):
            spot = scan(w, h, gap, margin)
            if spot:
                if gap != PART_GAP and notes is not None:
                    notes.append(f"{cid} 布局较挤（间距降到 {gap}mm），建议手动微调位置")
                return spot
        if notes is not None:
            notes.append(f"{cid}（{w:g}×{h:g}mm）在板内放不下，已暂放板中心，请在 EDA 里手动挪位")
        return (0.0, 0.0)

    # 1) 接口**先**占边：靠边是硬约束（必须对着壳体开孔），晚放就会被主控挤走
    conns = [c for c in comps if c.get("kind") == "connector"]
    others = [c for c in comps if c.get("kind") != "connector"]
    edge_of = {str(ec.get("ref") or "").upper(): ec["edge"] for ec in (b.get("edge_constraints") or [])}
    groups: dict[str, list[dict[str, Any]]] = {"left": [], "right": [], "top": [], "bottom": []}
    for i, c in enumerate(conns):
        e = edge_of.get(str(c["id"]).upper()) or ["right", "bottom", "left", "top"][i % 4]
        groups[e].append(c)

    for edge, cs in groups.items():
        n = len(cs)
        for k, c in enumerate(cs):
            cw, ch = _fp_size(c)
            t = (k + 1) / (n + 1)
            if edge == "left":
                x, y = -w_b / 2 + EDGE_MARGIN + cw / 2, -h_b / 2 + t * h_b
            elif edge == "right":
                x, y = w_b / 2 - EDGE_MARGIN - cw / 2, -h_b / 2 + t * h_b
            elif edge == "top":
                x, y = -w_b / 2 + t * w_b, -h_b / 2 + EDGE_MARGIN + ch / 2
            else:
                x, y = -w_b / 2 + t * w_b, h_b / 2 - EDGE_MARGIN - ch / 2
            if free(x, y, cw, ch):
                commit(c["id"], x, y, cw, ch)
            else:
                spot = scan_relaxed(cw, ch, str(c["id"]))
                commit(c["id"], spot[0], spot[1], cw, ch)

    # 2) 主控：面积最大的 module/ic，尽量居中
    mains = [c for c in others if c.get("kind") in ("module", "ic")]
    mains.sort(key=lambda c: -(_fp_size(c)[0] * _fp_size(c)[1]))
    if mains:
        main = mains[0]
        mw, mh = _fp_size(main)
        for ty in (-h_b * 0.1, 0.0):
            if free(0.0, ty, mw, mh):
                commit(main["id"], 0.0, ty, mw, mh)
                break
        else:
            spot = scan_relaxed(mw, mh, str(main["id"]))
            commit(main["id"], spot[0], spot[1], mw, mh)
        others = [c for c in others if c["id"] != main["id"]]

    # 3) 其余：大件先放，从中心向外找位置
    others.sort(key=lambda c: -(_fp_size(c)[0] * _fp_size(c)[1]))
    for c in others:
        cw, ch = _fp_size(c)
        # 不丢件：放不下也要留个位置，但位置是降级来的（notes 里记一笔）
        spot = scan(cw, ch) or scan_relaxed(cw, ch, str(c["id"]))
        commit(c["id"], spot[0], spot[1], cw, ch)

    return pos


# ---------------------------------------------------------------- 图形
def _fp_line(x1: float, y1: float, x2: float, y2: float, layer: str, width: float = 0.12) -> str:
    return (
        f'    (fp_line (start {_f(x1)} {_f(y1)}) (end {_f(x2)} {_f(y2)})\n'
        f'      (stroke (width {_f(width)}) (type solid)) (layer "{layer}") (uuid "{_uid()}")\n'
        f'    )'
    )


def _fp_rect(w: float, h: float, layer: str, width: float = 0.12) -> list[str]:
    hw, hh = w / 2.0, h / 2.0
    return [
        _fp_line(-hw, -hh, hw, -hh, layer, width),
        _fp_line(hw, -hh, hw, hh, layer, width),
        _fp_line(hw, hh, -hw, hh, layer, width),
        _fp_line(-hw, hh, -hw, -hh, layer, width),
    ]


def _fp_circle(r: float, layer: str, width: float = 0.05) -> str:
    """闭合圆。KiCad 的 courtyard 必须是闭合形状，单根线会被判 malformed。"""
    return (
        f'    (fp_circle (center 0 0) (end {_f(r)} 0)\n'
        f'      (stroke (width {_f(width)}) (type solid)) (fill no) (layer "{layer}") (uuid "{_uid()}")\n'
        f'    )'
    )


def _fp_text(kind: str, text: str, y: float, layer: str, size: float = 1.0) -> str:
    return (
        f'    (fp_text {kind} "{_esc(text)}" (at 0 {_f(y)} 0) (layer "{layer}")\n'
        f'      (effects (font (size {_f(size)} {_f(size)}) (thickness 0.15)))\n'
        f'      (uuid "{_uid()}")\n'
        f'    )'
    )


def _pad(number: str, x: float, y: float, w: float, h: float,
         net_id: int, net_name: str) -> str:
    net_s = f' (net {net_id} "{_esc(net_name)}")' if net_id else ""
    return (
        f'    (pad "{_esc(number)}" smd rect (at {_f(x)} {_f(y)}) (size {_f(w)} {_f(h)})\n'
        f'      (layers "F.Cu" "F.Paste" "F.Mask"){net_s}\n'
        f'      (uuid "{_uid()}")\n'
        f'    )'
    )


def _hole_pad(d: float, plated: bool) -> str:
    kind = "thru_hole" if plated else "np_thru_hole"
    layers = '"*.Cu" "*.Mask"' if plated else '"*.Cu" "*.Mask"'
    return (
        f'    (pad "" {kind} circle (at 0 0) (size {_f(d)} {_f(d)}) (drill {_f(d)})\n'
        f'      (layers {layers})\n'
        f'      (uuid "{_uid()}")\n'
        f'    )'
    )


def _fp_size(comp: dict[str, Any]) -> tuple[float, float]:
    """封装在板上的实际占位（本体尺寸，必要时按引脚数撑高）。

    密脚器件（USB-C 十几脚那种）如果按器件库给的外形尺寸排引脚，节距会挤到 1.1mm，
    焊盘还有 1mm 高 → 相邻焊盘只剩 0.14mm，低于 0.2mm 的最小铜箔间距（DRC clearance 9 条由此而来）。
    所以这里先保证节距够，不够就把本体撑高。布局与封装必须共用它，否则碰撞检测按小尺寸算、
    实际封装按大尺寸画，又会撞在一起。
    """
    w, h = parts_mod.size_of(comp)
    n = len(comp.get("pins") or []) or 2
    if n > 2:
        per_side = max(1, (n + 1) // 2)
        h = max(h, (per_side + 1) * MIN_PITCH)
    return w, h


def pad_geom(comp: dict[str, Any], w: float | None = None,
             h: float | None = None) -> tuple[float, float, float, int]:
    """焊盘几何：宽、高、节距、每侧引脚数。

    抽出来是为了让 EasyEDA writer 复用同一套数 —— 两份输出如果是两套焊盘尺寸，
    用户对比时会以为板子做错了，其实是生成器自己不一致。

    焊盘高度必须给相邻焊盘让出间隙：节距 1.15mm 时焊盘只能到 0.9mm，
    否则 clearance 违规（真实会话里 USB-C 就是这个情况，一次报了 9 条）。
    """
    if w is None or h is None:
        w, h = _fp_size(comp)
    pins = comp.get("pins") or []
    n = len(pins) or 2
    pw = min(1.8, max(0.4, h * 0.6 if n > 2 else w * 0.35))
    ph = min(1.0, max(0.3, h * 0.5 if n > 2 else h * 0.7))
    per_side = max(1, (n + 1) // 2)
    pitch = h / (per_side + 1) if per_side else h / 2
    if n > 2:
        ph = max(0.25, min(ph, pitch - MIN_PAD_CLEARANCE))
    return pw, ph, pitch, per_side


def _mod_name(comp: dict[str, Any], size: tuple[float, float] | None = None) -> str:
    """库内封装名：同 kind 同引脚数**同尺寸**共用一个。

    尺寸必须进名字 —— 两个都是 5 脚但外形不同的 IC 若共用 `ic_5p`，库里只留下最后写的那一份，
    先写的那个实例就跟库对不上（lib_footprint_mismatch）。
    """
    kind = str(comp.get("kind") or "part")
    n = len(comp.get("pins") or []) or 2
    w, h = size or _fp_size(comp)
    return f"{kind}_{n}p_{w:g}x{h:g}"


def pad_positions(comp: dict[str, Any], i: int,
                  w: float | None = None, h: float | None = None) -> tuple[float, float]:
    """第 i 个引脚的焊盘坐标（相对器件中心，mm）。

    KiCad 与 EasyEDA 两个 writer 都调这里。焊盘位置要是各算一套，
    两份输出会看着像两颗不同的料，用户一对比就以为生成器错了。
    """
    if w is None or h is None:
        w, h = _fp_size(comp)
    n = len(comp.get("pins") or []) or 2
    pw, _ph, pitch, per_side = pad_geom(comp, w, h)
    if n == 1:
        return 0.0, 0.0
    if n == 2:
        return (-1 if i == 0 else 1) * (w / 2 - pw / 2), 0.0
    left = i < per_side
    idx = i if left else i - per_side
    px = (-1 if left else 1) * (w / 2 - pw / 2)
    py = -h / 2 + (idx + 1) * pitch if left else h / 2 - (idx + 1) * pitch
    return px, py


def _ref_place(w: float, h: float, tsize: float, edge_gap: float = 99.0) -> tuple[float, str]:
    """位号文字放哪：够得着板边就画在丝印层，够不着就退到 F.Fab。

    位号必须在**本体外侧**（收进框内会擦到焊盘 → silk_over_copper），
    但外沿要到 h/2+1.9mm，靠边的器件会被板边裁掉 → silk_edge_clearance。
    两者不可兼得，所以按离板边的真实距离二选一：放不下就退 F.Fab（不参与丝印检查）。
    """
    if edge_gap >= h / 2 + tsize * 0.8 + 1.6:
        return -h / 2 - 1.4, "F.SilkS"
    return -h / 2 - 1.0, "F.Fab"


def _fp_body(comp: dict[str, Any], pin_net: dict[str, int] | None,
             net_names: list[str], for_lib: bool = False,
             edge_gap: float = 99.0) -> tuple[str, list[str]]:
    """生成封装图形与焊盘。for_lib=True 时用于 .kicad_mod（无网络、占位文本）。"""
    cid = str(comp.get("id") or "?")
    w, h = _fp_size(comp)
    pins = [str(p) for p in (comp.get("pins") or [])] or ["1", "2"]

    body: list[str] = []
    # 丝印框必须画在焊盘外沿之外，否则 DRC 会报一堆 silk_over_copper
    body += _fp_rect(w + 0.6, h + 0.6, "F.SilkS")
    # courtyard 线宽规范是 0.05；且必须闭合，不然 malformed_courtyard
    body += _fp_rect(w + 1.0, h + 1.0, "F.CrtYd", 0.05)
    tsize = min(1.0, max(0.8, h * 0.4))     # 小于 0.8 会被 DRC 判 text_height
    ref_txt = "${REFERENCE}" if for_lib else cid
    val_txt = "${VALUE}" if for_lib else str(comp.get("value") or "")[:24]
    ref_y, ref_layer = _ref_place(w, h, tsize, edge_gap)
    body.append(_fp_text("reference", ref_txt, ref_y, ref_layer, tsize))
    body.append(_fp_text("value", val_txt, h / 2 + 1.4, "F.Fab", tsize))

    # 参数化焊盘：≥3 脚沿左右两边均分；两脚左右各一（几何见 pad_geom）
    n = len(pins)
    pw, ph, _pitch, _per_side = pad_geom(comp, w, h)
    for i, p in enumerate(pins):
        px, py = pad_positions(comp, i, w, h)
        nid = 0 if for_lib or not pin_net else pin_net.get(f"{cid}.{p}", 0)
        nm = net_names[nid - 1] if 0 < nid <= len(net_names) else ""
        body.append(_pad(str(i + 1), px, py, pw, ph, nid, nm))

    return f"Gen:{_mod_name(comp, (w, h))}", body


def _footprint(comp: dict[str, Any], x: float, y: float,
               pin_net: dict[str, int], net_names: list[str],
               edge_gap: float = 99.0) -> str:
    cid = str(comp.get("id") or "?")
    w, h = _fp_size(comp)
    _lib_name, body = _fp_body(comp, pin_net, net_names, for_lib=False, edge_gap=edge_gap)
    ref_y, ref_layer = _ref_place(w, h, min(1.0, max(0.8, h * 0.4)), edge_gap)
    target = str(comp.get("footprint") or "")
    descr = f"{comp.get('value') or cid}"
    if target:
        descr += f"；原目标封装 {target}（布线前请替换）"

    return "\n".join([
        f'  (footprint "Gen:{_esc(_mod_name(comp, (w, h)))}"',
        f'    (layer "F.Cu")',
        f'    (uuid "{_uid()}")',
        f'    (at {_f(x)} {_f(y)})',
        f'    (descr "{_esc(descr)}")',
        f'    (property "Reference" "{_esc(cid)}" (at 0 {_f(ref_y)} 0)',
        f'      (layer "{ref_layer}") (uuid "{_uid()}")',
        f'      (effects (font (size 1 1) (thickness 0.15)))',
        f'    )',
        f'    (property "Value" "{_esc(str(comp.get("value") or cid)[:40])}" (at 0 {_f(h / 2 + 1.4)} 0)',
        f'      (layer "F.Fab") (uuid "{_uid()}")',
        f'      (effects (font (size 1 1) (thickness 0.15)))',
        f'    )',
        f'    (attr smd)',
        *body,
        f'    (embedded_fonts no)',
        f'  )',
    ])


def _kicad_mod(comp: dict[str, Any]) -> tuple[str, str]:
    """库文件里的单个封装（.kicad_mod）。返回 (封装名, 文本)。"""
    _lib, body = _fp_body(comp, None, [], for_lib=True)
    w, h = _fp_size(comp)
    mod = _mod_name(comp, (w, h))
    target = str(comp.get("footprint") or "")
    text = "\n".join([
        f'(footprint "{_esc(mod)}"',
        f'  (version {PCB_VERSION})',
        f'  (generator "agent_sdk_eda")',
        f'  (generator_version "{GENERATOR_VERSION}")',
        f'  (layer "F.Cu")',
        f'  (descr "{_esc(str(comp.get("value") or mod))}{f"；原目标封装 {target}" if target else ""}")',
        f'  (tags "{_esc(f"gen {comp.get('kind') or ''} {w:g}x{h:g}mm")}")',
        f'  (attr smd)',
        *[line.replace("    ", "  ", 1) for line in body],
        f'  (embedded_fonts no)',
        f')',
    ])
    return mod, text


def _mounting_hole(mh: dict[str, Any], x: float, y: float) -> str:
    d = mh["diameter_mm"]
    body = [
        _hole_pad(d, bool(mh.get("plated"))),
        _fp_circle(d / 2 + 1.0, "F.CrtYd", 0.05),
    ]
    label = "MOUNTING_HOLE" + ("_PLATED" if mh.get("plated") else "")
    mod = f"mountinghole_{_f(d)}mm" + ("_plated" if mh.get("plated") else "")
    return "\n".join([
        f'  (footprint "Gen:{mod}"',
        f'    (layer "F.Cu")',
        f'    (uuid "{_uid()}")',
        f'    (at {_f(x)} {_f(y)})',
        f'    (descr "Mounting Hole {_f(d)}mm{" plated" if mh.get("plated") else " NPTH"}")',
        # 位号放 F.Fab 而不是 F.SilkS：孔在四角，丝印位号会被板边裁掉（silk_edge_clearance）
        f'    (property "Reference" "{_esc(mh.get("name") or "H")}" (at 0 {_f(-d / 2 - 1.2)} 0)',
        f'      (layer "F.Fab") (uuid "{_uid()}")',
        f'      (effects (font (size 1 1) (thickness 0.15)))',
        f'    )',
        f'    (property "Value" "{label}" (at 0 {_f(d / 2 + 1.2)} 0)',
        f'      (layer "F.Fab") (uuid "{_uid()}")',
        f'      (effects (font (size 1 1) (thickness 0.15)))',
        f'    )',
        *body,
        f'    (embedded_fonts no)',
        f'  )',
    ])


def _edge_cuts(b: dict[str, Any]) -> list[str]:
    pts = board_mod.outline_points(b)
    if len(pts) < 2:
        return []
    out = []
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
            continue
        out.append(
            f'  (gr_line (start {_f(ORIGIN_X + x1)} {_f(ORIGIN_Y + y1)}) '
            f'(end {_f(ORIGIN_X + x2)} {_f(ORIGIN_Y + y2)})\n'
            f'    (stroke (width 0.127) (type solid)) (layer "Edge.Cuts") (uuid "{_uid()}")\n'
            f'  )'
        )
    return out


def _zone(net_id: int, net_name: str, layer: str, b: dict[str, Any],
          inset: float = 0.4, priority: int = 1) -> str:
    pts = board_mod.outline_points(b)
    if len(pts) < 3:
        return ""
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    shrunk = []
    for px, py in pts:
        dx, dy = px - cx, py - cy
        d = math.hypot(dx, dy)
        if d <= inset + 0.01:      # 板太小，缩不动就退回原位
            shrunk.append((px, py))
            continue
        k = (d - inset) / d
        shrunk.append((cx + dx * k, cy + dy * k))
    xy = " ".join(f"(xy {_f(ORIGIN_X + p[0])} {_f(ORIGIN_Y + p[1])})" for p in shrunk)
    return "\n".join([
        f'  (zone',
        f'    (net {net_id})',
        f'    (net_name "{_esc(net_name)}")',
        f'    (layer "{layer}")',
        f'    (uuid "{_uid()}")',
        f'    (hatch edge 0.5)',
        f'    (priority {priority})',
        f'    (connect_pads yes (clearance 0.25))',
        f'    (min_thickness 0.25)',
        f'    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5) (island_removal_mode 0))',
        f'    (polygon',
        f'      (pts',
        f'        {xy}',
        f'      )',
        f'    )',
        f'  )',
    ])


# ---------------------------------------------------------------- 组装
_LAYERS = "\n".join([
    '\t\t(0 "F.Cu" signal "top_cu")',
    '\t\t(2 "B.Cu" signal "bottom_cu")',
    '\t\t(9 "F.Adhes" user "F.Adhesive")',
    '\t\t(11 "B.Adhes" user "B.Adhesive")',
    '\t\t(13 "F.Paste" user)',
    '\t\t(15 "B.Paste" user)',
    '\t\t(5 "F.SilkS" user "F.Silkscreen")',
    '\t\t(7 "B.SilkS" user "B.Silkscreen")',
    '\t\t(1 "F.Mask" user)',
    '\t\t(3 "B.Mask" user)',
    '\t\t(17 "Dwgs.User" user "User.Drawings")',
    '\t\t(19 "Cmts.User" user "User.Comments")',
    '\t\t(21 "Eco1.User" user "User.Eco1")',
    '\t\t(23 "Eco2.User" user "User.Eco2")',
    '\t\t(25 "Edge.Cuts" user)',
    '\t\t(27 "Margin" user)',
    '\t\t(31 "F.CrtYd" user "F.Courtyard")',
    '\t\t(29 "B.CrtYd" user "B.Courtyard")',
    '\t\t(35 "F.Fab" user)',
    '\t\t(33 "B.Fab" user)',
])


def build(net: dict[str, Any], b: dict[str, Any], name: str = "board",
          notes: list[str] | None = None) -> str:
    thickness = b["board"].get("thickness_mm") or 1.6
    ids, net_names = _net_table(net)
    pin_net = _pin_net_map(net, ids)
    gnd, _pwr = _gnd_and_power(net, ids)

    holes = list(b.get("mounting_holes") or []) + _auto_holes(b)
    pos = _place(net, b, holes, notes)

    parts_out: list[str] = []
    for mh in holes:
        parts_out.append(_mounting_hole(mh, ORIGIN_X + mh["x_mm"], ORIGIN_Y + mh["y_mm"]))
    for c in net.get("components") or []:
        p = pos.get(str(c.get("id")))
        if not p:
            continue
        # 位号画不画得下取决于这个位置离板边多远，位置定了才知道
        gap = board_mod.distance_to_edge(p[0], p[1], b)
        parts_out.append(_footprint(c, ORIGIN_X + p[0], ORIGIN_Y + p[1],
                                    pin_net, net_names, gap))

    nets_decl = ['  (net 0 "")'] + [
        f'  (net {i + 1} "{_esc(nm)}")' for i, nm in enumerate(net_names)
    ]
    zones = []
    if gnd:
        zones.append(_zone(gnd, net_names[gnd - 1], "B.Cu", b, 0.4, 1))

    head = "\n".join([
        f'(kicad_pcb (version {PCB_VERSION}) (generator "agent_sdk_eda") '
        f'(generator_version "{GENERATOR_VERSION}")',
        f'  (general',
        f'    (thickness {_f(thickness)})',
        f'    (legacy_teardrops no)',
        f'  )',
        f'  (paper "A4")',
        f'  (layers',
        _LAYERS,
        f'  )',
        f'  (setup',
        f'    (pad_to_mask_clearance 0)',
        f'    (allow_soldermask_bridges_in_footprints no)',
        f'    (tenting front back)',
        f'  )',
        "",
    ])
    body = "\n".join([
        *nets_decl,
        "",
        *parts_out,
        "",
        *_edge_cuts(b),
    ] + ([""] + zones if zones else []))
    return head + body + "\n)\n"


def _hole_mod(d: float, plated: bool) -> str:
    mod = f"mountinghole_{_f(d)}mm" + ("_plated" if plated else "")
    return "\n".join([
        f'(footprint "{_esc(mod)}"',
        f'  (version {PCB_VERSION})',
        f'  (generator "agent_sdk_eda")',
        f'  (generator_version "{GENERATOR_VERSION}")',
        f'  (layer "F.Cu")',
        f'  (descr "Mounting hole {_f(d)}mm {"plated" if plated else "NPTH"}")',
        f'  (attr exclude_from_pos_files exclude_from_bom)',
        f'  {_hole_pad(d, plated).strip()}',
        f'  {_fp_circle(d / 2 + 1.0, "F.CrtYd", 0.05).strip()}',
        f'  (embedded_fonts no)',
        f')',
    ])


def write(net: dict[str, Any], b: dict[str, Any], out_dir: Path | str,
          name: str = "board") -> dict[str, Any]:
    """写 .kicad_pcb + 自带封装库。任何异常都不外抛（不能拖垮硬件阶段）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    notes: list[str] = []
    try:
        text = build(net, b, name, notes)
        p = out_dir / f"{name}.kicad_pcb"
        p.write_text(text, encoding="utf-8")
        files.append({"path": str(p), "name": p.name, "size": p.stat().st_size})

        # 自带封装库：否则 KiCad 会报「当前配置中不包含封装库 Gen」
        pretty = out_dir / f"{name}.pretty"
        pretty.mkdir(exist_ok=True)
        mods: dict[str, str] = {}
        holes = list(b.get("mounting_holes") or []) + _auto_holes(b)
        for mh in holes:
            key = f"mountinghole_{_f(mh['diameter_mm'])}mm" + ("_plated" if mh.get("plated") else "")
            mods.setdefault(key, _hole_mod(mh["diameter_mm"], bool(mh.get("plated"))))
        for c in net.get("components") or []:
            mod, t = _kicad_mod(c)
            mods.setdefault(mod, t)
        for mod, t in mods.items():
            mp = pretty / f"{mod}.kicad_mod"
            mp.write_text(t, encoding="utf-8")
            files.append({"path": str(mp), "name": f"{pretty.name}/{mp.name}",
                          "size": mp.stat().st_size})

        tbl = out_dir / "fp-lib-table"
        tbl.write_text(
            "(fp_lib_table\n"
            "  (version 7)\n"
            f'  (lib (name "Gen")(type "KiCad")(uri "${{KIPRJMOD}}/{name}.pretty")'
            '(options "")(descr "Agent 生成的封装（参数化近似，布线前请替换官方封装）"))\n'
            ")\n",
            encoding="utf-8",
        )
        files.append({"path": str(tbl), "name": tbl.name, "size": tbl.stat().st_size})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "files": files}
    return {"ok": True, "files": files, "notes": notes, "size": sum(f["size"] for f in files)}
