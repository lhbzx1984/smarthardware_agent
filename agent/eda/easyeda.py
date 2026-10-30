"""嘉立创 EDA 专业版 `.epro` 工程 writer。

**格式依据是用户用客户端导出的真实工程**（立创·实战派ESP32-S31开发板），
而不是官方 V3 规范文档 —— 两者不兼容，以真机为准：

  .epro = zip( project.json
               PCB/<uuid>.epcb             每个 PCB 一个文件
               FOOTPRINT/<uuid>.efoo       每个封装一个文件
               SYMBOL/<uuid>.esym          每种符号一个文件
               SHEET/<原理图uuid>/1.esch   每页原理图一个文件 )

每个文档内部是「**逐行一个 JSON 数组**」（JSON Lines，数组首元素是行类型），
例如 `["LAYER",1,"TOP","Top Layer",3,"#ff0000",1,"#7f0000",0.5]`。

早先按 V3 规范写的 `{外层}||{内层}|` 单文件 `.epru` 方案已废弃：
那个格式客户端 4.1.78 导入后工程树能建出来、但 PCB 画布是空的。

长度单位是 **mil**（1mm = 39.3701）。板框、器件、焊盘坐标全量平移到正象限
（板框左上角落在 (25,25)mm）—— 负坐标会被视图定位推到远处，看着像空板。

能力边界：PCB 段（板框 / 器件 / 焊盘 / 网络 / 安装孔）+ 原理图段
（符号 / 器件 / 每引脚一根引出线挂 NET 标签），**都不布线**。
原理图段的格式细节见 `sch.py`（单位、COMPONENT/ATTR/PIN 行各几段都在那边）。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from . import board as board_mod
from . import pcb as pcb_mod
from . import sch as sch_mod
from . import lib as lib_mod

MIL = 39.37007874015748           # 1mm 等于多少 mil
MARGIN_MM = 25.0                  # 板框左上角在画布里的落点

L_TOP = 1
L_BOTTOM = 2
L_TOP_SILK = 3
L_OUTLINE = 11
L_MULTI = 12

_HEAD_FILE = Path(__file__).with_name("data") / "epcb_head.json"
_head_cache: list[list] | None = None

# 贴片焊盘没有钻孔，真机用 0.03937mil(=1µm) 占位而不是 0
_NO_DRILL = 0.03937


def _head() -> list[list]:
    """样板头：DOCTYPE/HEAD/CANVAS/LAYER×92/LAYER_PHYS/PREFERENCE/RULE/PRIMITIVE…

    这些都是与具体板子无关的固定配置，逐字节照抄真机导出的工程，
    免得自己猜哪个字段编辑器认、哪个不认。
    """
    global _head_cache
    if _head_cache is None:
        _head_cache = json.loads(_HEAD_FILE.read_text(encoding="utf-8"))
    return [list(r) for r in _head_cache]


def _m(mm: float) -> float:
    return round(mm * MIL, 4)


def _uid(text: str) -> str:
    """16 位十六进制，和真机的 uuid 同形。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


class _Eid:
    """元素 id 发号器。真机就是 e1、e2、e3… 顺序递增。"""

    def __init__(self, start: int = 0) -> None:
        self.n = start

    def __call__(self) -> str:
        self.n += 1
        return f"e{self.n}"


def _rect_seq(w: float, h: float) -> list:
    """闭合矩形点列（相对中心，mm → mil），格式对齐真机：
    [x1,y1,"L",x2,y2,x3,y3,...]"""
    pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2), (-w / 2, -h / 2)]
    seq: list = [_m(pts[0][0]), _m(pts[0][1]), "L"]
    for x, y in pts[1:]:
        seq += [_m(x), _m(y)]
    return seq


# ---------------------------------------------------------------- 封装文档

def _footprint_rows(comp: dict[str, Any], title: str, uuid: str) -> tuple[list[list], list[str]]:
    """生成一个 .efoo 文档。返回 (行, 焊盘的 eid 列表)。

    焊盘 eid 是按引脚顺序来的，PCB 段的 PAD_NET 要靠它对上号。
    """
    w, h = pcb_mod._fp_size(comp)
    pw, ph, _pitch, _per = pcb_mod.pad_geom(comp, w, h)
    pins = [str(p) for p in (comp.get("pins") or [])] or ["1", "2"]

    rows: list[list] = [
        ["DOCTYPE", "FOOTPRINT", "1.8"],
        ["HEAD", {"editorVersion": "2.2.C", "importFlag": 0,
                  "uuid": uuid, "title": title}],
        ["CANVAS", 0, 0, "mm", 10, 10, 0.5, 0.5, 1, 1, 0, 0, 5],
    ]
    rows += [list(r) for r in _head() if r[0] == "LAYER"]
    rows.append(["ACTIVE_LAYER", L_TOP])
    rows += [list(r) for r in _head() if r[0] == "LAYER_PHYS"]
    rows.append(["PREFERENCE", 0, 10, 0, 24, 12, 1, 0, "L45", 1, 1, 0, 1, "",
                 0, 1, "OPTIMIZA_NONE", 0, "OPTIMIZA_NONE", 1, 1])

    eid = _Eid()
    rows.append(["ATTR", eid(), 0, "", 3, None, None, "Designator", "U?",
                 0, 0, "default", 67.5, 6, 0, 0, 3, 0, 0, 0, 0, False])

    # 器件外形框（48 = COMPONENT_SHAPE，编辑器靠它画丝印框）
    rows.append(["POLY", eid(), 0, "", 48, 2, _rect_seq(w + 0.6, h + 0.6), 0])
    # 四条边线（3 = 顶层丝印，线宽 6mil≈0.15mm）
    hw, hh = (w + 0.6) / 2, (h + 0.6) / 2
    for (x1, y1), (x2, y2) in (
        ((-hw, -hh), (hw, -hh)), ((hw, -hh), (hw, hh)),
        ((hw, hh), (-hw, hh)), ((-hw, hh), (-hw, -hh)),
    ):
        rows.append(["POLY", eid(), 0, "", L_TOP_SILK, 6,
                     [_m(x1), _m(y1), "L", _m(x2), _m(y2)], 0])
    # 1 脚标记（49 = COMPONENT_MARKING）
    if len(pins) > 1:
        mx = -hw + min(0.6, w * 0.15)
        rows.append(["FILL", eid(), 0, "", 49, 0.2, 0,
                     [["CIRCLE", _m(mx), _m(-hh + min(0.6, h * 0.15)), _m(0.35)]], 0])

    pad_eids: list[str] = []
    for i, pin in enumerate(pins):
        px, py = pcb_mod.pad_positions(comp, i, w, h)
        pe = eid()
        pad_eids.append(pe)
        rows.append(["PAD", pe, 0, "", L_TOP, pin,
                     _m(px), _m(py), 0,
                     ["ROUND", _NO_DRILL, _NO_DRILL],
                     ["RECT", _m(pw), _m(ph), 0],
                     [], 0, 0, 0, 1, 0, None, None, None, None, 0])
    return rows, pad_eids


def _hole_rows(mh: dict[str, Any], uuid: str) -> tuple[list[list], str]:
    """安装孔做成一个单焊盘封装：layer 12(MULTI) 通孔，钻孔=焊盘=孔径（无环）。"""
    d = float(mh.get("diameter_mm") or 3.2)
    title = f"MountingHole_{d:g}mm"
    rows: list[list] = [
        ["DOCTYPE", "FOOTPRINT", "1.8"],
        ["HEAD", {"editorVersion": "2.2.C", "importFlag": 0, "uuid": uuid, "title": title}],
        ["CANVAS", 0, 0, "mm", 10, 10, 0.5, 0.5, 1, 1, 0, 0, 5],
    ]
    rows += [list(r) for r in _head() if r[0] == "LAYER"]
    rows.append(["ACTIVE_LAYER", L_TOP])
    rows += [list(r) for r in _head() if r[0] == "LAYER_PHYS"]
    rows.append(["PREFERENCE", 0, 10, 0, 24, 12, 1, 0, "L45", 1, 1, 0, 1, "",
                 0, 1, "OPTIMIZA_NONE", 0, "OPTIMIZA_NONE", 1, 1])
    eid = _Eid()
    rows.append(["ATTR", eid(), 0, "", 3, None, None, "Designator", "H?",
                 0, 0, "default", 67.5, 6, 0, 0, 3, 0, 0, 0, 0, False])
    pad_eid = eid()
    rows.append(["PAD", pad_eid, 0, "", L_MULTI, "1", 0.0, 0.0, 0,
                 ["ROUND", _m(d), _m(d)],
                 ["CIRCLE", _m(d), _m(d), 0],
                 [], 0, 0, 0, 1, 0, None, None, None, None, 0])
    return rows, pad_eid


# ---------------------------------------------------------------- PCB 文档

def _pcb_rows(net: dict[str, Any], b: dict[str, Any], pos: dict[str, tuple[float, float]],
              fp_of: dict[str, str], holes: list[dict[str, Any]],
              hole_fp: dict[int, str], shift: tuple[float, float],
              pads_of: dict[str, list[str]],
              dev_of: dict[str, str],
              hole_dev: dict[int, str],
              hole_pad: dict[str, str] | None = None,
              lib_of: dict[str, dict[str, Any]] | None = None) -> list[list]:
    sx, sy = shift

    def X(x_mm: float) -> float:
        return _m(x_mm + sx)

    def Y(y_mm: float) -> float:
        return _m(y_mm + sy)

    rows = _head()
    rows = [r for r in rows if r[0] != "DOCTYPE"]
    rows.insert(0, ["DOCTYPE", "PCB", "1.8"])
    rows = [r if r[0] != "HEAD" else
            ["HEAD", {"editorVersion": "2.2.C", "importFlag": 0}] for r in rows]

    lib_of = lib_of or {}
    ids, net_names = pcb_mod._net_table(net)
    pin_net = pcb_mod._pin_net_map(net, ids)

    # 网络表必须先声明，PAD_NET 才引用得到
    for name in net_names:
        if name:
            rows.append(["NET", name, None, None, 1, None, 0, None])

    rows.append(["PANELIZE", 0, 2, 1, 196.8504, 0, 0])

    eid = _Eid()

    # 板框：outline_points 是中心原点的点列，**首尾不重复**（KiCad 那边逐段连线补闭合）。
    # 这里必须显式连回起点，否则最后一段缺边、板框不闭合。
    pts = list(board_mod.outline_points(b))
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    seq: list = [X(pts[0][0]), Y(pts[0][1]), "L"]
    for x, y in pts[1:]:
        seq += [X(x), Y(y)]
    rows.append(["POLY", eid(), 0, "", L_OUTLINE, _m(0.2), seq, 0])

    # 器件
    for comp in net.get("components") or []:
        ref = str(comp.get("id") or "?")
        if ref not in pos:
            continue
        x, y = pos[ref]
        ce = eid()
        rows.append(["COMPONENT", ce, 0, L_TOP, X(x), Y(y), 0,
                     {"Unique ID": f"gg{ref}", "Reuse Block": "",
                      "Group ID": "", "Channel ID": ""}, 0])
        rows.append(["ATTR", eid(), 0, ce, 4, None, None,
                     "Footprint", fp_of.get(ref, ""),
                     0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, False])
        # 真机每个器件都有 Footprint + Device + Designator 三条：缺 Device 时
        # 客户端找不到库元件，器件显示不出来（KiCad 的 Pro 导入器直接 0 个）
        rows.append(["ATTR", eid(), 0, ce, 4, None, None,
                     "Device", dev_of.get(ref, ""),
                     0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, False])
        # 位号画在本体外侧（真机的 Designator 是绝对坐标）
        rows.append(["ATTR", eid(), 0, ce, 4, X(x), Y(y - 2.2),
                     "Designator", ref,
                     0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, False])

        pins = [str(p) for p in (comp.get("pins") or [])] or ["1", "2"]
        # 网表端点常写引脚**名**（U1.VCC），而封装焊盘只认**号**（5）。
        # 这里用符号的 NAME→NUMBER 映射转一道，再按焊盘号取封装里那个焊盘的真实 eid ——
        # 早先按引脚下标取，器件引脚名一多就整体错位，网络全挂到别的焊盘上。
        part = lib_of.get(ref)
        pad_map = pads_of.get(ref) or {}
        pad_keys = list(pad_map)
        for i, pin in enumerate(pins):
            # 焊盘号三级兜底：库符号 NAME→NUMBER 映射 > 引脚本身就是焊盘号 >
            # 按序号对应。最后一级是给「网表写引脚名、库符号写焊盘位号」这种情况
            # 用的（USB-C 的 VBUS 未必就叫 A4），至少不会挂到不存在的焊盘上。
            padno = ""
            if part:
                cand = str(lib_mod.pin_number(part, pin))
                if cand in pad_map:
                    padno = cand
            if not padno and pin in pad_map:
                padno = pin
            if not padno and i < len(pad_keys):
                padno = pad_keys[i]
            nid = pin_net.get(f"{ref}.{pin}")
            rows.append(["PAD_NET", ce, padno,
                         net_names[nid - 1] if nid and nid <= len(net_names) else "",
                         pad_map.get(padno, ""), 0])

    # 安装孔：单焊盘封装实例，位号 H1/H2…
    for i, mh in enumerate(holes):
        x, y = float(mh.get("x_mm") or 0), float(mh.get("y_mm") or 0)
        ce = eid()
        rows.append(["COMPONENT", ce, 0, L_TOP, X(x), Y(y), 0,
                     {"Unique ID": f"ggh{i + 1}", "Reuse Block": "",
                      "Group ID": "", "Channel ID": ""}, 0])
        rows.append(["ATTR", eid(), 0, ce, 4, None, None,
                     "Footprint", hole_fp.get(i, ""),
                     0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, False])
        rows.append(["ATTR", eid(), 0, ce, 4, None, None,
                     "Device", hole_dev.get(i, ""),
                     0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, False])
        rows.append(["ATTR", eid(), 0, ce, 4, X(x), Y(y - 3.0),
                     "Designator", str(mh.get("name") or f"H{i + 1}"),
                     0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, False])
        rows.append(["PAD_NET", ce, "", "",
                     (hole_pad or {}).get(hole_fp.get(i, ""), "e2"), 0])
    return rows


# ---------------------------------------------------------------- 装配

def write(net: dict[str, Any], b: dict[str, Any],
          out_dir: Path | str, name: str = "board") -> dict[str, Any]:
    """写出 <name>.epro。返回 {ok, files, size, note}。"""
    out = Path(out_dir)
    files: list[dict[str, Any]] = []
    try:
        out.mkdir(parents=True, exist_ok=True)

        holes = list(b.get("mounting_holes") or []) + pcb_mod._auto_holes(b)
        pos = pcb_mod._place(net, b, holes)
        outline = board_mod.outline_points(b)
        bw = max(p[0] for p in outline) - min(p[0] for p in outline)
        bh = max(p[1] for p in outline) - min(p[1] for p in outline)
        shift = (bw / 2 + MARGIN_MM, bh / 2 + MARGIN_MM)

        # 每个器件去立创官方库取真符号 + 真封装。
        # 这是「基于元件库」的开关：取到用官方库（封装是真实焊盘尺寸），
        # 取不到（离线 / 型号搜不到）才退回本地参数化图形。
        lib_of: dict[str, dict[str, Any]] = {}
        if lib_mod.online():
            for comp in net.get("components") or []:
                ref = str(comp.get("id") or "?")
                part = lib_mod.resolve(comp)
                if part:
                    lib_of[ref] = part

        # 同 kind 同脚数同尺寸共用一个封装，UUID 要稳定（同样的料生成同一份）
        fp_of: dict[str, str] = {}
        fp_rows: dict[str, tuple[str, list[list]]] = {}
        pads_of: dict[str, Any] = {}          # 位号/封装uuid → {焊盘号: 焊盘 eid}
        for comp in net.get("components") or []:
            ref = str(comp.get("id") or "?")
            title = pcb_mod._mod_name(comp)
            part = lib_of.get(ref)
            uuid = _uid(f"{name}:{part['package'] if part else title}")
            fp_of[ref] = uuid
            if uuid not in fp_rows:
                if part and part["fp"]["pads"]:
                    rows_fp, pad_map = lib_mod.fp_rows(part, part.get("package") or title)
                    fp_rows[uuid] = (part.get("package") or title, rows_fp)
                    pads_of[uuid] = pad_map
                else:
                    rows_fp, pad_list = _footprint_rows(comp, title, uuid)
                    fp_rows[uuid] = (title, rows_fp)
                    # 本地兜底封装的焊盘是 **1..N**（按引脚下标铺的），键必须是焊盘号，
                    # 不能拿引脚名（U1 的 GND/3V3/EN…）—— 写成引脚名等于把网络挂到
                    # 一个不存在的焊盘上，PCB 上全无飞线。
                    pads_of[uuid] = {str(i + 1): e for i, e in enumerate(pad_list)}
            pads_of[ref] = pads_of[uuid]

        hole_fp: dict[int, str] = {}
        hole_rows: dict[str, list[list]] = {}
        hole_pad: dict[str, str] = {}          # 封装 uuid → 焊盘 eid
        for i, mh in enumerate(holes):
            d = float(mh.get("diameter_mm") or 3.2)
            uuid = _uid(f"{name}:hole{d:g}")
            hole_fp[i] = uuid
            if uuid not in hole_rows:
                hole_rows[uuid], hole_pad[uuid] = _hole_rows(mh, uuid)

        pcb_uuid = _uid(f"{name}:pcb")

        # 每个器件一份 device（库元件）：客户端的器件是「Device → Footprint」两级引用，
        # 只有 Footprint 会找不到库元件
        devs: dict[str, dict[str, Any]] = {}
        dev_of: dict[str, str] = {}
        for comp in net.get("components") or []:
            ref = str(comp.get("id") or "?")
            title = str(comp.get("value") or comp.get("kind") or ref)
            part = lib_of.get(ref)
            du = _uid(f"{name}:dev:{part['lcsc'] if part else title}")
            dev_of[ref] = du
            if du not in devs:
                attrs: dict[str, Any] = {
                    "Symbol": "",                     # 稍后由原理图段填上
                    "Footprint": fp_of.get(ref, ""),
                    "Add into BOM": "yes",
                    "Convert to PCB": "yes",
                    "Designator": str(comp.get("prefix") or "U?"),
                    "Name": "={Value}",
                    "Value": str(comp.get("value") or title),
                }
                if part:
                    # 这几个属性是客户端识别「官方库元件」的依据，
                    # 也决定属性面板里显示型号还是空白
                    attrs.update({
                        "Supplier": "LCSC",
                        "Supplier Part": part["lcsc"],
                        "LCSC Part": part["lcsc"],
                        "Manufacturer": part.get("manufacturer") or "",
                        "Manufacturer Part": part.get("mpn") or title,
                        "Supplier Footprint": part.get("package") or "",
                        "Description": part.get("value") or "",
                        "Datasheet": "",
                    })
                    title = part.get("mpn") or title
                devs[du] = {
                    "uuid": du, "title": title, "description": "",
                    "attributes": attrs,
                    "images": None, "tag": {}, "source": "",
                    "version": "1", "symbol_type": 2, "footprint_type": 4,
                }

        hole_dev: dict[int, str] = {}
        for i, mh in enumerate(holes):
            du = _uid(f"{name}:dev:hole{float(mh.get('diameter_mm') or 3.2):g}")
            hole_dev[i] = du
            devs.setdefault(du, {
                "uuid": du, "title": "MountingHole", "description": "",
                "attributes": {
                    "Symbol": "", "Footprint": hole_fp.get(i, ""),
                    "Add into BOM": "no", "Convert to PCB": "yes",
                    "Designator": "H?", "Name": "MountingHole", "Value": "MountingHole",
                },
                "images": None, "tag": {}, "source": "",
                "version": "1", "symbol_type": 2, "footprint_type": 4,
            })

        rows = _pcb_rows(net, b, pos, fp_of, holes, hole_fp, shift, pads_of, dev_of,
                         hole_dev, hole_pad, lib_of)

        # ---- 原理图段：与 PCB 共用同一批 device，只是把 Symbol 属性补上
        schematic = sch_mod.build(net, name, dev_of, lib_of)
        for ref, su in schematic["sym_of"].items():
            du = dev_of.get(ref)
            if du in devs:
                devs[du]["attributes"]["Symbol"] = su

        mini_fps = {}
        for uuid, (title, _r) in fp_rows.items():
            mini_fps[uuid] = {"uuid": uuid, "title": title, "display_title": title,
                              "docType": 4, "type": 4, "description": "", "desc": "",
                              "source": "", "version": "1"}
        for uuid, _r in hole_rows.items():
            mini_fps[uuid] = {"uuid": uuid, "title": "MountingHole", "display_title": "MountingHole",
                              "docType": 4, "type": 4, "description": "", "desc": "",
                              "source": "", "version": "1"}

        project = {
            "config": {"title": name, "cbbProject": False},
            "boards": {name: {"schematic": schematic["sch_uuid"], "pcb": pcb_uuid}},
            "devices": devs,
            "symbols": schematic["sym_index"],
            "footprints": mini_fps,
            "schematics": schematic["schematic_index"],
            "pcbs": {pcb_uuid: "PCB1"},
            "panels": {},
        }

        # 封装只走外置 .efoo + project.json 索引，**不要**再往 .epcb 里内嵌一份：
        # 内嵌段会被当成"未放置器件"，实测 26 个器件变成 96 个（多出 70 个空位号）。
        # 真机的内嵌段是客户端对局部改过的封装做的缓存，正常工程用外置即可。
        target = out / f"{name}.epro"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps(project, ensure_ascii=False))
            z.writestr(f"PCB/{pcb_uuid}.epcb", _lines(rows))
            for uuid, rows_fp in fp_rows.items():
                z.writestr(f"FOOTPRINT/{uuid}.efoo", _lines(rows_fp[1]))
            for uuid, rows_h in hole_rows.items():
                z.writestr(f"FOOTPRINT/{uuid}.efoo", _lines(rows_h))
            for uuid, r_sym in schematic["sym_files"].items():
                z.writestr(f"SYMBOL/{uuid}.esym", _lines(r_sym))
            z.writestr(schematic["sheet_file"], _lines(schematic["sheet_rows"]))

        files.append({"name": target.name, "size": target.stat().st_size})
        return {"ok": True, "files": files,
                "size": sum(f["size"] for f in files),
                "note": f"{len(net.get('components') or [])} 器件 / {len(holes)} 孔 / "
                        f"{len(fp_rows) + len(hole_rows)} 封装 / "
                        f"{schematic['stats']['components']} 原理图器件 · "
                        f"{schematic['stats']['net_labels']} 网络标签 · "
                        f"库 {schematic['stats']['from_lib']}"
                        f"/{schematic['stats']['components']}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "files": files}


def _lines(rows: list[list]) -> str:
    """逐行一个 JSON 数组。真机就是这么存的：可流式解析，不用整个文件读进内存。"""
    return "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
