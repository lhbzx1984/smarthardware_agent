"""器件库查询：把网表里的元件匹配到封装 / 典型高度 / 引脚定义。

匹配顺序：
  1. 逐条 parts 的 match 关键词，在 "type + value + note + id" 拼出的文本里找（长词优先）
  2. 没命中就按 kind 取 defaults（至少给个合理封装）
  3. 都没有 → 返回 None，让调用方保留空值

**绝不臆造 LCSC 编号**：库里没填就是没填，宁可空着也不能让用户买到错料。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).with_name("data") / "parts.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    try:
        return json.loads(_DATA.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  库坏了不能拖垮整条硬件链路
        return {"version": 0, "defaults": {}, "parts": []}


def all_parts() -> list[dict[str, Any]]:
    return list(_load().get("parts") or [])


def default_of(kind: str) -> dict[str, Any]:
    return dict((_load().get("defaults") or {}).get(kind) or {})


def match(comp: dict[str, Any]) -> dict[str, Any] | None:
    """返回 {name,kind,footprint,lcsc,pins,height_mm,note}；没匹配到返回 None。"""
    hay = " ".join(
        str(comp.get(k) or "")
        for k in ("type", "value", "note", "name", "id", "kind", "footprint")
    ).lower()

    hits: list[tuple[int, dict[str, Any]]] = []
    for p in all_parts():
        for kw in p.get("match") or []:
            if str(kw).lower() in hay:
                hits.append((len(str(kw)), p))
                break
    if hits:
        hits.sort(key=lambda t: -t[0])
        p = dict(hits[0][1])
        p.setdefault("lcsc", "")
        return p

    d = default_of(str(comp.get("kind") or ""))
    return d or None


def resolve(comp: dict[str, Any]) -> dict[str, Any]:
    """补齐单个元件：footprint / height_mm / pins 缺失时从库里取，找不到就留空。"""
    out = dict(comp)
    hit = match(comp)
    if hit:
        out["footprint"] = comp.get("footprint") or hit.get("footprint", "")
        out["lcsc"] = comp.get("lcsc") or hit.get("lcsc", "")
        out["height_mm"] = comp.get("height_mm") or hit.get("height_mm") or default_of(
            str(comp.get("kind") or "")
        ).get("height_mm", 0)
        out["part_name"] = comp.get("part_name") or hit.get("name", "")
        if hit.get("size_mm"):
            out["size_mm"] = list(hit["size_mm"])
        lib_pins = [str(p) for p in (hit.get("pins") or []) if str(p).strip()]
        if lib_pins and comp.get("kind") in ("module", "ic", "connector"):
            # 模块/IC/连接器：用库里的真实引脚名（对得上 datasheet，PCB 阶段才能映射物理脚），
            # 网表里出现但库里没有的补在后面，一个都不丢
            seen = {p.lower() for p in lib_pins}
            out["pins"] = lib_pins + [p for p in (comp.get("pins") or []) if str(p).lower() not in seen]
            out["pin_source"] = "library"
        elif not comp.get("pins") and lib_pins:
            out["pins"] = lib_pins
            out["pin_source"] = "library"
    out.setdefault("height_mm", default_of(str(comp.get("kind") or "")).get("height_mm", 0))
    return out


def size_of(comp: dict[str, Any]) -> tuple[float, float]:
    """元件占位尺寸 (w, h)，mm。库里没有就按 kind 兜底，绝不返回 0。

    这是布局用的近似值（含焊盘余量），不是封装精确外形 —— 够避让判断即可。
    """
    s = comp.get("size_mm")
    if isinstance(s, (list, tuple)) and len(s) >= 2:
        try:
            w, h = float(s[0]), float(s[1])
        except (TypeError, ValueError):
            w = h = 0.0
        if w > 0 and h > 0:
            return w, h
    d = default_of(str(comp.get("kind") or ""))
    s = d.get("size_mm")
    if isinstance(s, (list, tuple)) and len(s) >= 2:
        try:
            return float(s[0]), float(s[1])
        except (TypeError, ValueError):
            pass
    return 6.0, 6.0


def stats() -> dict[str, Any]:
    d = _load()
    kinds: dict[str, int] = {}
    for p in d.get("parts") or []:
        kinds[p.get("kind", "?")] = kinds.get(p.get("kind", "?"), 0) + 1
    return {
        "version": d.get("version", 0),
        "parts": len(d.get("parts") or []),
        "defaults": len(d.get("defaults") or {}),
        "by_kind": kinds,
        "with_lcsc": sum(1 for p in (d.get("parts") or []) if p.get("lcsc")),
    }
