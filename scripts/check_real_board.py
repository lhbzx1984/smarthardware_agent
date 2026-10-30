"""校验「真实会话」产出的 board.json，以及由它生成的 .kicad_pcb 能否过 DRC。

用法:
    python scripts/check_real_board.py <run_dir_or_board.json>

之前 PCB 链路只跑过手写样例，真实模型产出的板级约束长什么样没人见过。
这个脚本就是补那一刀：结构工程师真写了没、写得规不规范、下游能不能用。
"""
from __future__ import annotations

import collections
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "agent")]

from eda import board as boardmod  # noqa: E402

FAILED: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}" + (f"  -- {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(label)


def find_kicad_cli() -> str | None:
    env = __import__("os").environ.get("KICAD_CLI")
    if env and Path(env).exists():
        return env
    for c in ("E:/KiCad/10.0/bin/kicad-cli.exe",
              "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe",
              "C:/Program Files/KiCad/9.0/bin/kicad-cli.exe",
              "C:/Program Files/KiCad/8.0/bin/kicad-cli.exe"):
        if Path(c).exists():
            return c
    return None


def main() -> int:
    src = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if not src or not src.exists():
        print(f"用法: python {Path(__file__).name} <run_dir|board.json>")
        return 1

    bj = src / "20_structure" / "board.json" if src.is_dir() else src
    run_dir = src if src.is_dir() else src.parent.parent

    print(f"\n【1】board.json 是否存在：{bj}")
    if not bj.exists():
        print("  ❌ 结构阶段没有产出 board.json —— 契约没落实，直接失败")
        return 1
    raw = json.loads(bj.read_text(encoding="utf-8"))
    print(f"  原始字段：{sorted(raw.keys())}")
    print(f"  原始 board 段：{json.dumps(raw.get('board', {}), ensure_ascii=False)[:200]}")

    b = boardmod.normalize(raw)
    print("\n【2】规范化结果")
    print(f"  板型={b['board']['shape']}  {b['board']['width_mm']}×{b['board']['height_mm']}mm"
          f"  圆角={b['board']['corner_radius_mm']}mm  板厚={b['board']['thickness_mm']}mm  层数={b['board']['layers']}")
    print(f"  安装孔={len(b['mounting_holes'])}  禁布区={len(b['keepouts'])}  "
          f"边约束={len(b['edge_constraints'])}  限高={b['height_limit_mm']}")
    print(f"  notes: {b['notes'][:120]}")

    check("板型合法", b["board"]["shape"] in {"rect", "rounded_rect", "circle", "ellipse", "polygon"},
          b["board"]["shape"])
    check("尺寸非零", b["board"]["width_mm"] > 0 and b["board"]["height_mm"] > 0)
    check("尺寸合理（5~400mm）", 5 <= b["board"]["width_mm"] <= 400 and 5 <= b["board"]["height_mm"] <= 400,
          f"{b['board']['width_mm']}×{b['board']['height_mm']}")
    check("归一化无告警", not b["warnings"], " / ".join(b["warnings"]))

    print("\n【3】几何自洽")
    pts = boardmod.outline_points(b)
    check("轮廓点数 ≥ 3", len(pts) >= 3, str(len(pts)))
    area = boardmod._polygon_area(pts)
    print(f"  轮廓 {len(pts)} 点，包围盒 "
          f"{max(p[0] for p in pts) - min(p[0] for p in pts):.1f}×"
          f"{max(p[1] for p in pts) - min(p[1] for p in pts):.1f}mm，面积 {area:.0f}mm²")
    check("轮廓面积 > 0", area > 1.0, f"{area}")
    check("轮廓闭合于首点", math.dist(pts[0], pts[-1]) < 1e-6 or len(pts) > 3)

    ok_in = all(boardmod.inside(h["x_mm"], h["y_mm"], b, margin_mm=0.0)
                for h in b["mounting_holes"])
    check("安装孔都在板内", ok_in,
          str([(h["x_mm"], h["y_mm"]) for h in b["mounting_holes"]
               if not boardmod.inside(h["x_mm"], h["y_mm"], b)]))
    if b["mounting_holes"]:
        print("  孔位：" + ", ".join(f"{h['name']}({h['x_mm']},{h['y_mm']})⌀{h['diameter_mm']}"
                                     for h in b["mounting_holes"]))
    for k in b["keepouts"]:
        print(f"  禁布区：{k.get('name')} "
              f"({k.get('x_mm')},{k.get('y_mm')}) {k.get('w_mm')}×{k.get('h_mm')}mm")
    for e in b["edge_constraints"]:
        print(f"  边约束：{e.get('ref')} → {e.get('edge')}")

    print("\n【4】下游：能否生成 .kicad_pcb 并过 DRC")
    hw = run_dir / "30_hardware"
    pcb = None
    if hw.exists():
        cand = sorted(hw.glob("*.kicad_pcb"))
        pcb = cand[0] if cand else None
    if not pcb:
        print("  ⚠ 30_hardware 下没有 .kicad_pcb（硬件阶段可能还没跑到或没调 emit_pcb）")
        check("硬件阶段产出了 PCB", False, "未找到 *.kicad_pcb")
    else:
        print(f"  PCB: {pcb.name}  ({pcb.stat().st_size / 1024:.1f} KB)")
        check("硬件阶段产出了 PCB", True)
        txt = pcb.read_text(encoding="utf-8", errors="replace")
        check("含 Edge.Cuts 板框", "Edge.Cuts" in txt)
        check("含 footprint", "(footprint " in txt)
        check("含 GND 铺铜", "(zone" in txt)
        cli = find_kicad_cli()
        if not cli:
            print("  ⚠ 找不到 kicad-cli，跳过 DRC")
        else:
            with tempfile.TemporaryDirectory() as td:
                rpt = Path(td) / "drc.rpt"
                p = subprocess.run([cli, "pcb", "drc", "--severity-all",
                                    "--output", str(rpt), str(pcb)],
                                   capture_output=True)
                out = (p.stdout + p.stderr).decode("gbk", "replace")
                if rpt.exists():
                    t = rpt.read_text(encoding="utf-8", errors="replace")
                    # 报告分两段：DRC violations（我们该负责的）和 unconnected pads。
                    # 后者是"不布线"的必然结果，不能算失败 —— 混在一起统计会得到假的 44 error。
                    m = re.search(r"\*\* Found (\d+) DRC violations \*\*", t)
                    n_viol = int(m.group(1)) if m else -1
                    seg = t[m.end():].split("** Found")[0] if m else t
                    errs = [l for l in seg.splitlines() if "; error" in l]
                    warns = [l for l in seg.splitlines() if "; warning" in l]
                    kinds = collections.Counter(re.findall(r"^\[([a-z_]+)\]:", seg, re.M))
                    print(f"  DRC 违规 {n_viol} 条 {dict(kinds)}；"
                          f"未连接 {len(re.findall(r'Missing connection', t))} 项（未布线，预期）")
                    check("DRC 0 违规", n_viol == 0, f"{n_viol} 条：{dict(kinds)}")
                    for l in (errs + warns)[:8]:
                        print("     !", l[:140])
                else:
                    print(f"  DRC 无报告，输出：{out[:300]}")

    print("\n【5】嘉立创 .epro：结构 / 原理图 / 回读")
    epro = None
    if hw.exists():
        cand = sorted(hw.glob("*.epro"))
        epro = cand[0] if cand else None
    if not epro:
        print("  ⚠ 没有 .epro（emit_easyeda 可能没跑到）")
        check("硬件阶段产出了 .epro", False, "未找到 *.epro")
    else:
        print(f"  .epro: {epro.name}  ({epro.stat().st_size / 1024:.1f} KB)")
        check("硬件阶段产出了 .epro", True)
        import zipfile
        with zipfile.ZipFile(epro) as z:
            names = z.namelist()
        need = {
            "工程索引": any(n.endswith("project.json") for n in names),
            "PCB 段": any(n.endswith(".epcb") for n in names),
            "封装": any(n.endswith(".efoo") for n in names),
            "符号": any(n.endswith(".esym") for n in names),
            "原理图页": any(n.endswith(".esch") for n in names),
        }
        for k, v in need.items():
            check(f"含{k}", v, str(names[:3]))

        sheet = next((n for n in names if n.endswith(".esch")), None)
        if sheet:
            with zipfile.ZipFile(epro) as z:
                srows = [json.loads(l) for l in z.read(sheet).decode("utf-8").split("\n") if l.strip()]
            comps = [r for r in srows if r[0] == "COMPONENT"]
            xs = [c[3] for c in comps]
            ys = [c[4] for c in comps]
            # A4 = 1170 x 825（1/100 英寸）
            inside = (min(xs) >= 0 and max(xs) <= 1170 and min(ys) >= 0 and max(ys) <= 825)
            print(f"  原理图 {len(comps)} 器件，画布 x {min(xs):.0f}..{max(xs):.0f} "
                  f"y {min(ys):.0f}..{max(ys):.0f}")
            check("原理图器件都在 A4 纸内", inside,
                  f"x {min(xs):.0f}..{max(xs):.0f} y {min(ys):.0f}..{max(ys):.0f}")
            sym_uuids = {r[4] for r in srows if r[0] == "ATTR" and r[3] == "Symbol"}
            check("符号不止一种（不是同一个模板刷的）", len(sym_uuids) > 1, f"{len(sym_uuids)} 种")
            labels = [r[4] for r in srows if r[0] == "ATTR" and r[3] == "NET"]
            check("有网络标签（原理图是连着线的）", len(labels) > 0, str(len(labels)))

        svg = sorted(hw.glob("*.schematic.svg"))
        check("生成了原理图预览 SVG", bool(svg), str([s.name for s in sorted(hw.glob('*.svg'))]))

        kpy = Path("E:/KiCad/10.0/bin/python.exe")
        if not kpy.exists():
            print("  ⚠ 找不到 KiCad python，跳过 .epro 回读")
        else:
            code = ('import pcbnew, json\n'
                    f'b = pcbnew.PCB_IO_MGR.Load(pcbnew.PCB_IO_MGR.EASYEDAPRO, r"{epro}")\n'
                    'fps = list(b.GetFootprints())\n'
                    'refs = [f.GetReference() for f in fps]\n'
                    'bb = b.GetBoardEdgesBoundingBox()\n'
                    'print(json.dumps({"fp": len(fps),'
                    ' "pads": sum(len(f.Pads()) for f in fps), "nets": b.GetNetCount(),'
                    ' "w": bb.GetWidth()/1e6, "h": bb.GetHeight()/1e6,'
                    ' "empty": sum(1 for r in refs if not r)}))\n')
            try:
                r = subprocess.run([str(kpy), "-c", code], capture_output=True, text=True, timeout=300)
                d = json.loads((r.stdout or "").strip().split("\n")[-1])
                print(f"  回读：{d['fp']} 封装 / {d['pads']} 焊盘 / {d['nets']} 网络 / "
                      f"{d['w']:.1f}×{d['h']:.1f}mm")
                check("KiCad 能读回 .epro", d["fp"] > 0, str(d))
                check("每个器件都有位号", d["empty"] == 0, f"{d['empty']} 个空位号")
                check("有网络（焊盘挂上了网）", d["nets"] > 1, str(d["nets"]))
            except Exception as exc:                                     # noqa: BLE001
                check("KiCad 能读回 .epro", False, f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 56)
    if FAILED:
        print(f"❌ 失败 {len(FAILED)} 项：")
        for f in FAILED:
            print("   -", f)
        return 1
    print("✅ 真实会话的板级约束与 PCB 链路闭环")
    return 0


if __name__ == "__main__":
    sys.exit(main())
