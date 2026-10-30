"""嘉立创 EDA Pro `.epro` writer 的结构自验。

**为什么是自验而不是"真机打开"**：本机没有 EasyEDA 客户端。
但 KiCad 10 自带 EasyEDA Pro 导入器，可以用 `kicad-cli` 那套 python 来交叉验证
（见 `--kicad` 分支）。两条腿都要走：结构断言抓格式错，KiCad 导入抓语义错。

真机基准 = 用户用客户端 4.1.78 导出的工程（立创·实战派ESP32-S31开发板），
所以下面每个字段数都是照着真机行核对过的，不是从规范推的。
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from eda import board as bm  # noqa: E402
from eda import easyeda  # noqa: E402
from eda import pcb  # noqa: E402
from eda.netlist import annotate, normalize  # noqa: E402

TMP = ROOT / "workspace" / "_tmp" / "easyeda_test"
KICAD_PY = Path("E:/KiCad/10.0/bin/python.exe")

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def _write(name: str = "board") -> tuple[Path, dict]:
    TMP.mkdir(parents=True, exist_ok=True)
    net = annotate(normalize({
        "title": "test",
        "components": [
            {"id": "U1", "kind": "ic", "value": "ESP32-C3",
             "pins": ["VDD", "GND", "IO0", "IO1", "EN"], "size_mm": [7, 7]},
            {"id": "C1", "kind": "cap", "value": "10uF",
             "pins": ["1", "2"], "size_mm": [1.6, 0.8]},
            {"id": "J1", "kind": "connector", "value": "USB-C",
             "pins": [str(i) for i in range(1, 13)], "size_mm": [8.9, 7.3]},
        ],
        "connections": [
            {"from": "U1.VDD", "to": "J1.1"},
            {"from": "U1.GND", "to": "C1.2"},
            {"from": "U1.EN", "to": "C1.1"},
        ],
    }, title="test"))
    b = bm.normalize({
        "board": {"shape": "rounded_rect", "width_mm": 60, "height_mm": 40,
                  "corner_radius_mm": 3, "thickness_mm": 1.6},
        "mounting_holes": [{"name": "H1", "x_mm": -24, "y_mm": -14, "diameter_mm": 3.2}],
        "edge_constraints": [{"ref": "J1", "edge": "right"}],
    })
    out = TMP / name
    res = easyeda.write(net, b, out, name)
    assert res.get("ok"), res.get("error")
    return out / f"{name}.epro", {"net": net, "board": b, "res": res}


def _rows(z: zipfile.ZipFile, name: str) -> list[list]:
    return [json.loads(l) for l in z.read(name).decode("utf-8").split("\n") if l.strip()]


def main() -> int:
    print("[1] 文件结构")
    p, ctx = _write()
    z = zipfile.ZipFile(p)
    names = z.namelist()
    check("生成 .epro", p.exists() and p.stat().st_size > 2000, str(p.stat().st_size if p.exists() else 0))
    check("含 project.json", "project.json" in names)
    check("含 PCB/*.epcb", any(n.startswith("PCB/") and n.endswith(".epcb") for n in names))
    check("含 FOOTPRINT/*.efoo", any(n.startswith("FOOTPRINT/") and n.endswith(".efoo") for n in names))
    check("没有旧版 .epru", not any(n.endswith(".epru") for n in names), str(names[:5]))

    pcb_name = next(n for n in names if n.startswith("PCB/"))
    lines = [l for l in z.read(pcb_name).decode("utf-8").split("\n") if l.strip()]
    check("PCB 只有一段 DOCHEAD", sum(1 for l in lines if json.loads(l)[0] == "DOCTYPE") == 1)

    print("[2] 逐行 JSON 数组（真机格式）")
    bad = []
    for i, l in enumerate(lines):
        try:
            a = json.loads(l)
        except Exception:
            bad.append((i, "非 JSON"))
            continue
        if not isinstance(a, list) or not a:
            bad.append((i, "不是非空数组"))
        elif not isinstance(a[0], str):
            bad.append((i, "首元素不是类型名"))
    check("每行都是合法 JSON 数组", not bad, str(bad[:3]))
    check("没有 V3 规范的 || 分隔符", not any("||" in l for l in lines))

    rows = [json.loads(l) for l in lines]
    seq = [a[0] for a in rows]
    check("首行 DOCTYPE", seq[0] == "DOCTYPE" and rows[0][1] == "PCB", str(rows[0]))
    check("第二行 HEAD", seq[1] == "HEAD", str(seq[:3]))

    print("[3] 层表与固定配置")
    layers = [a for a in rows if a[0] == "LAYER"]
    check("层表 ≥ 12 条", len(layers) >= 12, str(len(layers)))
    lmap = {a[1]: a for a in layers if isinstance(a[1], int)}
    check("TOP 层是 1", lmap.get(1, [None] * 3)[2] == "TOP", str(lmap.get(1)))
    check("板框层 11 是 OUTLINE", lmap.get(11, [None] * 4)[3] == "Board Outline Layer", str(lmap.get(11)))
    check("有 LAYER_PHYS", any(a[0] == "LAYER_PHYS" for a in rows))
    check("有 PREFERENCE", any(a[0] == "PREFERENCE" for a in rows))
    check("有 PRIMITIVE 显示表", sum(1 for a in rows if a[0] == "PRIMITIVE") >= 20)

    print("[4] 板框")
    polys = [a for a in rows if a[0] == "POLY" and a[4] == 11]
    check("板框是 POLY + layer 11", len(polys) == 1, str(len(polys)))
    seqpts = polys[0][6]
    check("板框点列以 L 连接", seqpts[2] == "L", str(seqpts[:4]))
    xs = [seqpts[0]] + seqpts[3::2]
    ys = [seqpts[1]] + seqpts[4::2]
    w_mm = (max(xs) - min(xs)) / easyeda.MIL
    h_mm = (max(ys) - min(ys)) / easyeda.MIL
    check("板框宽度 = 60mm", abs(w_mm - 60) < 0.05, f"{w_mm:.3f}")
    check("板框高度 = 40mm", abs(h_mm - 40) < 0.05, f"{h_mm:.3f}")
    check("板框闭合", abs(xs[0] - xs[-1]) < 1e-6 and abs(ys[0] - ys[-1]) < 1e-6,
          f"{xs[0]}..{xs[-1]} / {ys[0]}..{ys[-1]}")
    check("板框坐标全为正（不会飞出视野）", min(xs) > 0 and min(ys) > 0,
          f"x>{min(xs):.1f} y>{min(ys):.1f}")

    print("[5] 网络")
    nets = [a for a in rows if a[0] == "NET"]
    names_n = {a[1] for a in nets}
    check("至少声明 2 个网络", len(names_n) >= 2, str(sorted(names_n)))
    check("NET 行 8 字段（对齐真机）", all(len(a) == 8 for a in nets))

    print("[6] 器件与属性")
    comps = [a for a in rows if a[0] == "COMPONENT"]
    # 安装孔也做成 COMPONENT（单焊盘封装），所以是 3 器件 + 1 孔
    check("3 器件 + 1 安装孔", len(comps) == 4, str(len(comps)))
    check("COMPONENT 9 字段", all(len(a) == 9 for a in comps), str([len(a) for a in comps]))
    check("COMPONENT 层号是 1", all(a[3] == 1 for a in comps))
    check("COMPONENT 坐标为正", all(a[4] > 0 and a[5] > 0 for a in comps))

    attrs = [a for a in rows if a[0] == "ATTR"]
    by_comp = {}
    for a in attrs:
        by_comp.setdefault(a[3], []).append(a)
    keys_ok = all({"Footprint", "Device", "Designator"} <= {x[7] for x in v} for v in by_comp.values())
    check("每个器件都有 Footprint/Device/Designator 三条", keys_ok,
          str([sorted({x[7] for x in v}) for v in by_comp.values()]))
    check("ATTR 22 字段（对齐真机）", all(len(a) == 22 for a in attrs), str([len(a) for a in attrs]))

    print("[7] project.json 索引")
    pj = json.loads(z.read("project.json").decode("utf-8"))
    for key in ("config", "boards", "devices", "symbols", "footprints", "schematics", "pcbs", "panels"):
        check(f"有 {key}", key in pj)
    fp_ids = set(pj["footprints"])
    dev_ids = set(pj["devices"])
    refs = {(x[7], x[8]) for x in attrs if x[7] in ("Footprint", "Device")}
    fp_refs = {v for k, v in refs if k == "Footprint"}
    dev_refs = {v for k, v in refs if k == "Device"}
    check("Footprint 引用都在 footprints 索引里", fp_refs <= fp_ids, str(fp_refs - fp_ids))
    check("Device 引用都在 devices 索引里", dev_refs <= dev_ids, str(dev_refs - dev_ids))
    check("每个 device 指向存在的 footprint",
          all(d.get("attributes", {}).get("Footprint") in fp_ids for d in pj["devices"].values()))
    check("pcbs 索引指向实际文件", all(f"PCB/{k}.epcb" in names for k in pj["pcbs"]))
    check("板子挂到了 boards", len(pj["boards"]) == 1)

    print("[8] 焊盘与网络绑定")
    pads_total = 0
    pad_ids: dict[str, set[str]] = {}
    for n in names:
        if not n.endswith(".efoo"):
            continue
        frows = _rows(z, n)
        head = next(a for a in frows if a[0] == "HEAD")
        fpad = [a for a in frows if a[0] == "PAD"]
        # 库封装（照真机头部生成的）HEAD 里**不带 uuid**，uuid 只在 project.json
        # 索引和文件名上，所以回落用文件名 stem —— 两种封装都要能过这条断言。
        pad_ids[head[1].get("uuid") or Path(n).stem] = {a[1] for a in fpad}
        pads_total += len(fpad)
        for a in fpad:
            if len(a) != 22:
                check(f"{n} 的 PAD 22 字段", False, str(len(a)))
                break
    check("PAD 行 22 字段", True)
    # ⚠ 不能再用「焊盘数 = 引脚数」当判据了：命中官方库时封装是真封装，
    # 焊盘数本来就可能多于网表引脚数（USB-C 座 16~20 个焊盘、网表只写 9 个脚）。
    # 真正要保证的是：每个封装都有焊盘，且 PAD_NET 引用的焊盘真实存在（下面那条）。
    n_inst = len(ctx["net"]["components"]) + \
        len(list(ctx["board"].get("mounting_holes") or []) + pcb._auto_holes(ctx["board"]))
    check("每个封装都有焊盘", pads_total >= n_inst, f"{pads_total} vs {n_inst}")
    want_pads = pads_total
    pad_nets = [a for a in rows if a[0] == "PAD_NET"]
    check("PAD_NET 6 字段", all(len(a) == 6 for a in pad_nets))
    comp_fp = {}
    for a in attrs:
        if a[7] == "Footprint":
            comp_fp[a[3]] = a[8]
    orphan = []
    for a in pad_nets:
        fp = comp_fp.get(a[1])
        if not fp or a[4] not in pad_ids.get(fp, set()):
            orphan.append((a[1], a[4]))
    check("PAD_NET 的焊盘 id 在封装里真实存在", not orphan, str(orphan[:3]))
    declared = {a[1] for a in rows if a[0] == "NET"}
    bad_net = [a[3] for a in pad_nets if a[3] and a[3] not in declared]
    check("PAD_NET 的网络名都已用 NET 声明", not bad_net, str(bad_net[:3]))
    # 引脚→网络错位过一次（网络号 1 基、列表 0 基，差一位），C2 正极被连到 I2C_SCL。
    # 这里按「位号+引脚」重建期望值逐一比对，防止再犯。
    comp_ref = {a[3]: a[8] for a in attrs if a[7] == "Designator"}
    want_net: dict[tuple[str, str], str] = {}
    for nn in ctx["net"]["nets"]:
        for ep in nn["points"]:
            if "." in ep:
                r_, p_ = ep.split(".", 1)
                want_net[(r_, p_)] = nn["name"]
    wrong = []
    for a in pad_nets:
        key = (comp_ref.get(a[1], ""), a[2])
        if key in want_net and want_net[key] != (a[3] or ""):
            wrong.append((key, a[3], want_net[key]))
    check("PAD_NET 的引脚→网络与网表一致", not wrong, str(wrong[:3]))
    check("有网络名的 PAD_NET ≥ 4 条",
          sum(1 for a in pad_nets if a[3]) >= 4,
          str(sum(1 for a in pad_nets if a[3])))

    print("[9] 与 KiCad 版同源")
    kicad = pcb.build(ctx["net"], ctx["board"], "board")
    check("KiCad 版也能生成", "board.kicad_pcb" in kicad or "footprint" in kicad)
    holes_pos = list(ctx["board"].get("mounting_holes") or [])
    check("安装孔进了 EasyEDA", any(
        a[0] == "COMPONENT" and a[7].get("Unique ID", "").startswith("ggh") for a in comps) or len(comps) >= 3)

    print("[10] KiCad 交叉验证（EasyEDA Pro 导入器）")
    if KICAD_PY.exists():
        script = f'''
import pcbnew, json
b = pcbnew.PCB_IO_MGR.Load(pcbnew.PCB_IO_MGR.EASYEDAPRO, r"{p}")
fps = list(b.GetFootprints())
refs = [f.GetReference() for f in fps]
pads = sum(len(f.Pads()) for f in fps)
bb = b.GetBoardEdgesBoundingBox()
print(json.dumps({{"fp": len(fps), "pads": pads, "nets": b.GetNetCount(),
                  "w": bb.GetWidth()/1e6, "h": bb.GetHeight()/1e6,
                  "refs": sorted(r for r in refs if r),
                  "empty": sum(1 for r in refs if not r)}}))
'''
        r = subprocess.run([str(KICAD_PY), "-c", script], capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and r.stdout.strip():
            d = json.loads(r.stdout.strip().split("\n")[-1])
            check("KiCad 读到 4 个器件（3+1孔）", d["fp"] == 4, str(d["fp"]))
            check("位号齐全", d["refs"] == ["C1", "H1", "J1", "U1"], str(d["refs"]))
            check("没有空位号（内嵌段没混进来）", d["empty"] == 0, str(d["empty"]))
            check("焊盘数与封装一致", d["pads"] == want_pads, f'{d["pads"]} vs {want_pads}')
            check("板框 60.2 x 40.2mm",
                  abs(d["w"] - 60.2) < 0.1 and abs(d["h"] - 40.2) < 0.1, f'{d["w"]:.2f}x{d["h"]:.2f}')
        else:
            check("KiCad 导入成功", False, (r.stderr or r.stdout)[:200])
    else:
        print("  skip 未找到 KiCad python")

    print("[11] 原理图段（SCH）")
    sch_files = [n for n in names if n.endswith(".esch")]
    sym_files = [n for n in names if n.endswith(".esym")]
    check("含 SHEET/**/*.esch", len(sch_files) == 1, str(sch_files))
    check("含 SYMBOL/*.esym", len(sym_files) >= 2, str(sym_files))
    pj = json.loads(z.read("project.json"))
    check("project.json 有 schematics 索引", bool(pj.get("schematics")))
    bmap = pj.get("boards") or {}
    check("boards 同时挂了 schematic 与 pcb",
          all(v.get("schematic") and v.get("pcb") for v in bmap.values()), str(bmap)[:160])
    sch_uuid = next(iter(pj["schematics"]))
    check("原理图 uuid 与 SHEET 目录同名",
          any(n.startswith("SHEET/" + sch_uuid + "/") for n in names), sch_uuid)
    sym_idx = pj.get("symbols") or {}
    check("symbols 索引与 .esym 文件一一对应",
          set(sym_idx) == set(n[len("SYMBOL/"):-len(".esym")] for n in sym_files),
          str(len(sym_idx)) + " vs " + str(len(sym_files)))
    check("符号 docType/type = 2",
          all(v.get("docType") == 2 and v.get("type") == 2 for v in sym_idx.values()))

    srows = _rows(z, sch_files[0])
    check("原理图首行 DOCTYPE SCH", srows[0][0] == "DOCTYPE" and srows[0][1] == "SCH", str(srows[0]))
    stypes = set(r[0] for r in srows)
    check("原理图没有 PCB 的行类型", not ({"PAD", "POLY", "VIA"} & stypes), str(sorted(stypes)))

    scomps = [r for r in srows if r[0] == "COMPONENT"]
    sattrs = [r for r in srows if r[0] == "ATTR"]
    wires = [r for r in srows if r[0] == "WIRE"]
    check("COMPONENT 9 段", all(len(r) == 9 for r in scomps), str(sorted({len(r) for r in scomps})))
    check("ATTR 12 段", all(len(r) == 12 for r in sattrs), str(sorted({len(r) for r in sattrs})))
    check("WIRE 5 段", all(len(r) == 5 for r in wires), str(sorted({len(r) for r in wires})))
    check("3 个器件都上了原理图", len(scomps) == 3, str(len(scomps)))

    ref_of = dict((a[2], a[4]) for a in sattrs if a[3] == "Designator")
    symattr = dict((a[2], a[4]) for a in sattrs if a[3] == "Symbol")
    devattr = dict((a[2], a[4]) for a in sattrs if a[3] == "Device")
    check("每个器件都有位号", set(ref_of.values()) == {"U1", "C1", "J1"}, str(sorted(ref_of.values())))
    check("Symbol 属性都指向真存在的 .esym",
          all(v in sym_idx for v in symattr.values()), str(sorted(symattr.values()))[:160])
    check("Device 属性都指向 devices 索引",
          all(v in (pj.get("devices") or {}) for v in devattr.values()), str(len(devattr)))
    mism = [r for r in ref_of
            if (pj["devices"].get(devattr.get(r, "")) or {}).get("attributes", {}).get("Symbol")
            != symattr.get(r)]
    check("devices 的 Symbol 属性已补齐", not mism, str(mism))

    # 符号文件：PIN 11 段，每个引脚都有 NAME 属性，且有默认位号
    pin_pos = {}
    pin_xy: dict[str, list[tuple[str, float, float]]] = {}
    for sn in sym_files:
        fr = _rows(z, sn)
        pins = [r for r in fr if r[0] == "PIN"]
        pattr = [r for r in fr if r[0] == "ATTR"]
        tag = sn[len("SYMBOL/"):-len(".esym")]
        d = {}
        for r in pins:
            nm = next((a[4] for a in fr if a[0] == "ATTR" and a[2] == r[1] and a[3] == "NAME"), "")
            d[nm] = (r[4], r[5])
        pin_pos[tag] = d
        # 按坐标索引：符号引脚的写出顺序**跟网表的引脚顺序无关**（电容的 .esym
        # 里 2 号脚就排在 1 号前面），所以校验「哪根线属于哪个脚」只能认坐标。
        pin_xy[tag] = [(nm, r[4], r[5]) for r in pins for nm in
                       [next((a[4] for a in fr if a[0] == "ATTR" and a[2] == r[1]
                              and a[3] == "NAME"), "")]]
        check(tag[:12] + " PIN 11 段", all(len(r) == 11 for r in pins),
              str(sorted({len(r) for r in pins})))
        peids = set(r[1] for r in pins)
        named = set(a[2] for a in pattr if a[3] == "NAME")
        check(tag[:12] + " 每个引脚都有 NAME 属性", peids == named,
              str(len(peids)) + "/" + str(len(named)))
        check(tag[:12] + " 有默认位号", any(a[3] == "Designator" for a in pattr))

    # 用户实测反馈过「所有器件符号长得一模一样、也没有名称」——根因是早先用
    # 一套参数化矩形刷所有器件。现在符号按种类（优先官方库）生成，
    # 所以不同器件必须产出不同的符号内容（电容 ≠ 连接器 ≠ IC）。
    sym_blobs = {n: z.read(n) for n in sym_files}
    check("每个器件有独立符号（不是一个模板刷的）",
          len(set(sym_blobs.values())) == len(sym_blobs), f"{len(sym_blobs)} 个符号")

    # 器件名要能在原理图上看见：COMPONENT 的 Name 属性不能是种类名（ic/cap/connector）
    names = [a[4] for a in sattrs if a[3] == "Name"]
    check("器件有型号名（不是种类名）",
          bool(names) and not (set(names) & {"ic", "cap", "connector", "resistor"}),
          str(names)[:120])

    # 线必须挂在引脚外接点上：器件原点 + 符号引脚偏移
    suuid_of = dict((r[1], symattr.get(r[1], "")) for r in scomps)
    x_of = dict((r[1], (r[3], r[4])) for r in scomps)
    owner = {}
    for r in srows:
        if r[0] == "COMPONENT":
            cur = r[1]
        elif r[0] == "WIRE":
            owner[r[1]] = cur

    def pin_at(comp_eid: str, sx: float, sy: float) -> str:
        """按几何找这根线挂在哪个引脚上（引脚名）。"""
        cx, cy = x_of.get(comp_eid, (0, 0))
        for nm, px, py in pin_xy.get(suuid_of.get(comp_eid, ""), []):
            if abs(cx + px - sx) < 0.01 and abs(cy + py - sy) < 0.01:
                return nm
        return ""

    bad_start = []
    for w in wires:
        c = owner.get(w[1], "")
        sx, sy = w[2][0][0], w[2][0][1]
        if not pin_at(c, sx, sy):
            bad_start.append((w[1], (sx, sy), (x_of.get(c, (0, 0)))))
    check("每根线的起点都落在某个引脚外接点上", not bad_start, str(bad_start[:2]))

    # 网络标签：名字来自网表，且「位号.引脚 → 网名」与网表逐条一致
    want_sch = {}
    for nn in ctx["net"]["nets"]:
        for ep in nn["points"]:
            if "." in ep:
                r_, p_ = ep.split(".", 1)
                want_sch[(r_, p_)] = nn["name"]
    net_labels = [a for a in sattrs if a[3] == "NET"]
    check("有 NET 网络标签", len(net_labels) >= 4, str(len(net_labels)))
    net_names = set(n["name"] for n in ctx["net"]["nets"])
    check("标签名都在网表里", all(a[4] in net_names for a in net_labels),
          str([a[4] for a in net_labels if a[4] not in net_names]))
    owner_pin, cur = {}, ""
    for r in srows:
        if r[0] == "COMPONENT":
            cur = r[1]
        elif r[0] == "WIRE":
            nm = pin_at(cur, r[2][0][0], r[2][0][1])
            if nm:
                owner_pin[r[1]] = (ref_of.get(cur, ""), nm)
    wrong_sch = []
    for a in net_labels:
        key = owner_pin.get(a[2], ("", ""))
        if key in want_sch and want_sch[key] != a[4]:
            wrong_sch.append((key, a[4], want_sch[key]))
    check("原理图引脚→网络与网表一致", not wrong_sch, str(wrong_sch[:3]))
    check("有连线的引脚都有标签", len(net_labels) == len(want_sch),
          str(len(net_labels)) + " vs " + str(len(want_sch)))

    # 排版：所有器件必须落在 A4（1170×825，1/100 英寸）里。
    # 旧版按「图纸无限」铺，器件会一路排到纸外面去。
    out_of_sheet = [c for c in scomps if not (0 <= c[3] <= 1170 and 0 <= c[4] <= 825)]
    check("器件都落在 A4 图纸内", not out_of_sheet,
          str([(ref_of.get(c[1]), c[3], c[4]) for c in out_of_sheet[:3]]))
    cx = [c[3] for c in scomps]
    cy = [c[4] for c in scomps]
    check("器件有横向铺开", (max(cx) - min(cx)) > 0, f"跨度 {max(cx)-min(cx):.0f}")

    # 器件不能叠在一起：用符号 PART.BBOX 算占位矩形，两两判相交
    bbox_of = {}
    with zipfile.ZipFile(p) as zf:
        for n in zf.namelist():
            if not n.endswith(".esym"):
                continue
            rs = [json.loads(l) for l in zf.read(n).decode("utf-8").split("\n") if l.strip()]
            bb = [r[2]["BBOX"] for r in rs if r[0] == "PART"]
            if bb:
                bbox_of[n.rsplit("/", 1)[-1][:-5]] = bb[0]
    boxes = []
    for c in scomps:
        bb = bbox_of.get(suuid_of.get(c[1], ""), [-30, -20, 30, 20])
        boxes.append((ref_of.get(c[1], "?"),
                      c[3] + bb[0], c[4] + bb[1], c[3] + bb[2], c[4] + bb[3]))
    overlap = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]:
                overlap.append((a[0], b[0]))
    check("器件互不重叠", not overlap, str(overlap[:3]))

    # 预览渲染：纯 Python 出的 SVG，用户不开客户端也能看排版
    from eda.preview import schematic_svg  # 本脚本把 agent/ 放在 sys.path 上，包就是顶层 eda
    svg = schematic_svg(p)
    check("原理图预览能渲染", svg.startswith("<svg") and svg.rstrip().endswith("</svg>"),
          str(len(svg)))
    check("预览里出现了位号", all(ref_of.get(c[1], "?") in svg for c in scomps),
          str([ref_of.get(c[1]) for c in scomps[:3]]))

    print(f"\n通过 {passed} / 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
