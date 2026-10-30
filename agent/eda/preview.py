"""把 `.epro` 里的原理图页渲染成 SVG（纯 Python，不依赖浏览器）。

用途：让「原理图长什么样」在生成时就能看到，不必每次开客户端。
它只是**看图工具**，不参与工程生成 —— 真正的产物还是 `.epro` 本身。

注意几个 `.esch` / `.esym` 的字段陷阱（踩过）：
  * `RECT` 存的是两个角点 (x1,y1,x2,y2)，**不是** x/y/w/h；
  * `POLY` 的点是扁平数组 [x1,y1,x2,y2,...]，闭合标志在第 4 列；
  * `PIN` 的 (x,y) 是引脚**外接点**，线段朝 rot 方向延伸 len；
  * `WIRE` 是线段列表，每段扁平 4 元。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

# Pro 原理图单位是 1/100 英寸；A4 = 1170 × 825
A4_W, A4_H = 1170.0, 825.0
K = 4.0                     # 1 单位 -> 4 px，1:1 输出不缩放，线才不糊
_DIR = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}


def _rows(z: zipfile.ZipFile, name: str) -> list[list]:
    return [json.loads(l) for l in z.read(name).decode("utf-8").split("\n") if l.strip()]


def _sym_items(z: zipfile.ZipFile, uuid: str) -> list[tuple[str, tuple]]:
    name = f"SYMBOL/{uuid}.esym"
    if name not in z.namelist():
        return []
    out: list[tuple[str, tuple]] = []
    for r in _rows(z, name):
        t = r[0]
        if t == "PIN":
            out.append(("pin", (r[4], r[5], r[6], r[7])))
        elif t == "POLY":
            pts = r[2]
            out.append(("poly", ([(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)],
                                 bool(r[3]))))
        elif t == "RECT":
            x1, y1, x2, y2 = r[2], r[3], r[4], r[5]
            out.append(("rect", (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))))
        elif t == "ELLIPSE":
            out.append(("ell", (r[2], r[3], r[4], r[5])))
        elif t == "CIRCLE":
            out.append(("circ", (r[2], r[3], r[4])))
    return out


def schematic_svg(epro: str | Path, title: str = "") -> str:
    z = zipfile.ZipFile(str(epro))
    sheets = [n for n in z.namelist() if n.endswith(".esch")]
    if not sheets:
        raise ValueError(f"{epro} 里没有原理图页（SHEET/*.esch）")
    rows = _rows(z, sheets[0])

    ref_of: dict[str, str] = {}
    sym_of: dict[str, str] = {}
    net_of: dict[str, str] = {}
    for r in rows:
        if r[0] != "ATTR":
            continue
        if r[3] == "Designator":
            ref_of[r[2]] = r[4]
        elif r[3] == "Symbol":
            sym_of[r[2]] = r[4]
        elif r[3] == "NET":
            net_of[r[2]] = r[4]

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{A4_W*K:.0f}px" '
           f'height="{A4_H*K:.0f}px" viewBox="0 0 {A4_W*K:.0f} {A4_H*K:.0f}">',
           '<rect width="100%" height="100%" fill="#fdfdfb"/>',
           f'<rect x="{20*K/4:.0f}" y="{20*K/4:.0f}" width="{(A4_W-10)*K:.0f}" '
           f'height="{(A4_H-10)*K:.0f}" fill="none" stroke="#c3cad2" stroke-width="2"/>']
    if title:
        svg.append(f'<text x="{(A4_W/2)*K:.0f}" y="{28*K:.0f}" font-size="22" fill="#8a949e" '
                   f'text-anchor="middle" font-family="Arial">{title}</text>')

    comps: list[tuple[str, str, float, float]] = []
    for r in rows:
        if r[0] == "COMPONENT":
            comps.append((r[1], r[2], r[3], r[4]))

    for eid, label, cx, cy in comps:
        for kind, d in _sym_items(z, sym_of.get(eid, "")):
            if kind == "pin":
                x, y, ln, rot = d
                ux, uy = _DIR.get(rot, (1, 0))
                svg.append(f'<line x1="{(cx+x)*K:.1f}" y1="{(cy+y)*K:.1f}" '
                           f'x2="{(cx+x+ux*ln)*K:.1f}" y2="{(cy+y+uy*ln)*K:.1f}" '
                           f'stroke="#8a2b2b" stroke-width="2.2"/>')
            elif kind == "poly":
                pts, closed = d
                tag = "polygon" if closed else "polyline"
                p = " ".join(f"{(cx+px)*K:.1f},{(cy+py)*K:.1f}" for px, py in pts)
                svg.append(f'<{tag} points="{p}" fill="none" stroke="#1d3557" '
                           f'stroke-width="2.4" stroke-linejoin="round"/>')
            elif kind == "rect":
                x, y, w, h = d
                svg.append(f'<rect x="{(cx+x)*K:.1f}" y="{(cy+y)*K:.1f}" width="{w*K:.1f}" '
                           f'height="{h*K:.1f}" fill="none" stroke="#1d3557" stroke-width="2.4"/>')
            elif kind == "ell":
                x, y, rx, ry = d
                svg.append(f'<ellipse cx="{(cx+x)*K:.1f}" cy="{(cy+y)*K:.1f}" rx="{abs(rx)*K:.1f}" '
                           f'ry="{abs(ry)*K:.1f}" fill="none" stroke="#1d3557" stroke-width="2.4"/>')
            elif kind == "circ":
                x, y, rr = d
                svg.append(f'<circle cx="{(cx+x)*K:.1f}" cy="{(cy+y)*K:.1f}" r="{abs(rr)*K:.1f}" '
                           f'fill="none" stroke="#1d3557" stroke-width="2.4"/>')
        svg.append(f'<text x="{cx*K:.1f}" y="{(cy-16)*K:.1f}" font-size="24" fill="#111" '
                   f'text-anchor="middle" font-family="Arial">{ref_of.get(eid, "")}</text>')
        svg.append(f'<text x="{cx*K:.1f}" y="{(cy+20)*K:.1f}" font-size="18" fill="#6b737c" '
                   f'text-anchor="middle" font-family="Arial">{label[:24]}</text>')

    for r in rows:
        if r[0] != "WIRE":
            continue
        for seg in r[2]:
            svg.append(f'<line x1="{seg[0]*K:.1f}" y1="{seg[1]*K:.1f}" x2="{seg[2]*K:.1f}" '
                       f'y2="{seg[3]*K:.1f}" stroke="#2a6f97" stroke-width="2"/>')
        nm = net_of.get(r[1])
        if nm:
            seg = r[2][-1]
            svg.append(f'<text x="{seg[2]*K+3:.1f}" y="{seg[3]*K-3:.1f}" font-size="17" '
                       f'fill="#2a6f97" font-family="Arial">{nm}</text>')

    svg.append("</svg>")
    return "\n".join(svg)


def write_svg(epro: str | Path, out: str | Path, title: str = "") -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(schematic_svg(epro, title), encoding="utf-8")
    return out
