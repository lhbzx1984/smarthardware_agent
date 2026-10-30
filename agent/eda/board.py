"""板级约束（board.json）：PCB 外形的硬边界。

结构阶段产出，硬件阶段消费。这是**板框的唯一事实源**：
内腔多大、哪里不能布、孔在哪，都由它决定，不从 markdown 里扒数字。

坐标系：**板中心为原点，单位 mm，X 向右、Y 向下**（与结构/外观一致）。
写进 .kicad_pcb 时会整体平移到正区间。

支持的板型：
  rect / rounded_rect —— 矩形，corner_radius_mm > 0 自动按圆角处理
  circle / ellipse    —— 圆形、椭圆（圆形产品）
  polygon             —— 任意多边形（异形板），顶点按顺时针或逆时针给出
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# 圆角/圆弧离散化的段数（够平滑，又不至于让 Edge.Cuts 点数爆炸）
ARC_SEGMENTS = 8
CIRCLE_SEGMENTS = 64

_MIN_SIDE_MM = 5.0          # 小于这个尺寸基本不是板子，按脏数据处理
_MAX_CORNER_RATIO = 0.5     # 圆角半径不能超过短边的一半


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# ---------------------------------------------------------------- 缺省板
def default_board(width_mm: float = 0.0, height_mm: float = 0.0,
                  component_count: int = 0) -> dict[str, Any]:
    """没有 board.json 时的兜底板：按器件数估一块矩形板。

    宁可给一块明显偏大的板并标注「未接结构尺寸」，也不要猜一个装不进去的数。
    """
    w = _num(width_mm)
    h = _num(height_mm)
    if w <= 0 or h <= 0:
        # 每个器件约 25mm² 面积 + 边框，粗略估正方形板
        area = max(component_count, 1) * 25.0 + 400.0
        side = math.sqrt(area)
        w = h = round(side, 1)
    return normalize({
        "board": {"shape": "rect", "width_mm": w, "height_mm": h,
                  "corner_radius_mm": 1.5, "thickness_mm": 1.6},
        "notes": "未接结构尺寸，按器件数估算的兜底板框",
    })


# ---------------------------------------------------------------- 规范化
def normalize(raw: Any) -> dict[str, Any]:
    """把任意形状的输入洗成规范 board。绝不抛异常，问题记进 warnings。"""
    warnings: list[str] = []
    raw = raw if isinstance(raw, dict) else {}
    b = raw.get("board") if isinstance(raw.get("board"), dict) else raw

    shape = str(b.get("shape") or "rect").strip().lower()
    if shape in ("rectangle", "rectangular"):
        shape = "rect"
    if shape in ("round", "disc"):
        shape = "circle"
    if shape in ("poly", "custom", "irregular"):
        shape = "polygon"
    if shape not in ("rect", "rounded_rect", "circle", "ellipse", "polygon"):
        warnings.append(f"未知板型 '{shape}'，按矩形处理")
        shape = "rect"

    w = _num(b.get("width_mm") or b.get("width") or b.get("len_mm"))
    h = _num(b.get("height_mm") or b.get("height") or b.get("wid_mm"))

    if shape == "circle":
        d = _num(b.get("diameter_mm") or b.get("width_mm") or b.get("width"))
        if d <= 0:
            d = 50.0
            warnings.append("圆形板缺直径，兜底 50mm")
        w = h = d

    if shape in ("rect", "rounded_rect", "ellipse"):
        if w <= 0 or h <= 0:
            w, h = 50.0, 40.0
            warnings.append(f"{shape} 板缺边长，兜底 50×40mm")

    radius = _num(b.get("corner_radius_mm") or b.get("corner_radius") or b.get("radius_mm"))
    if shape == "rect" and radius > 0:
        shape = "rounded_rect"
    if shape == "rounded_rect":
        short = min(abs(w), abs(h))
        cap = short * _MAX_CORNER_RATIO
        if radius > cap:
            warnings.append(f"圆角 {radius}mm 超过短边一半，收敛到 {cap:.2f}mm")
            radius = cap
        radius = max(0.0, radius)

    w, h = abs(w), abs(h)
    if shape in ("rect", "rounded_rect", "ellipse") and (w < _MIN_SIDE_MM or h < _MIN_SIDE_MM):
        warnings.append(f"板尺寸 {w}×{h}mm 过小，兜底 50×40mm")
        w, h = 50.0, 40.0

    # 异形板顶点
    poly_raw = b.get("polygon_mm") or b.get("polygon") or b.get("outline_mm")
    poly: list[list[float]] = []
    if shape == "polygon":
        if isinstance(poly_raw, list) and len(poly_raw) >= 3:
            for pt in poly_raw:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    poly.append([_num(pt[0]), _num(pt[1])])
        if len(poly) < 3:
            warnings.append("polygon 板缺有效顶点（≥3 个），降级为矩形")
            shape = "rect"
            poly = []
            if w <= 0 or h <= 0:
                w, h = 50.0, 40.0

    thickness = _num(b.get("thickness_mm") or b.get("thickness"), 1.6)
    if thickness <= 0:
        thickness = 1.6
    thickness = _clamp(thickness, 0.2, 6.0)

    layers = int(_num(b.get("layers"), 2))
    if layers not in (1, 2, 4, 6):
        layers = 2

    out: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "board": {
            "name": str(b.get("name") or raw.get("name") or "main-board")[:60],
            "shape": shape,
            "width_mm": round(w, 3),
            "height_mm": round(h, 3),
            "corner_radius_mm": round(radius, 3),
            "thickness_mm": round(thickness, 3),
            "layers": layers,
        },
        "mounting_holes": [],
        "keepouts": [],
        "height_limit_mm": {},
        "edge_constraints": [],
        "notes": str(raw.get("notes") or b.get("notes") or "")[:300],
        "warnings": warnings,
    }
    if poly:
        out["board"]["polygon_mm"] = poly

    # ---- 安装孔
    for i, mh in enumerate(raw.get("mounting_holes") or []):
        if not isinstance(mh, dict):
            continue
        d = _num(mh.get("diameter_mm") or mh.get("diameter") or mh.get("d"), 3.2)
        if d <= 0:
            continue
        out["mounting_holes"].append({
            "name": str(mh.get("name") or f"H{i + 1}"),
            "x_mm": round(_num(mh.get("x_mm") if mh.get("x_mm") is not None else mh.get("x")), 3),
            "y_mm": round(_num(mh.get("y_mm") if mh.get("y_mm") is not None else mh.get("y")), 3),
            "diameter_mm": round(_clamp(d, 0.5, 20.0), 3),
            "plated": bool(mh.get("plated", False)),
            "slot_len_mm": round(max(0.0, _num(mh.get("slot_len_mm"))), 3),
        })

    # ---- 禁布区
    for i, ko in enumerate(raw.get("keepouts") or []):
        if not isinstance(ko, dict):
            continue
        kw = _num(ko.get("w_mm") or ko.get("width_mm") or ko.get("width"))
        kh = _num(ko.get("h_mm") or ko.get("height_mm") or ko.get("height"))
        if ko.get("shape") == "circle":
            kd = _num(ko.get("d_mm") or ko.get("diameter_mm"))
            kw = kh = kd
        if kw <= 0 or kh <= 0:
            continue
        out["keepouts"].append({
            "name": str(ko.get("name") or f"KO{i + 1}"),
            "shape": "circle" if ko.get("shape") == "circle" else "rect",
            "x_mm": round(_num(ko.get("x_mm") if ko.get("x_mm") is not None else ko.get("x")), 3),
            "y_mm": round(_num(ko.get("y_mm") if ko.get("y_mm") is not None else ko.get("y")), 3),
            "w_mm": round(kw, 3),
            "h_mm": round(kh, 3),
            "reason": str(ko.get("reason") or "")[:120],
        })

    # ---- 限高
    hl = raw.get("height_limit_mm")
    if isinstance(hl, dict):
        d = _num(hl.get("default_mm") or hl.get("default"))
        zones = []
        for z in hl.get("zones") or []:
            if not isinstance(z, dict):
                continue
            mx = _num(z.get("max_mm") or z.get("max_height_mm"))
            if mx <= 0:
                continue
            zones.append({
                "name": str(z.get("name") or ""),
                "x_mm": round(_num(z.get("x_mm") if z.get("x_mm") is not None else z.get("x")), 3),
                "y_mm": round(_num(z.get("y_mm") if z.get("y_mm") is not None else z.get("y")), 3),
                "w_mm": round(_num(z.get("w_mm") or z.get("width_mm")), 3),
                "h_mm": round(_num(z.get("h_mm") or z.get("height_mm")), 3),
                "max_mm": round(mx, 3),
            })
        out["height_limit_mm"] = {"default_mm": round(d, 3) if d > 0 else 0.0, "zones": zones}
    elif hl is not None:
        d = _num(hl)
        out["height_limit_mm"] = {"default_mm": round(d, 3) if d > 0 else 0.0, "zones": []}

    # ---- 接口靠边约束（连接器必须落在壳体开孔那侧）
    for ec in raw.get("edge_constraints") or []:
        if not isinstance(ec, dict):
            continue
        edge = str(ec.get("edge") or "").strip().lower()
        if edge in ("l", "west"):
            edge = "left"
        elif edge in ("r", "east"):
            edge = "right"
        elif edge in ("t", "north", "up"):
            edge = "top"
        elif edge in ("b", "south", "down"):
            edge = "bottom"
        if edge not in ("left", "right", "top", "bottom"):
            continue
        out["edge_constraints"].append({
            "ref": str(ec.get("ref") or ec.get("id") or "").strip(),
            "edge": edge,
            "offset_mm": round(_num(ec.get("offset_mm")), 3),
        })

    out["stats"] = _stats(out)
    return out


def _stats(b: dict[str, Any]) -> dict[str, Any]:
    pts = outline_points(b)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    area = _polygon_area(pts)
    return {
        "shape": b["board"]["shape"],
        "outline_points": len(pts),
        "bbox_mm": [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)] if pts else [0, 0],
        "area_mm2": round(area, 1),
        "mounting_holes": len(b.get("mounting_holes") or []),
        "keepouts": len(b.get("keepouts") or []),
    }


# ---------------------------------------------------------------- 轮廓点列
def outline_points(b: dict[str, Any]) -> list[tuple[float, float]]:
    """所有板型统一归一成闭合点列表（板中心为原点）。"""
    shape = b["board"]["shape"]
    w = b["board"]["width_mm"]
    h = b["board"]["height_mm"]

    if shape == "polygon":
        return [(p[0], p[1]) for p in (b["board"].get("polygon_mm") or [])]

    if shape == "circle":
        r = w / 2.0
        return [(_q(r * math.cos(2 * math.pi * i / CIRCLE_SEGMENTS)),
                 _q(r * math.sin(2 * math.pi * i / CIRCLE_SEGMENTS)))
                for i in range(CIRCLE_SEGMENTS)]

    if shape == "ellipse":
        rx, ry = w / 2.0, h / 2.0
        return [(_q(rx * math.cos(2 * math.pi * i / CIRCLE_SEGMENTS)),
                 _q(ry * math.sin(2 * math.pi * i / CIRCLE_SEGMENTS)))
                for i in range(CIRCLE_SEGMENTS)]

    # rect / rounded_rect
    hw, hh = w / 2.0, h / 2.0
    r = b["board"].get("corner_radius_mm") or 0.0
    if r <= 0:
        return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]

    r = min(r, min(hw, hh))
    pts: list[tuple[float, float]] = []
    # 四个圆角的圆心，按 右上 → 左上 → 左下 → 右下；相邻两点首尾相接形成闭合轮廓。
    # 注意图纸 Y 轴朝下：从「右边」转到「上边」是角度 -90°，不是 +90° —— 方向搞反
    # 会让圆角朝板内凹陷，板子短掉一个圆角直径。
    centers = [(hw - r, -hh + r), (-hw + r, -hh + r), (-hw + r, hh - r), (hw - r, hh - r)]
    for k, (cx, cy) in enumerate(centers):
        for i in range(ARC_SEGMENTS + 1):
            ang = math.radians(-90.0 * k - 90.0 * i / ARC_SEGMENTS)
            pts.append((_q(cx + r * math.cos(ang)), _q(cy + r * math.sin(ang))))
    return pts


def _q(v: float) -> float:
    return round(v, 4)


def _polygon_area(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# ---------------------------------------------------------------- 几何判定
def inside(x: float, y: float, b: dict[str, Any], margin_mm: float = 0.0) -> bool:
    """点是否在板内（带内缩安全距）。多边形用射线法，矩形/圆用解析式。"""
    shape = b["board"]["shape"]
    w, h = b["board"]["width_mm"], b["board"]["height_mm"]

    # 轮廓点本身就落在边界上，且坐标量化到 4 位小数，比较必须留 ~1μm 容差，
    # 否则圆弧上的点会被判到板外（差 0.0003 mm² 就会失败）
    eps = 1e-3
    if shape in ("rect", "rounded_rect"):
        hw, hh = w / 2.0 - margin_mm, h / 2.0 - margin_mm
        if hw <= 0 or hh <= 0:
            return False
        if abs(x) > hw + eps or abs(y) > hh + eps:
            return False
        r = b["board"].get("corner_radius_mm") or 0.0
        if r > 0:
            # 圆角外侧的四角要挖掉
            cx = _clamp(x, -hw + r, hw - r)
            cy = _clamp(y, -hh + r, hh - r)
            if (x - cx) ** 2 + (y - cy) ** 2 > (r + margin_mm + eps) ** 2:
                return False
        return True

    if shape == "circle":
        return x * x + y * y <= ((w / 2.0 - margin_mm) ** 2 if w / 2.0 > margin_mm else 0.0)

    if shape == "ellipse":
        rx, ry = w / 2.0 - margin_mm, h / 2.0 - margin_mm
        if rx <= 0 or ry <= 0:
            return False
        return (x * x) / (rx * rx) + (y * y) / (ry * ry) <= 1.0

    # polygon：射线法（margin 用「到各边距离」近似，够布局避让用）
    pts = b["board"].get("polygon_mm") or []
    if len(pts) < 3:
        return False
    if margin_mm <= 0:
        return _point_in_polygon(x, y, pts)
    return _point_in_polygon(x, y, pts) and _dist_to_edges(x, y, pts) >= margin_mm


def _point_in_polygon(x: float, y: float, pts: list[list[float]]) -> bool:
    hit = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if x < xi:
                hit = not hit
    return hit


def _dist_to_edges(x: float, y: float, pts: list[list[float]]) -> float:
    best = float("inf")
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        dx, dy = x2 - x1, y2 - y1
        d2 = dx * dx + dy * dy
        t = 0.0 if d2 == 0 else _clamp(((x - x1) * dx + (y - y1) * dy) / d2, 0.0, 1.0)
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


def distance_to_edge(x: float, y: float, b: dict[str, Any]) -> float:
    """点到板边的最小距离（板内板外都返回正值）。

    布局之后判断"这个器件的位号还画得下吗"要用它：位号在丝印层且在本体外 1.9mm，
    器件离板边不够远就会被裁掉（silk_edge_clearance），这时得把位号退到 F.Fab。
    """
    return _dist_to_edges(x, y, outline_points(b))


def blocked(x: float, y: float, w: float, h: float, b: dict[str, Any]) -> str | None:
    """器件占位是否与安装孔/禁布区冲突。返回冲突原因，None 表示可以放。"""
    half_w, half_h = w / 2.0, h / 2.0
    for mh in b.get("mounting_holes") or []:
        need = mh["diameter_mm"] / 2.0 + 1.5          # 孔周围留 1.5mm 禁布
        if _aabb_overlap(x, y, half_w, half_h, mh["x_mm"], mh["y_mm"], need, need):
            return f"压安装孔 {mh['name']}"
    for ko in b.get("keepouts") or []:
        if ko["shape"] == "circle":
            rr = ko["w_mm"] / 2.0
            cx = _clamp(x, ko["x_mm"] - rr, ko["x_mm"] + rr)
            cy = _clamp(y, ko["y_mm"] - rr, ko["y_mm"] + rr)
            if math.hypot(x - cx, y - cy) <= max(half_w, half_h):
                return f"压禁布区 {ko['name']}"
        elif _aabb_overlap(x, y, half_w, half_h,
                           ko["x_mm"], ko["y_mm"], ko["w_mm"] / 2.0, ko["h_mm"] / 2.0):
            return f"压禁布区 {ko['name']}"
    return None


def _aabb_overlap(ax: float, ay: float, ahw: float, ahh: float,
                  bx: float, by: float, bhw: float, bhh: float) -> bool:
    return abs(ax - bx) < (ahw + bhw) and abs(ay - by) < (ahh + bhh)


# ---------------------------------------------------------------- 读文件
def load(path: Path | str) -> dict[str, Any]:
    """读 board.json。文件缺失/损坏返回兜底板，绝不抛异常。"""
    p = Path(path)
    if not p.exists():
        b = default_board()
        b["warnings"].append(f"未找到 {p.name}，用了兜底矩形板")
        return b
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        b = default_board()
        b["warnings"].append(f"{p.name} 解析失败（{type(e).__name__}），用了兜底矩形板")
        return b
    b = normalize(raw)
    b["source"] = str(p)
    return b
