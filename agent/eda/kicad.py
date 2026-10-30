"""网表 → KiCad 工程（.kicad_pro / .kicad_sch / .kicad_sym / sym-lib-table），纯文本生成，零第三方依赖。

三条硬约束：
  1. **自包含**：所有符号内嵌在 lib_symbols，不引用任何官方库，换台机器也能开
  2. **确定性**：同样的网表永远生成同样的图（uuid 除外），方便 diff 与回归
  3. **不抛异常**：网表再烂也要出文件 —— 出不了的图比难看的图更没用

坐标单位 mm，全部对齐 1.27mm 栅格（KiCad 原理图 50mil 栅格）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from . import symbols

SCH_VERSION = 20250610         # KiCad 10 原理图格式版本（KiCad 9/10 可开）
GENERATOR_VERSION = "10.0"
GRID = 1.27
X0, Y0 = 30.48, 25.4           # 起始位置：留出标题栏
GAP_X, GAP_Y = 25.4, 30.48     # 器件之间的间距（要容得下外侧的电源符号与 PWR_FLAG）
PWR_LEAD = 7.62               # 电源/地符号与引脚的间距（沿引脚朝外方向）
PER_ROW = 4

_PAPERS = [(4, 3, "A4"), (6, 4, "A3"), (8, 6, "A2"), (10, 8, "A1")]


def _q(v: float) -> float:
    return round(v / GRID) * GRID


def _f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s and s != "-0" else "0"


def _uid() -> str:
    return str(uuid.uuid4())


def _esc(s: Any) -> str:
    return str(s or "").replace("\\", "\\\\").replace('"', '\\"')


def _paper(cols: int, rows: int) -> str:
    for mc, mr, name in _PAPERS:
        if cols <= mc and rows <= mr:
            return name
    return "A0"


def _lib_name(comp: dict[str, Any]) -> str:
    """符号名：kind + 引脚数 + 引脚签名。

    必须带引脚签名 —— 否则「两个 5 脚的模块」会共用一个符号定义，
    后一个器件的引脚名被前一个覆盖（网表连得上、图纸上标签是错的）。
    """
    kind = comp.get("kind") or "box"
    pins = [str(p) for p in (comp.get("pins") or [])]
    if not pins:
        return f"Gen:{kind}_0"
    sig = hashlib.md5("|".join(pins).encode("utf-8")).hexdigest()[:6]
    return f"Gen:{kind}_{len(pins)}_{sig}"


def _net_kind(net: dict[str, Any]) -> str:
    return net.get("kind") or "signal"


def _net_has_power_out(n: dict[str, Any], net: dict[str, Any]) -> bool:
    """网络上有没有真正的电源输出脚（LDO 的 VOUT 之类）。

    有就不用挂 PWR_FLAG —— 两处 power_out 相连反而会被 ERC 判 pin_to_pin。
    """
    comps = {c["id"]: c for c in (net.get("components") or [])}
    for ep in n.get("points") or []:
        if "." not in ep:
            continue
        cid, pin = ep.split(".", 1)
        c = comps.get(cid)
        if c and symbols._etype(pin, c.get("kind") or "box") == "power_out":
            return True
    return False


# ---------------------------------------------------------------- 符号表
def _collect_symbols(net: dict[str, Any]) -> tuple[dict[str, tuple[str, dict]], str]:
    """生成 lib_symbols 段 + 每个元件对应的 (lib_id, 引脚坐标表)。"""
    by_key: dict[str, tuple[str, dict]] = {}
    chunks: list[str] = []
    seen: set[str] = set()

    for c in net.get("components") or []:
        key = _lib_name(c)
        if key not in seen:
            seen.add(key)
            text, pos = symbols.build(
                key, c.get("kind") or "box", c.get("pins") or [],
                value=str(c.get("part_name") or c.get("value") or c.get("kind") or ""),
                ref=str(c.get("id") or "")[:1] or "U",
            )
            chunks.append(text)
            by_key[key] = (key, pos)

    # 电源 / 地符号（每种只定义一次）
    for key, kind, label in (("Gen:GND", "gnd", "GND"), ("Gen:+3V3", "power", "+3V3"),
                             ("Gen:+5V", "power", "+5V"), ("Gen:VIN", "power", "VIN")):
        text, pos = symbols.build(key, kind, [label], value=label, ref="#PWR")
        chunks.append(text)
        by_key[key] = (key, pos)

    # PWR_FLAG：给 ERC 声明电源驱动源
    text, pos = symbols.build("Gen:PWR_FLAG", "pwr_flag", ["~"], value="PWR_FLAG", ref="#FLG")
    chunks.append(text)
    by_key["Gen:PWR_FLAG"] = ("Gen:PWR_FLAG", pos)

    return by_key, "\n".join(chunks)


def _bfs_order(net: dict[str, Any]) -> list[str]:
    """按网络连通性给器件排序：主控先出，相连的器件紧跟着。

    比按 kind 排好得多 —— 相连的器件落在相邻格子里，连线就不会横穿整张图。
    """
    comps = list(net.get("components") or [])
    by_id = {c["id"]: c for c in comps}
    adj: dict[str, set[str]] = {cid: set() for cid in by_id}
    for n in net.get("nets") or []:
        ids = [p.split(".", 1)[0] for p in (n.get("points") or []) if "." in p]
        ids = [i for i in dict.fromkeys(ids) if i in by_id]
        for a in ids:
            for b in ids:
                if a != b:
                    adj[a].add(b)

    order: list[str] = []
    seen: set[str] = set()
    seeds = sorted(comps, key=lambda c: (-len(c.get("pins") or []), str(c.get("id"))))
    for s in seeds:
        sid = s["id"]
        if sid in seen:
            continue
        q = [sid]
        seen.add(sid)
        while q:
            cur = q.pop(0)
            order.append(cur)
            nb = sorted((x for x in adj[cur] if x not in seen),
                        key=lambda x: (-len(adj[x]), x))
            for x in nb:
                seen.add(x)
                q.append(x)
    return order


# ---------------------------------------------------------------- 布局
def _layout(net: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """网格布局：列宽/行高按该列该行最宽的器件算，不留大块空白。"""
    comps = {c["id"]: c for c in (net.get("components") or [])}
    order = _bfs_order(net)
    if not order:
        return {}

    size = {
        cid: symbols.extent(comps[cid].get("kind") or "box", comps[cid].get("pins") or [])
        for cid in order
    }
    cols = min(PER_ROW, len(order))
    rows = (len(order) + cols - 1) // cols
    col_w = [0.0] * cols
    row_h = [0.0] * rows
    for i, cid in enumerate(order):
        col, row = i % cols, i // cols
        col_w[col] = max(col_w[col], size[cid][0])
        row_h[row] = max(row_h[row], size[cid][1])

    xs: list[float] = []
    acc = X0
    for w in col_w:
        xs.append(acc + w / 2)
        acc += w + GAP_X
    ys: list[float] = []
    acc = Y0
    for h in row_h:
        ys.append(acc + h / 2)
        acc += h + GAP_Y

    return {
        cid: (_q(xs[i % cols]), _q(ys[i // cols]))
        for i, cid in enumerate(order)
    }


def _pin_points(net: dict[str, Any], pos: dict[str, tuple[float, float]],
                syms: dict[str, tuple[str, dict]]) -> dict[str, list[tuple[float, float, float, float]]]:
    """net 名 → [(x, y, ox, oy)]。

    ox/oy 是「引脚朝外」的单位方向：电源/地符号必须沿这个方向引出，
    否则竖直插到引脚列中间会正好压在相邻引脚上（等于把两个网络短路）。
    """
    comp_by_id = {c["id"]: c for c in (net.get("components") or [])}
    out: dict[str, list[tuple[float, float, float, float]]] = {}
    for n in net.get("nets") or []:
        pts: list[tuple[float, float, float, float]] = []
        for endpoint in n.get("points") or []:
            if "." not in endpoint:
                continue
            cid, pin = endpoint.split(".", 1)
            c = comp_by_id.get(cid)
            if not c or cid not in pos:
                continue
            _, pinmap = syms.get(_lib_name(c), (None, {}))
            off = (pinmap or {}).get(pin)
            if off is None:
                continue
            cx, cy = pos[cid]
            # 符号库坐标 Y 轴朝上、图纸坐标 Y 轴朝下 —— 必须取反，否则连线整体错位
            gx, gy = _q(cx + off[0]), _q(cy - off[1])
            dx, dy = gx - cx, gy - cy
            if abs(dx) >= abs(dy):
                ox, oy = (1.0 if dx >= 0 else -1.0), 0.0
            else:
                ox, oy = 0.0, (1.0 if dy >= 0 else -1.0)
            if ox == 0.0 and oy == 0.0:
                oy = 1.0
            pts.append((gx, gy, ox, oy))
        if pts:
            out[n["name"]] = pts
    return out


# ---------------------------------------------------------------- 主体
def build_schematic(net: dict[str, Any], project_name: str = "schematic") -> str:
    comps = list(net.get("components") or [])
    if not comps:
        return _empty_sch(project_name)

    syms, lib_text = _collect_symbols(net)
    pos = _layout(net)
    pins = _pin_points(net, pos, syms)

    cols = max(1, min(PER_ROW, len(comps)))
    rows = max(1, (len(comps) + PER_ROW - 1) // PER_ROW)
    root_uuid = _uid()          # 图纸 UUID：符号实例的 instances/path 要指向它

    body: list[str] = []

    # ---- 元件实例
    for c in comps:
        cid = c["id"]
        if cid not in pos:
            continue
        x, y = pos[cid]
        lib_id = _lib_name(c)
        ref = str(cid)
        value = str(c.get("value") or c.get("type") or c.get("kind") or "")[:60]
        fp = str(c.get("footprint") or "")
        body.append(
            f'  (symbol (lib_id "{lib_id}") (at {_f(x)} {_f(y)} 0) (unit 1)\n'
            f'    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)\n'
            f'    (uuid "{_uid()}")\n'
            f'    (property "Reference" "{_esc(ref)}" (at {_f(x + 2.54)} {_f(y + 1.27)} 0)\n'
            f'      (effects (font (size 1.27 1.27)) (justify left))\n'
            f'    )\n'
            f'    (property "Value" "{_esc(value)}" (at {_f(x + 2.54)} {_f(y - 1.27)} 0)\n'
            f'      (effects (font (size 1.27 1.27)) (justify left))\n'
            f'    )\n'
            f'    (property "Footprint" "{_esc(fp)}" (at {_f(x)} {_f(y)} 0)\n'
            f'      (hide yes)\n'
            f'      (effects (font (size 1.27 1.27)))\n'
            f'    )\n'
            + "".join(
                # 实例里的 pin 引用的是「引脚号」，不是引脚名；符号里号就是 1..n
                f'    (pin "{i}" (uuid "{_uid()}"))\n'
                for i in range(1, len(syms.get(lib_id, (None, {}))[1] or {}) + 1)
            )
            + f'    (instances\n'
            f'      (project "{_esc(project_name)}"\n'
            f'        (path "/{root_uuid}" (reference "{_esc(ref)}") (unit 1))\n'
            f'      )\n'
            f'    )\n'
            f'  )'
        )

    # ---- 悬空引脚标 no_connect（ERC 才不会满屏 "pin not connected"）
    connected: set[str] = set()
    for n in net.get("nets") or []:
        for ep in n.get("points") or []:
            if "." in ep:
                connected.add(ep)
    for c in comps:
        cid = c["id"]
        if cid not in pos:
            continue
        x, y = pos[cid]
        _, pinmap = syms.get(_lib_name(c), (None, {}))
        for pin, off in (pinmap or {}).items():
            if f"{cid}.{pin}" in connected:
                continue
            body.append(
                f'  (no_connect (at {_f(_q(x + off[0]))} {_f(_q(y - off[1]))}) (uuid "{_uid()}"))'
            )

    # ---- 连线 + 电源符号（循环变量必须避开 net 本身，否则会把自己覆盖掉）
    for n in net.get("nets") or []:
        pts = pins.get(n["name"]) or []
        if not pts:
            continue
        kind = _net_kind(n)

        # 电源/地：每个脚就近接一个电源符号（真实图纸的画法，不拉长线到统一 hub）
        if kind in ("gnd", "power"):
            name = str(n.get("name") or "VCC").upper()
            if kind == "gnd":
                lib = "Gen:GND"
            elif "3V3" in name or "3.3" in name:
                lib = "Gen:+3V3"
            elif "5V" in name:
                lib = "Gen:+5V"
            else:
                lib = "Gen:VIN"
            need_flag = not _net_has_power_out(n, net)
            for i, (gx, gy, ox, oy) in enumerate(pts):
                sx, sy = _q(gx + ox * PWR_LEAD), _q(gy + oy * PWR_LEAD)
                _add_power(body, lib, name, sx, sy, gx, gy, project_name, root_uuid)
                if i == 0 and need_flag:
                    # 每个没有电源输出脚的电源网络挂一个 PWR_FLAG（ERC 才认它「有驱动源」）
                    fx, fy = _q(sx + ox * PWR_LEAD), _q(sy + oy * PWR_LEAD)
                    _add_power(body, "Gen:PWR_FLAG", "PWR_FLAG", fx, fy, sx, sy,
                               project_name, root_uuid, "#FLG?")
            continue

        if len(pts) < 2:
            continue
        hx = _q(sum(p[0] for p in pts) / len(pts))
        hy = _q(sum(p[1] for p in pts) / len(pts))
        for gx, gy, ox, oy in pts:
            # 先从引脚沿「朝外」方向走 2.54，再拐向汇合点 —— 严格正交，
            # 且不会从器件本体上横穿过去（横穿会看起来像把器件短路了）
            ex, ey = _q(gx + ox * 2.54), _q(gy + oy * 2.54)
            if ox:
                seg = [(gx, gy), (ex, ey), (ex, hy), (hx, hy)]
            else:
                seg = [(gx, gy), (ex, ey), (hx, ey), (hx, hy)]
            seg = _dedupe(seg)
            if len(seg) < 2:
                continue
            body.append(_wire(seg))
        if len(pts) >= 3:
            body.append(
                f'  (junction (at {_f(hx)} {_f(hy)}) (diameter 1.016) (color 0 0 0 0) (uuid "{_uid()}"))'
            )

    head = (
        f'(kicad_sch\n'
        f'  (version {SCH_VERSION})\n'
        f'  (generator "agent_sdk_eda")\n'
        f'  (generator_version "{GENERATOR_VERSION}")\n'
        f'  (uuid "{root_uuid}")\n'
        f'  (paper "{_paper(cols, rows)}")\n'
        f'  (lib_symbols\n{lib_text}\n  )\n'
    )
    tail = (
        f'  (sheet_instances\n'
        f'    (path "/" (page "1"))\n'
        f'  )\n'
        f'  (embedded_fonts no)\n'
        f')\n'
    )
    return head + "\n".join(body) + "\n" + tail


def _dedupe(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """去掉相邻重复点：KiCad 不接受零长度线段。"""
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
    return out


def _wire(pts: list[tuple[float, float]]) -> str:
    """折线 → 多根 wire。

    KiCad 的 (wire) 只接受 **两个端点**（一段直线）；塞 3 个以上的点会
    整文件解析失败（报「加载原理图失败」，不给行号）。所以这里按相邻点拆段。
    """
    segs: list[str] = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if (x1, y1) == (x2, y2):
            continue
        segs.append(
            f'  (wire (pts (xy {_f(x1)} {_f(y1)}) (xy {_f(x2)} {_f(y2)}))\n'
            f'    (stroke (width 0) (type default))\n'
            f'    (uuid "{_uid()}")\n'
            f'  )'
        )
    return "\n".join(segs)


def _add_power(body: list[str], lib: str, name: str, sx: float, sy: float,
               pin_x: float, pin_y: float, project: str = "", root_uuid: str = "",
               ref: str = "#PWR?") -> None:
    body.append(
        f'  (symbol (lib_id "{lib}") (at {_f(sx)} {_f(sy)} 0) (unit 1)\n'
        f'    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)\n'
        f'    (uuid "{_uid()}")\n'
        f'    (property "Reference" "{_esc(ref)}" (at {_f(sx)} {_f(sy + 2.54)} 0)\n'
        f'      (hide yes)\n'
        f'      (effects (font (size 1.27 1.27)))\n'
        f'    )\n'
        f'    (property "Value" "{_esc(name)}" (at {_f(sx)} {_f(sy + 1.27)} 0)\n'
        f'      (effects (font (size 1.27 1.27)) (justify left))\n'
        f'    )\n'
        f'    (pin "1" (uuid "{_uid()}"))\n'
        f'    (instances\n'
        f'      (project "{_esc(project)}"\n'
        f'        (path "/{root_uuid}" (reference "{_esc(ref)}") (unit 1))\n'
        f'      )\n'
        f'    )\n'
        f'  )'
    )
    body.append(_wire(_dedupe([(sx, sy), (pin_x, pin_y)])))


def _empty_sch(name: str) -> str:
    return (
        f'(kicad_sch\n'
        f'  (version {SCH_VERSION})\n'
        f'  (generator "agent_sdk_eda")\n'
        f'  (generator_version "{GENERATOR_VERSION}")\n'
        f'  (uuid "{_uid()}")\n'
        f'  (paper "A4")\n'
        f'  (lib_symbols)\n'
        f'  (sheet_instances\n    (path "/" (page "1"))\n  )\n'
        f'  (embedded_fonts no)\n)\n'
    )


def build_project(net: dict[str, Any], name: str = "schematic") -> str:
    """最小 .kicad_pro。字段不全时 KiCad 会按默认补全；单独打开 .kicad_sch 亦可。"""
    root_uuid = _uid()
    proj = {
        "board": {
            "3dviewports": [],
            "design_settings": {
                "defaults": {
                    "board_outline_line_width": 0.1,
                    "copper_line_width": 0.2,
                    "copper_text_size_h": 1.5,
                    "copper_text_size_v": 1.5,
                    "copper_text_thickness": 0.3,
                    "other_copper_line_width": 0.15,
                    "silk_line_width": 0.12,
                    "silk_text_size_h": 1.0,
                    "silk_text_size_v": 1.0,
                    "silk_text_thickness": 0.15,
                },
                "diff_pair_dimensions": [],
                "drc_exclusions": [],
                "meta": {"version": 2},
                "rules": {"min_copper_edge_clearance": 0.0, "solder_mask_to_copper_clearance": 0.0},
                "track_widths": [0.25],
                "via_dimensions": [{"diameter": 0.8, "drill": 0.4}],
            },
            "layer_presets": [],
            "viewports": [],
        },
        "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{name}.kicad_pro", "version": 2},
        "net_settings": {
            "classes": [{
                "bus_width": 12.0, "clearance": 0.2, "diff_pair_gap": 0.25,
                "diff_pair_width": 0.2, "line_style": 1, "microvia_diameter": 0.3,
                "microvia_drill": 0.1, "name": "Default", "pcb_color": "rgb(0, 0, 0)",
                "schematic_color": "rgb(0, 0, 0)", "track_width": 0.25,
                "via_diameter": 0.8, "via_drill": 0.4, "wire_width": 6.0,
            }],
            "meta": {"version": 3},
            "net_colors": None,
        },
        "pcbnew": {
            "last_paths": {"gencad": "", "idf": "", "netlist": "", "specctra_dsn": "", "step": "", "svg": ""},
            "page": {"paper_size": 4},
        },
        "schematic": {
            "annotate_start_num": 0,
            "drawing": {"dashed_lines_dash_length_ratio": 12.0, "dashed_lines_gap_length_ratio": 3.0,
                        "default_line_thickness": 6.0, "default_text_size": 50.0,
                        "field_names": [], "intersheets_ref_prefix": "", "intersheets_ref_short": False,
                        "intersheets_ref_show": False, "intersheets_ref_suffix": "",
                        "junction_size_choice": 3, "label_size_ratio": 0.375,
                        "pin_symbol_size": 25.0, "text_offset_ratio": 0.15},
            "legacy_lib_dir": "",
            "legacy_lib_list": [],
            "meta": {"version": 1},
            "net_format_name": "",
            "page": {"paper_size": "A4"},
            "spice_current_sheet_as_root": False,
            "spice_external_command": "",
            "spice_model_current_sheet_as_root": True,
            "spice_save_all_currents": False,
            "spice_save_all_voltages": False,
            "subpart_first_id": 65,
            "subpart_id_separator": 0,
        },
        "sheets": [[root_uuid, "/"]],
        "text_variables": {},
    }
    return json.dumps(proj, ensure_ascii=False, indent=2) + "\n"


def build(net: dict[str, Any], name: str = "schematic") -> dict[str, str]:
    """生成全部工程文件文本。

    除了原理图与工程文件，还额外产出符号库 + 库表：
    内嵌 lib_symbols 只能保证「打得开」，ERC 仍会提示「符号库 'Gen' 未配置」；
    带上 Gen.kicad_sym + sym-lib-table 之后工程完全自包含，ERC 也干净。
    """
    _, lib_text = _collect_symbols(net)
    lib_file = lib_text.replace('(symbol "Gen:', '(symbol "')
    return {
        "kicad_pro": build_project(net, name),
        "kicad_sch": build_schematic(net, name),
        "kicad_sym": (
            f'(kicad_symbol_lib\n'
            f'  (version 20231120)\n'
            f'  (generator "agent_sdk_eda")\n'
            f'{lib_file}\n'
            f')\n'
        ),
        "sym-lib-table": (
            f'(sym_lib_table\n'
            f'  (version 7)\n'
            f'  (lib (name "Gen")(type "KiCad")(uri "${{KIPRJMOD}}/{name}.kicad_sym")'
            f'(options "")(descr "符号由智能体生成"))\n'
            f')\n'
        ),
    }


def write(net: dict[str, Any], out_dir: Path | str, name: str = "schematic") -> dict[str, Any]:
    """写盘，返回 {"files": [...], "size": ...}。任何异常都不外抛。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    try:
        docs = build(net, name)
        for suffix in ("kicad_pro", "kicad_sch", "kicad_sym"):
            p = out_dir / f"{name}.{suffix}"
            p.write_text(docs[suffix], encoding="utf-8")
            files.append({"path": str(p), "name": p.name, "size": p.stat().st_size})
        # 库表文件名固定，工程里靠 ${KIPRJMOD} 相对定位
        p = out_dir / "sym-lib-table"
        p.write_text(docs["sym-lib-table"], encoding="utf-8")
        files.append({"path": str(p), "name": p.name, "size": p.stat().st_size})
    except Exception as e:  # noqa: BLE001  出图失败不能拖垮硬件阶段
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "files": files}
    return {"ok": True, "files": files, "size": sum(f["size"] for f in files)}


def summary(net: dict[str, Any]) -> str:
    s = net.get("stats") or {}
    return (
        f'{s.get("components", 0)} 个器件 / {s.get("connections", 0)} 条连线 / '
        f'{s.get("nets", 0)} 个网络'
    )
