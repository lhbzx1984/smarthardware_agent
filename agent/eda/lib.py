"""立创官方元件库（EasyEDA 标准版接口）→ EasyEDA Pro 图元。

为什么需要这个模块
------------------
早先的符号/封装是**参数化猜出来的**（所有器件一个矩形、封装按 ``size_mm`` 铺焊盘），
于是原理图上二十几个器件长得一模一样、没有型号名，PCB 上封装全不对。
正确做法是**按 LCSC 编号取官方库里的真符号和真封装**，本模块负责这件事。

数据源（公开只读，无需鉴权）
----------------------------
* 取器件 ``GET https://easyeda.com/api/products/{LCSC}/components?version=6.5.17``
  - ``result.dataStr.{head, shape}``            原理图符号
  - ``result.packageDetail.dataStr.{head, shape}``  PCB 封装
* 搜索   ``GET https://easyeda.com/api/eda/product/list?keyword=&currentPage=1&pageSize=10``
  - 返回 ``mpn``（厂牌型号）、``number``（LCSC 编号）、``package``、``manufacturer``

单位（最容易踩的坑，两侧**不一样**）
------------------------------------
* 符号：标准版 1 单位 = 0.1 inch = Pro 原理图 1 单位 → **1:1**
* 封装：标准版 1 单位 = 10 mil                 → Pro PCB mil **×10**
* 标准版封装坐标是**画布绝对坐标**，要先减去 ``head.x/head.y`` 再缩放

标准版图元语法（``~`` 分段，引脚再用 ``^^`` 分 sub-seg）
--------------------------------------------------------
* ``P~show~elec~num~x~y~rot~id~0`` 后接 ``^^端点^^路径~色^^名称^^编号^^时钟^^时钟路径``
  ``(x,y)`` 是**引脚外接点**；``rot`` 语义与 Pro 一致（0=朝右，180=朝左，90=下，270=上，
  引脚体从外接点朝 ``rot`` 的反方向伸出）
* ``PL~x1 y1 x2 y2…~描边色~线宽~样式~填充~id~0``  空格分隔点列
* ``PT~M x y L x y Z~描边色~线宽~样式~填充色~id~0``  迷你 SVG 路径
* ``R~x~y~rx~ry~w~h~色~线宽~样式~填充~id~0``
* ``E~cx~cy~rx~ry~色~线宽~样式~填充~id~0``
* ``C~cx~cy~r~色~线宽~样式~填充~id~0``
* ``T~…`` 文本（未使用）
* 封装里：``PAD~形状~x~y~w~h~层~网络~焊盘号~旋转~路径~…`` / ``TRACK~宽~层~…~点列~id~0``
  / ``CIRCLE~cx~cy~r~线宽~层~id~0`` / ``SOLIDREGION~层~~路径~填充~id~…``

层号映射（标准版 → Pro，两边编号**不一致**）
--------------------------------------------
``3 顶层丝印→3`` / ``99 器件轮廓→48`` / ``100 引脚外框→50`` / ``101 极性→49``
/ ``5 钢网→7`` / ``1 顶层→1`` / ``11 多层→12``。只转丝印与器件轮廓 —— 铜层交给焊盘本身，
免得把官方封装里的引线外形画成一块多余的铜。

网络（net）
-----------
本模块只管「器件长什么样、焊盘在哪、焊盘叫什么」，**不碰电气连接**；
连接由 ``easyeda.py`` 依据网表写 ``PAD_NET``。这里额外导出 ``pin_number()``
（引脚名 → 焊盘号）给那边用 —— 网表端点常写 ``U1.VCC``，而封装焊盘号是 ``5``，
没有这层映射就必然把网络绑到错的焊盘上。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_API = "https://easyeda.com/api"
_VERSION = "6.5.17"
_CACHE = Path(__file__).with_name("data") / "lib"
_SCACHE = _CACHE / "_search"      # 搜索结果缓存（关键词 → 命中列表）
_HEAD_FP = Path(__file__).with_name("data") / "efoo_head.json"

_FP_UNIT = 10.0          # 标准版封装 1 单位 = 10 mil
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 标准版封装层号 → Pro 层号
_LAYER_MAP = {
    "1": 1, "2": 2, "3": 3, "4": 4,
    "99": 48, "100": 50, "101": 49,
    "5": 7, "6": 8, "7": 5, "8": 6,
    "11": 12, "10": 11,
}

# 探测结果。**成功永久缓存，失败只缓存 120s** —— 一次抖动（DNS/证书/限流）就把
# 「基于元件库」整条链路在整个进程生命周期里判死，代价太大（实测出现过一次
# 探测失败 → 22 个器件全部退回本地图形，用户看到的就是「封装不对」）。
_L_PROBE: dict[str, Any] = {"ok": None, "why": "", "at": 0.0}
_L_TTL_FAIL = 120.0


# ------------------------------------------------------------------ HTTP

def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


# 立创的接口有频控：连续快打十几次就回 403，之后一段时间全拒。
# 一个真实工程 20+ 器件、每器件 1~3 次搜索，不限速必然撞上 —— 撞上之后
# online() 判定离线，整批器件退回本地图形，用户看到的就是「封装不对」。
# 所以：请求之间留最小间隔；真撞上 403/429 就退避重试一次。
_MIN_GAP = 0.35
_LAST_CALL = [0.0]


def _get(url: str, timeout: float = 25.0, retry: int = 1) -> dict[str, Any]:
    gap = _MIN_GAP - (time.time() - _LAST_CALL[0])
    if gap > 0:
        time.sleep(gap)
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://easyeda.com/",
        "Accept": "application/json, text/plain, */*",
    })
    try:
        raw = urllib.request.urlopen(req, timeout=timeout, context=_ctx()).read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429) and retry > 0:
            time.sleep(2.5)
            return _get(url, timeout, retry - 1)
        raise
    finally:
        _LAST_CALL[0] = time.time()
    return json.loads(raw)


def online() -> bool:
    """库接口是否可用。成功永久缓存；失败只信 120s，之后允许重新探测。"""
    if _L_PROBE["ok"] is not None:
        if _L_PROBE["ok"] or time.time() - _L_PROBE["at"] < _L_TTL_FAIL:
            return bool(_L_PROBE["ok"])
    _L_PROBE["at"] = time.time()
    try:
        d = _get(f"{_API}/eda/product/list?keyword=C12530&currentPage=1&pageSize=1", 12)
        _L_PROBE["ok"] = bool((d.get("result") or {}).get("productList"))
    except Exception as exc:                                     # noqa: BLE001
        _L_PROBE["ok"] = False
        _L_PROBE["why"] = f"{type(exc).__name__}: {exc}"
    return bool(_L_PROBE["ok"])


_SEARCH_CACHE: dict[str, list[dict[str, str]]] = {}


def search(keyword: str, limit: int = 5) -> list[dict[str, str]]:
    """按型号/品名搜立创现货，返回 ``[{lcsc, mpn, package, manufacturer}]``。

    结果也落盘（和 `fetch()` 一样）。原因：一个工程 20 多个器件、每器件要搜
    1~3 个关键词，不缓存的话每次运行都重打同一批请求 —— 立创有频控，连打十几次
    就 403，之后整批器件取不到库件、全部退回本地图形。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    key = kw.lower()
    if key in _SEARCH_CACHE:
        return _SEARCH_CACHE[key][:limit]

    f = _SCACHE / (hashlib.md5(key.encode("utf-8")).hexdigest() + ".json")
    if f.exists():
        try:
            out = json.loads(f.read_text(encoding="utf-8"))
            _SEARCH_CACHE[key] = out
            return out[:limit]
        except Exception:                                        # noqa: BLE001
            f.unlink(missing_ok=True)

    url = (f"{_API}/eda/product/list?keyword={urllib.parse.quote(kw)}"
           f"&currentPage=1&pageSize={max(1, min(limit, 20))}")
    try:
        d = _get(url)
    except Exception:                                            # noqa: BLE001
        return []
    out = []
    for p in ((d.get("result") or {}).get("productList") or []):
        num = (p.get("number") or "").strip()
        if not num.startswith("C"):
            continue
        out.append({
            "lcsc": num,
            "mpn": (p.get("mpn") or "").strip(),
            "package": (p.get("package") or "").strip(),
            "manufacturer": (p.get("manufacturer") or "").strip(),
        })
    _SEARCH_CACHE[key] = out
    _save_json(f, out)
    return out[:limit]


def pick(keyword: str) -> str:
    """把型号搜成 LCSC 编号：优先**型号完全相等**，其次「型号包含关键字」。"""
    hits = search(keyword, 8)
    if not hits:
        return ""
    kw = keyword.strip().upper()
    for h in hits:
        if h["mpn"].upper() == kw:
            return h["lcsc"]
    for h in hits:
        if h["mpn"].upper().startswith(kw):
            return h["lcsc"]
    return hits[0]["lcsc"]


# ------------------------------------------------------------------ 取库

def fetch(lcsc: str) -> dict[str, Any] | None:
    """取一个器件（符号 + 封装 + 元数据）。带磁盘缓存，取不到返回 None。"""
    key = (lcsc or "").strip().upper()
    if not re.fullmatch(r"C\d{3,9}", key or ""):
        return None
    cache = _CACHE / f"{key}.json"
    if cache.exists():
        try:
            return _build(json.loads(cache.read_text(encoding="utf-8")), key)
        except Exception:                                        # noqa: BLE001
            cache.unlink(missing_ok=True)

    try:
        raw = _get(f"{_API}/products/{key}/components?version={_VERSION}")
    except Exception:                                            # noqa: BLE001
        return None
    part = _build(raw, key)
    if not part:
        return None
    _save_json(cache, raw)
    return part


def _save_json(path: Path, data: Any) -> None:
    """原子写盘。⚠ Windows 上必须先把 mkstemp 的 fd 关掉，否则 `os.replace`
    抛 PermissionError，异常一吞缓存就永远是空的、每次都要重新联网。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:                                            # noqa: BLE001
        pass


def cached_count() -> int:
    return len(list(_CACHE.glob("C*.json"))) if _CACHE.exists() else 0


def _build(raw: dict[str, Any], lcsc: str) -> dict[str, Any] | None:
    res = raw.get("result") or {}
    sym_ds = res.get("dataStr") or {}
    if not sym_ds.get("shape"):
        return None
    head = sym_ds.get("head") or {}
    cpara = head.get("c_para") or {}

    pkg = res.get("packageDetail") or {}
    fp_ds = pkg.get("dataStr") or {}

    sym = _parse_symbol(sym_ds.get("shape") or [])
    if not sym["pins"]:
        return None
    fp = _parse_footprint(fp_ds.get("shape") or [], fp_ds.get("head") or {})

    return {
        "lcsc": lcsc,
        "mpn": (cpara.get("Manufacturer Part") or res.get("title") or "").strip() or lcsc,
        "title": (res.get("title") or "").strip(),
        "value": (cpara.get("Value") or "").strip(),
        "package": (cpara.get("package") or pkg.get("title") or "").strip(),
        "manufacturer": (cpara.get("Manufacturer") or "").strip(),
        "prefix": _prefix_of(cpara.get("pre") or "") or "U",
        "sym": sym,
        "fp": fp,
    }


def _prefix_of(pre: str) -> str:
    return re.sub(r"[^A-Za-z]", "", pre or "")[:4].upper()


# ------------------------------------------------------------------ 解析符号

def _num(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _path_points(path: str) -> list[tuple[float, float]]:
    """迷你 SVG 路径 → 折线点。支持 M/L/H/V/Z，曲线只取终点。容错逗号与粘连写法。"""
    if not path:
        return []
    s = path.replace(",", " ")
    # 命令与数字粘连（``M0,0L10,0``）在标准版 shape 里是常态，两个方向都要插空格，
    # 否则 token 会是 ``M0`` —— 既不是纯字母也解析不了，整条路径被静默丢掉。
    s = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=[0-9.\-])", " ", s)
    toks = [t for t in s.replace("\n", " ").split(" ") if t]
    pts: list[tuple[float, float]] = []
    cx = cy = 0.0
    cmd = ""
    rel = False
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t.upper()
            rel = t.islower()          # SVG 里小写 = 相对命令，漏了会把 h -10 读成绝对坐标
            i += 1
            if cmd == "Z":
                if pts:
                    pts.append(pts[0])
                continue
            continue
        try:
            if cmd in ("M", "L", "T"):
                nx, ny = _num(toks[i]), _num(toks[i + 1])
                cx, cy = (cx + nx, cy + ny) if rel else (nx, ny)
                pts.append((cx, cy))
                i += 2
            elif cmd == "H":
                nx = _num(t)
                cx = cx + nx if rel else nx
                pts.append((cx, cy))
                i += 1
            elif cmd == "V":
                ny = _num(t)
                cy = cy + ny if rel else ny
                pts.append((cx, cy))
                i += 1
            elif cmd in ("C", "Q", "S"):
                step = 6 if cmd == "C" else 4
                nx, ny = _num(toks[i + step - 2]), _num(toks[i + step - 1])
                cx, cy = (cx + nx, cy + ny) if rel else (nx, ny)
                pts.append((cx, cy))
                i += step
            elif cmd == "A":
                nx, ny = _num(toks[i + 5]), _num(toks[i + 6])
                cx, cy = (cx + nx, cy + ny) if rel else (nx, ny)
                pts.append((cx, cy))
                i += 7
            else:
                i += 1
        except IndexError:
            break
    return pts


def _pair_list(text: str) -> list[tuple[float, float]]:
    """'13 30 5 30' → [(13,30),(5,30)]。"""
    nums = [x for x in re.split(r"[ ,]+", (text or "").strip()) if x]
    out = []
    for i in range(0, len(nums) - 1, 2):
        out.append((_num(nums[i]), _num(nums[i + 1])))
    return out


_PIN_NAME_RE = re.compile(r"^(\d)~(-?[\d.]+)~(-?[\d.]+)~(-?[\d.]+)~(.*?)~(start|end|middle)~")


def _parse_symbol(shape: list[str]) -> dict[str, Any]:
    pins: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for raw in shape:
        if not isinstance(raw, str) or not raw:
            continue
        kind = raw.split("~", 1)[0]

        if kind == "P":
            parts = raw.split("^^")
            head = parts[0].split("~")
            if len(head) < 8:
                continue
            x, y, rot = _num(head[4]), _num(head[5]), int(_num(head[6]))
            num = (head[3] or "").strip()
            name, name_show = num, True
            if len(parts) > 3:
                m = _PIN_NAME_RE.match(parts[3])
                if m:
                    name_show = m.group(1) != "0"
                    name = (m.group(5) or "").strip() or num
            length = 10.0
            if len(parts) > 2:
                pts = _path_points(parts[2].split("~")[0])
                if len(pts) >= 2:
                    length = abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1]) or 10.0
            pins.append({"num": num, "name": name, "name_show": name_show,
                         "x": x, "y": y, "rot": rot, "len": round(length, 3)})

        elif kind in ("PL", "PT"):
            f = raw.split("~")
            pts = _path_points(f[1]) if kind == "PT" else _pair_list(f[1])
            if len(pts) < 2:
                continue
            fill = (f[5] if len(f) > 5 else "") or ""
            items.append({
                "t": "poly", "pts": pts,
                "closed": kind == "PT" and "z" in (f[1] or "").lower(),
                "fill": bool(fill and fill.lower() not in ("none", "")),
                "w": _num(f[3] if len(f) > 3 else 1, 1.0),
            })

        elif kind == "R":
            f = raw.split("~")
            if len(f) < 11:
                continue
            x, y = _num(f[1]), _num(f[2])
            w, h = _num(f[5]), _num(f[6])
            fill = (f[10] or "") if len(f) > 10 else ""
            items.append({"t": "rect", "x": min(x, x + w), "y": min(y, y + h),
                          "w": abs(w), "h": abs(h),
                          "fill": bool(fill and fill.lower() not in ("none", "")),
                          "w_": _num(f[8] if len(f) > 8 else 1, 1.0)})

        elif kind in ("E", "C"):
            f = raw.split("~")
            if len(f) < 6:
                continue
            rx = _num(f[3]) if kind == "E" else _num(f[3])
            ry = _num(f[4]) if kind == "E" else rx
            fill_i = 7 if kind == "E" else 7
            fill = (f[fill_i] if len(f) > fill_i else "") or ""
            items.append({"t": "ellipse", "x": _num(f[1]), "y": _num(f[2]),
                          "rx": rx, "ry": ry,
                          "fill": bool(fill and fill.lower() not in ("none", ""))})

    # 统一以包围盒中心为原点（标准版符号用的是画布绝对坐标）
    xs = [p["x"] for p in pins] + [q[0] for it in items for q in _item_pts(it)]
    ys = [p["y"] for p in pins] + [q[1] for it in items for q in _item_pts(it)]
    cx = (min(xs) + max(xs)) / 2.0 if xs else 0.0
    cy = (min(ys) + max(ys)) / 2.0 if ys else 0.0
    for p in pins:
        p["x"] = round((p["x"] - cx) * 1.0, 3)
        p["y"] = round((p["y"] - cy) * 1.0, 3)
    items = [_shift_item(it, cx, cy) for it in items]

    xs2 = [p["x"] for p in pins] + [q[0] for it in items for q in _item_pts(it)]
    ys2 = [p["y"] for p in pins] + [q[1] for it in items for q in _item_pts(it)]
    bbox = [min(xs2) - 4, min(ys2) - 4, max(xs2) + 4, max(ys2) + 4] if xs2 else [-20, -20, 20, 20]
    return {"pins": pins, "items": items,
            "bbox": [round(v, 3) for v in bbox],
            "w": round(max(xs2) - min(xs2), 3) if xs2 else 40.0,
            "h": round(max(ys2) - min(ys2), 3) if ys2 else 40.0}


def _item_pts(it: dict[str, Any]) -> list[tuple[float, float]]:
    t = it["t"]
    if t == "poly":
        return it["pts"]
    if t == "rect":
        return [(it["x"], it["y"]), (it["x"] + it["w"], it["y"] + it["h"])]
    if t == "ellipse":
        return [(it["x"] - it["rx"], it["y"] - it["ry"]),
                (it["x"] + it["rx"], it["y"] + it["ry"])]
    return []


def _shift_item(it: dict[str, Any], cx: float, cy: float) -> dict[str, Any]:
    o = dict(it)
    if it["t"] == "poly":
        o["pts"] = [(round(x - cx, 3), round(y - cy, 3)) for x, y in it["pts"]]
    else:
        o["x"] = round(it["x"] - cx, 3)
        o["y"] = round(it["y"] - cy, 3)
    return o


# ------------------------------------------------------------------ 解析封装

def _parse_footprint(shape: list[str], head: dict[str, Any]) -> dict[str, Any]:
    ox, oy = _num(head.get("x")), _num(head.get("y"))
    pads: list[dict[str, Any]] = []
    graphics: list[dict[str, Any]] = []

    for raw in shape:
        if not isinstance(raw, str) or not raw:
            continue
        kind = raw.split("~", 1)[0]

        if kind == "PAD":
            f = raw.split("~")
            if len(f) < 11:
                continue
            shp = (f[1] or "RECT").upper()
            x = (_num(f[2]) - ox) * _FP_UNIT
            y = (_num(f[3]) - oy) * _FP_UNIT
            w = _num(f[4]) * _FP_UNIT
            h = _num(f[5]) * _FP_UNIT
            layer = _LAYER_MAP.get(str(f[6]).strip(), 1)
            num = str(f[8] or "").strip()
            rot = _num(f[9])
            # 只有通孔焊盘才有钻孔：标准版里 THT 焊盘落在多层（layer=11），SMD 落在 1/2。
            # 早先想从行尾"猜"孔径，结果把 3D 模型的偏移量当成了孔径 —— SMD 焊盘全带上了孔。
            drill = 0.0
            tail = f[-1] if f else ""
            if shp in ("HOLE", "ROUNDHOLE"):
                drill = w
            elif str(f[6]).strip() == "11":
                drill = round(min(w, h), 3)
            pads.append({"shape": shp, "x": round(x, 3), "y": round(y, 3),
                         "w": round(w, 3), "h": round(h, 3),
                         "layer": layer, "num": num, "rot": rot,
                         "drill": round(drill, 3), "raw_tail": tail})

        elif kind in ("TRACK", "PL", "PT"):
            f = raw.split("~")
            pts = _pair_list(f[4]) if kind == "TRACK" and len(f) > 4 else _pair_list(f[1])
            if len(pts) < 2:
                continue
            lay = _LAYER_MAP.get(str(f[2]).strip(), 3) if kind == "TRACK" else 3
            if lay not in (1, 2, 3, 4, 48, 49, 50):
                continue
            graphics.append({
                "t": "poly", "layer": lay,
                "w": round(_num(f[1] if kind == "TRACK" else (f[3] if len(f) > 3 else 1), 1) * _FP_UNIT, 3),
                "pts": [(round((x - ox) * _FP_UNIT, 3), round((y - oy) * _FP_UNIT, 3)) for x, y in pts],
                "closed": False,
            })

        elif kind == "CIRCLE":
            f = raw.split("~")
            if len(f) < 7:
                continue
            lay = _LAYER_MAP.get(str(f[5]).strip(), 3)
            if lay not in (3, 4, 48, 49, 50):
                continue
            graphics.append({
                "t": "circle", "layer": lay,
                "x": round((_num(f[1]) - ox) * _FP_UNIT, 3),
                "y": round((_num(f[2]) - oy) * _FP_UNIT, 3),
                "r": round(_num(f[3]) * _FP_UNIT, 3),
                "w": round(_num(f[4], 0.15) * _FP_UNIT, 3),
            })

        elif kind == "SOLIDREGION":
            f = raw.split("~")
            if len(f) < 5:
                continue
            lay = _LAYER_MAP.get(str(f[1]).strip(), 0)
            if lay not in (48, 49, 50):
                continue
            pts = _path_points(f[3])
            if len(pts) < 3:
                continue
            graphics.append({
                "t": "poly", "layer": lay, "w": 0.0,
                "pts": [(round((x - ox) * _FP_UNIT, 3), round((y - oy) * _FP_UNIT, 3)) for x, y in pts],
                "closed": True,
            })

    # 封装包围盒（只算焊盘，丝印可能乱画）
    if pads:
        bbox = [min(p["x"] - p["w"] / 2 for p in pads), min(p["y"] - p["h"] / 2 for p in pads),
                max(p["x"] + p["w"] / 2 for p in pads), max(p["y"] + p["h"] / 2 for p in pads)]
    else:
        bbox = [-50, -50, 50, 50]
    return {"pads": pads, "graphics": graphics, "bbox": [round(v, 3) for v in bbox]}


# ------------------------------------------------------------------ 对外查询

def pin_number(part: dict[str, Any] | None, pin: str) -> str:
    """引脚名/号 → 封装焊盘号。

    网表里常写 ``U1.VCC``（名）或 ``R1.1``（号）。封装焊盘只认号，
    所以要先按符号的 NAME→NUMBER 转一道；对不上就把原值原样返回。
    """
    p = (pin or "").strip()
    if not part:
        return p
    pins = part["sym"]["pins"]
    for q in pins:
        if q["num"] == p:
            return q["num"]
    up = p.upper()
    for q in pins:
        if (q["name"] or "").upper() == up:
            return q["num"]
    # 常见别名：A/K、+/-、正极/负极
    alias = {"A": "ANODE", "K": "CATHODE", "+": "POS", "-": "NEG",
             "阳极": "ANODE", "阴极": "CATHODE", "正极": "POS", "负极": "NEG"}
    want = alias.get(p, alias.get(up, ""))
    if want:
        for q in pins:
            if (q["name"] or "").upper() == want:
                return q["num"]
    return p


def pins_of(part: dict[str, Any] | None) -> dict[str, tuple[float, float, int, float]]:
    """``{焊盘号: (x, y, rot, len)}`` —— 符号坐标系里的引脚外接点。"""
    if not part:
        return {}
    return {q["num"]: (q["x"], q["y"], q["rot"], q["len"]) for q in part["sym"]["pins"]}


# ------------------------------------------------------------------ 生成专业版行

class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"e{self.n}"


_LINESTYLE = ["LINESTYLE", "s_line", None, 0, None, 1, 1]
_FONTSTYLE = ["FONTSTYLE", "s_text", None, None, None, 8, 0, 0, 0, 0, 2, 0]


def sym_rows(part: dict[str, Any], title: str = "", prefix: str = "") -> list[list]:
    """库器件 → ``.esym`` 全部行（专业版原理图符号）。"""
    ids = _Ids()
    sym = part["sym"]
    pre = (prefix or part.get("prefix") or "U").rstrip("?") + "?"
    rows: list[list] = [
        ["DOCTYPE", "SYMBOL", "1.1"],
        ["HEAD", {"originX": 0, "originY": 0, "version": "2", "symbolType": 2}],
        ["PART", title or part.get("mpn") or part.get("lcsc") or "", {"BBOX": sym["bbox"]}],
        _LINESTYLE,
        _FONTSTYLE,
    ]

    for it in sym["items"]:
        if it["t"] == "rect":
            rows.append(["RECT", ids(), it["x"], it["y"],
                         it["x"] + it["w"], it["y"] + it["h"], 0, 0, 0, "s_line", 0])
        elif it["t"] == "ellipse":
            rows.append(["ELLIPSE", ids(), it["x"], it["y"],
                         it["rx"], it["ry"], 0, "s_line", 0])
        elif it["t"] == "poly":
            seq: list[float] = []
            for x, y in it["pts"]:
                seq += [round(x, 3), round(y, 3)]
            rows.append(["POLY", ids(), seq, bool(it.get("closed")), "s_line", 0])

    for p in sym["pins"]:
        pe = ids()
        rows.append(["PIN", pe, 1, None, p["x"], p["y"], p["len"], p["rot"], None, 0, 0])
        rows.append(["ATTR", ids(), pe, "NAME", p["name"] or p["num"], 0, 0,
                     None, None, 0, "s_text", 0])
        rows.append(["ATTR", ids(), pe, "NUMBER", p["num"], 0, 0,
                     None, None, 0, "s_text", 0])
        rows.append(["ATTR", ids(), pe, "Pin Type", "PASSIVE", 0, 0,
                     None, None, 0, "s_text", 0])

    hh = sym["bbox"][3]
    rows.append(["ATTR", ids(), "", "Designator", pre, 0, 1, 0, round(hh + 6, 3), 0, "s_text", 0])
    rows.append(["ATTR", ids(), "", "Name", title or part.get("mpn") or "", 0, 1,
                 0, round(-hh - 6, 3), 0, "s_text", 0])
    return rows


def fp_rows(part: dict[str, Any], title: str = "") -> tuple[list[list], dict[str, str]]:
    """库器件 → ``.efoo`` 全部行（专业版 PCB 封装）。

    同时返回 ``{焊盘号: 焊盘 eid}`` —— 写 PAD_NET 时第 5 项必须是封装里那个焊盘的
    **真实 eid**，拿序号猜会一个器件都渲染不出来。
    """
    head = json.loads(_HEAD_FP.read_text(encoding="utf-8"))
    rows: list[list] = []
    for r in head:
        if r[0] == "HEAD":
            h = dict(r[1])
            h["title"] = title or part.get("package") or ""
            h.pop("uuid", None)
            h.pop("source", None)
            rows.append(["HEAD", h])
        elif r[0] == "CANVAS":
            rows.append(list(r))
        else:
            rows.append(list(r))

    ids = _Ids()
    fp = part["fp"]
    pre = (part.get("prefix") or "U").rstrip("?") + "?"
    rows.append(["ATTR", ids(), 0, "", 3, None, None, "Designator", pre,
                 0, 0, "default", 45, 6, 0, 0, 3, 0, 0, 0, 0, False])

    for g in fp["graphics"]:
        if g["t"] == "circle":
            rows.append(["POLY", ids(), 0, "", g["layer"], g["w"],
                         ["CIRCLE", g["x"], g["y"], g["r"]], 0])
            continue
        seq: list = []
        pts = g["pts"]
        for i, (x, y) in enumerate(pts):
            if i == 1:
                seq.append("L")
            seq += [round(x, 3), round(y, 3)]
        if not seq:
            continue
        if g.get("closed") and (pts[0] != pts[-1]):
            seq += [round(pts[0][0], 3), round(pts[0][1], 3)]
        rows.append(["POLY", ids(), 0, "", g["layer"], max(g["w"], 0.1), seq, 0])

    pad_eids: dict[str, str] = {}
    for pad in fp["pads"]:
        shape = pad["shape"]
        # Pro 只认 ROUND/RECT/OVAL/ELLIPSE(CIRCLE)。标准版还有个 POLYGON，
        # 但它的顶点没带在接口返回里，形状名写进 .efoo 也没用 —— 实测 KiCad
        # 的 Pro 导入器读到 POLYGON 直接段错误（无输出、rc≠0）。降级成同尺寸
        # 矩形：几何是近似的，但至少打得开、焊盘位置与网络都对。
        if shape == "ROUND":
            shp_def: list = ["CIRCLE", pad["w"], pad["w"], 0]
        elif shape in ("OVAL", "ELLIPSE"):
            shp_def = [shape, pad["w"], pad["h"], 0]
        else:
            shp_def = ["RECT", pad["w"], pad["h"], 0]
        drill = pad.get("drill") or 0.0
        drill_def = (["ROUND", drill, drill] if drill > 0
                     else ["ROUND", 0.03937, 0.03937])
        pe = ids()
        pad_eids.setdefault(str(pad["num"]).strip(), pe)
        rows.append(["PAD", pe, 0, "", pad["layer"], pad["num"],
                     pad["x"], pad["y"], int(pad["rot"]),
                     drill_def, shp_def, [], 0, 0, 0, 1, 0,
                     None, None, None, None, 0])
    return rows, pad_eids


def resolve(comp: dict[str, Any]) -> dict[str, Any] | None:
    """给一个网表器件，想办法拿到官方库里的符号 + 封装。

    严格按「能有把握才用」的顺序，**宁可退回本地图形也不张冠李戴**：

    1. 显式 ``lcsc`` —— 用户/上游明确给了编号，直接用；
    2. 型号搜立创，且必须**型号命中 + 封装族一致**（``_score >= 60``）；
    3. 连接器按脚数精确匹配单排直针（脚数不对就不用）；
    4. 通用件（电阻/电容/电感/LED/二极管/三极管/晶振/开关/电池座）按
       「种类 + 封装尺寸」查映射表 —— 这类器件符号与封装本来就是标准的，
       只是阻值/容值不同，Value 走属性显示。

    一条都对不上返回 None，调用方用 `sch._fallback_geom()` 画本地图形。
    踩过的坑：早期只按「第一个搜索结果」取，于是 USB-C 被配成 40P 排针、
    OLED 屏被配成贴片电感 —— 有封装但不该是它，比没有封装更糟。
    """
    code = str(comp.get("lcsc") or "").strip().upper()
    if re.fullmatch(r"C\d{3,9}", code or ""):
        hit = fetch(code)
        if hit:
            hit["from"] = "explicit"
            return hit

    kind = _kind(comp)
    kw_fp = str(comp.get("footprint") or comp.get("package") or "")
    for kw in _candidate_models(comp) + _with_kind(comp):
        ranked = sorted(search(kw, 8), key=lambda h: _score(h, kw, kw_fp, kind), reverse=True)
        for h in ranked[:3]:
            if _score(h, kw, kw_fp, kind) < _CONFIDENT:
                break
            hit = _accept(h["lcsc"], kind)
            if hit:
                hit["from"] = "model"
                return hit

    for kw in _shortened(comp):
        ranked = sorted(search(kw, 8), key=lambda h: _score(h, kw, kw_fp, kind), reverse=True)
        for h in ranked[:2]:
            if _score(h, kw, kw_fp, kind) < _CONFIDENT:
                break
            hit = _accept(h["lcsc"], kind)
            if hit:
                hit["from"] = "model"
                return hit

    # 「规格型」器件：型号在立创库里根本不是那个名字（3mm 直插 LED 叫 C374T-…，
    # USB-C 座叫 TYPE-C 16P），按型号打分必然 0 分。这时用规格词直接搜，
    # 信任立创自己的相关性排序，但**必须**过 `_accept()` 的种类校验。
    for kw in _hints(comp):
        for h in search(kw, 5):
            hit = _accept(h["lcsc"], kind)
            if hit:
                hit["from"] = "hint"
                return hit

    n = len(comp.get("pins") or [])
    if kind in ("connector", "header", "socket") and 2 <= n <= 40:
        # USB / 音频座 / 卡座这类异形连接器拿 2.54 排针凑就是配错，直接放弃。
        blob = " ".join(str(comp.get(k) or "") for k in ("part", "mpn", "model", "value"))
        if _NOT_HEADER_RE.search(blob):
            return _generic(comp)
        for h in search(f"2.54-1*{n}P", 10):
            pkg = h["package"] or ""
            if f"{n}P" not in pkg or "Right Angle" in pkg:
                continue
            hit = fetch(h["lcsc"])
            if hit and len(hit["fp"]["pads"]) == n and len(hit["sym"]["pins"]) == n:
                hit["from"] = "connector"
                return hit

    return _generic(comp)


def _accept(code: str, kind: str) -> dict[str, Any] | None:
    """下载详情，并做最后一道「种类对不对」校验。

    ⚠ 关键：**搜索结果里的 package 是 ``SMD,18.5x15.2mm`` 这种简写**，看不出
    是电感还是屏；只有详情里的 ``IND-SMD_L18.5-W15.2`` 才带类别。所以种类
    冲突必须在**下载之后**再判一次 —— 只在搜索结果上判等于没判，SSD1306
    既是 OLED 驱动也是顺络的贴片电感型号，OLED 屏就是这么被配成电感的。
    """
    hit = fetch(code)
    if not hit:
        return None
    if kind and _pkg_clash(hit.get("package") or "", kind):
        return None
    return hit


# 单搜型号容易撞上同名器件，拼上种类词再搜一轮
_KIND_WORD: dict[str, str] = {
    "module": "模块", "display": "OLED", "oled": "OLED", "lcd": "LCD",
    "sensor": "传感器", "battery": "电池", "led": "LED", "relay": "继电器",
    "motor": "电机", "antenna": "天线", "crystal": "晶振", "buzzer": "蜂鸣器",
    "switch": "开关", "fuse": "保险丝", "microphone": "麦克风", "speaker": "喇叭",
}


def _hints(comp: dict[str, Any]) -> list[str]:
    """用「种类 + 规格」拼搜索词，救那些型号在立创里不叫这个名字的器件。

    只做两类，都是实测能搜到真件的：

    - 连接器写 ``USB-C`` → 库里叫 ``TYPE-C 16P``（直接搜 USB-C 出来的是 CAN 收发器）；
    - 直插 LED 写 ``GREEN-3mm`` → 库里叫 ``3mm Round``（型号是 C374T-… 这种厂家码，
      按型号打分必然 0 分）。
    """
    kind = _kind(comp)
    blob = " ".join(str(comp.get(k) or "")
                    for k in ("value", "footprint", "package", "part", "mpn", "model"))
    out: list[str] = []
    if kind in ("connector", "header", "socket", "usb"):
        if re.search(r"USB[- ]?C|TYPE[- ]?C|TYPEC", blob, re.I):
            m = re.search(r"(\d+)\s*P(?:IN)?\b", blob, re.I)
            out.append(f"TYPE-C {m.group(1)}P" if m else "TYPE-C")
    elif kind in ("led", "led_diode"):
        m = re.search(r"(\d+(?:\.\d+)?)\s*mm", blob, re.I)
        if m:
            out.append(f"LED {m.group(1)}mm")
    return out


def _with_kind(comp: dict[str, Any]) -> list[str]:
    """「型号 + 种类词」的搜索变体，排在纯型号之后试。"""
    word = _KIND_WORD.get(_kind(comp), "")
    return [f"{kw} {word}" for kw in _candidate_models(comp)] if word else []


def _generic(comp: dict[str, Any]) -> dict[str, Any] | None:
    """通用件按种类 + 尺寸取一个标准件，只借它的**符号形状与封装几何**。

    标记 ``from="generic"``：写 device 属性时不能把它的 LCSC 当成这颗料的编号 ——
    「10k 的料」替「220 的料」画符号没问题，但 BOM 里写错编号就是事故。
    """
    hit = fetch(_generic_code(comp))
    if hit:
        hit["from"] = "generic"
    return hit


_PRICEY_RE = re.compile(r"^\s*\d+(\.\d+)?\s*[kKmMuUnNpP]?\s*[ΩωFfHh]?\s*$")

# 型号搜索的最低置信分：低于这个就当没搜到（宁可兜底，不要配错）。
# 40 = 至少要做到「库件型号包含网表型号」（45）或前缀命中（60）；
# 只靠封装族相同（20）不算 —— 那正是 SSD1306 配成电感的分数区间。
_CONFIDENT = 40

# 名字里带这些的连接器**不是**排针，别拿 2.54 排针去凑
_NOT_HEADER_RE = re.compile(
    r"USB|TYPE-?C|TYPEC|JACK|HDMI|MICRO|MINI[- ]?USB|SD|TF|SIM|SLOT|TERMINAL|"
    r"RJ45|DC[- ]?0|DC[- ]?POWER|JST|XH|PH2|MOLEX|SH1|POGO|SPRING|FPC|FFC|IDC|DB9|VGA",
    re.I)


def _kind(comp: dict[str, Any]) -> str:
    """器件种类。网表里既有 ``kind`` 也有 ``type``，两个都认。"""
    return str(comp.get("kind") or comp.get("type") or "").lower()


def _shortened(comp: dict[str, Any]) -> list[str]:
    """把「A-B-C」这类复合型号拆成可搜的片段（原串已经搜过就别重复）。"""
    out: list[str] = []
    for kw in _candidate_models(comp):
        parts = [x for x in re.split(r"[-_\s/]+", kw) if len(x) >= 4]
        if len(parts) >= 2:
            out.append(parts[0])
            joined = "-".join(parts[:2])
            if joined != kw:
                out.append(joined)
    seen: set[str] = set()
    return [x for x in out if not (x.lower() in seen or seen.add(x.lower()))]


def _candidate_models(comp: dict[str, Any]) -> list[str]:
    """哪些字段可能藏着型号。``value`` 要在「像型号」时才拿来搜。"""
    out: list[str] = []
    for key in ("part", "mpn", "model"):
        v = str(comp.get(key) or "").strip()
        if len(v) >= 4:
            out.append(v)
    v = str(comp.get("value") or "").strip()
    if (len(v) >= 4 and not _PRICEY_RE.match(v)
            and re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
        out.append(v)
    seen: set[str] = set()
    return [x for x in out if not (x.lower() in seen or seen.add(x.lower()))]


def _score(hit: dict[str, str], kw: str, want_fp: str, kind: str = "") -> int:
    """给搜索结果打分。**型号是主身份，封装只是加分项。**

    踩过的坑（两版之间的取舍，别来回改）：

    - 第一版「封装不符 -60 一票否决」→ 网表里 footprint 经常是模型瞎写的
      （TP4056 被标成 SOT-23-5，实际 ESOP-8），真器件被自己刷掉了。
      型号对了才是本体，封装只是外形参考，所以现在**不符不扣分**。
    - 第二版「型号前缀命中就够」→ ``SSD1306`` 既是 OLED 驱动也是贴片电感型号，
      OLED 屏被配成了 IND-SMD。所以加了 `_pkg_clash()`：库件封装大类与网表
      种类对不上就直接 0 分（这条比封装加分硬得多）。
    """
    mpn = (hit.get("mpn") or "").upper()
    k = kw.upper()
    if mpn == k:
        score = 100
    elif mpn.startswith(k):
        score = 60
    elif k in mpn:
        score = 45
    elif _family(mpn) and _family(mpn) == _family(k):
        score = 20
    else:
        return 0
    if kind and _pkg_clash(hit.get("package") or "", kind):
        return 0
    if want_fp and hit.get("package"):
        pkg = hit["package"].upper().replace(" ", "")
        wp = want_fp.upper().replace(" ", "")
        fam = _family(wp)
        if pkg == wp or wp in pkg or (fam and pkg.startswith(fam)):
            score += 20
    return score


# 库件封装前缀 → 它只可能是哪几种器件。网表种类不在集合里就直接否决。
# 立创的 package 命名基本都是「大类-工艺_尺寸」，取前缀足够。
_PKG_KIND: dict[str, set[str]] = {
    "IND":  {"inductor", "ind", "bead", "ferrite", "choke", "coil"},
    "BAT":  {"battery", "bat", "cell", "batteryholder", "battery_holder"},
    "HDR":  {"connector", "header", "socket", "pin", "pinheader", "排针"},
    "LED":  {"led", "diode", "led_diode"},
    "RES":  {"resistor", "res"},
    "CAP":  {"capacitor", "cap"},
    "BUZ":  {"buzzer", "speaker", "蜂鸣器"},
    "XTAL": {"crystal", "oscillator", "resonator", "xtal"},
    "SW":   {"switch", "key", "button", "tact", "tactile"},
    "KEY":  {"switch", "key", "button", "tact", "tactile"},
    "DIODE": {"diode", "led"},
    "FUSE": {"fuse"},
    "RELAY": {"relay"},
    "MOTOR": {"motor"},
    "ANT":  {"antenna", "ant"},
}

# 直插件种类的额外别名：网表里可能写 "module" / "display" 这类上位词
_PKG_KIND_LOOSE = ("SOT", "SOP", "SOIC", "TSSOP", "ESOP", "MSOP", "QFN", "DFN",
                   "QFP", "LQFP", "TQFP", "BGA", "LGA", "TO-", "SOD", "DO-", "MODULE")


def _pkg_clash(pkg: str, kind: str) -> bool:
    """库件封装的大类与网表器件种类冲突 → True（不能用）。"""
    k = (kind or "").lower().replace(" ", "")
    if not k or not pkg:
        return False
    p = pkg.upper()
    for pre, kinds in _PKG_KIND.items():
        if p.startswith(pre):
            return k not in kinds
    return False


def _family(fp: str) -> str:
    """封装大类：SOT-23-5 / SOT-23 → SOT-23，QFN-16-0.5 → QFN。"""
    m = re.match(r"([A-Z]+(?:-\d+)?)", fp or "")
    return m.group(1) if m else ""


def _generic_code(comp: dict[str, Any]) -> str:
    kind = _kind(comp)
    size = _size_tag(comp)
    table = _GENERIC.get(kind) or {}
    if not table:
        return ""
    # 认不出贴片代号、又明写着直插（3mm / 5mm / THT / DIP）→ 不给贴片映射，
    # 否则 3mm 直插 LED 会被贴上 0805 的封装。
    if not size:
        blob = " ".join(str(comp.get(k) or "")
                        for k in ("value", "footprint", "package", "part", "desc", "note"))
        if _THT_RE.search(blob):
            return ""
    return table.get(size) or table.get("default") or ""


# 直插判据。⚠ 别把封装尺寸里的 ``6mm`` 当直插 —— ``TACT-6MM`` 是贴片轻触开关，
# 只有 LED 这类才会用 ``3mm/5mm`` 表示直插直径，所以 mm 前面必须带 LED。
_THT_RE = re.compile(
    r"THT|直插|DIP[-_]?\d|THROUGH[- ]?HOLE|插件|(?:LED|LAMP)[-_]?\d+(?:\.\d+)?\s*mm", re.I)


def _size_tag(comp: dict[str, Any]) -> str:
    """从封装名 / 尺寸里抽 ``0402`` 这类代号。"""
    blob = " ".join(str(comp.get(k) or "") for k in ("footprint", "package", "size"))
    m = re.search(r"(0201|0402|0603|0805|1206|1210|1812|2010|2512)", blob)
    return m.group(1) if m else ""


# 通用件的代表型号：符号形状 + 封装尺寸都一样，具体阻值/容值走 Value 属性，
# 所以拿库里的一个标准件当"模板"是成立的。编号都真实可查。
_GENERIC: dict[str, dict[str, str]] = {
    "resistor":  {"default": "C25744", "0402": "C25744", "0603": "C25804", "0805": "C17567"},
    "capacitor": {"default": "C12530", "0402": "C12530", "0603": "C19702", "0805": "C45783"},
    "led":       {"default": "C2297", "0805": "C2297", "0603": "C2286"},
    "diode":     {"default": "C8678"},
    "inductor":  {"default": "C1045"},
    "switch":    {"default": "C318884"},
    "crystal":   {"default": "C115962"},
    "transistor": {"default": "C6749"},
    "buzzer":    {"default": "C409012"},
    # 连接器 / 电池**不给** default：这类件「外形差很多」是常态 ——
    # 40P 排针冒充 USB-C、CR2032 座冒充 800mAh 锂电，封装就彻底错了，
    # 不如走本地兜底图形（脚数、节距都对，只是没有官方丝印）。
}


def describe(part: dict[str, Any]) -> str:
    """一行摘要，给日志和工具输出用。"""
    if not part:
        return "（无）"
    return (f"{part['lcsc']} {part.get('mpn') or part.get('title')} "
            f"[{part.get('package')}] 符号 {len(part['sym']['pins'])} 脚 / "
            f"封装 {len(part['fp']['pads'])} 焊盘")
