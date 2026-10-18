"""阶段流水线：串行依赖、开工门禁、跨阶段约束传递、交付清单与打包。

为什么要有这一层：
智能硬件的五个阶段不是五份互不相干的文档，而是一条**几何与电气双向约束链**——
    PRD 约束外观 → 外观定外形 → 结构定内腔/安装柱（决定 PCB 板框）
    → 板框面积决定器件封装与走线层数 → 器件与引脚决定固件驱动与仿真
上游产物没落盘就开工，下游只能凭想象填数字，最后评审阶段的「尺寸对不上」
其实是编排阶段就埋下的。所以这里把依赖写成数据（stages.STAGES），
让门禁由代码判定，而不是靠模型记得「先做结构再做硬件」。

对外提供：
- gate()          开工门禁（上游是否齐备 + 该读哪些文件 + 上游传下来的约束）
- constraints()   上游关键数值摘要（结构尺寸 → PCB 板型建议；BOM → 器件功能清单）
- manifest()      交付清单：阶段标题 ← 规划标题，产物逐个列出，供前端分组与打包
- zip_stage/zip_all  按阶段或全量打包
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable

import config
import stages

# ---------------------------------------------------------------- 数值提取

_NUM = r"(\d+(?:\.\d+)?)"
_BOX = re.compile(_NUM + r"\s*[×xX*]\s*" + _NUM + r"\s*[×xX*]\s*" + _NUM)
_MM_LINE = re.compile(rf"^.*{_NUM}\s*(?:mm|毫米).*$", re.I | re.M)

# PCB 板框相对壳体内腔的单边装配余量（mm）：太小装不进去，太大浪费面积
_CLEARANCE = 1.0
# 安装柱周围禁止布件的禁布区半径（mm）
_KEEPOUT_R = 2.5


def _read(root: Path, rel: str, limit: int = 20000) -> str:
    try:
        p = root / rel
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        pass
    return ""


def _first_box(text: str) -> tuple[float, float, float] | None:
    m = _BOX.search(text or "")
    if not m:
        return None
    try:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    except ValueError:
        return None


def structure_metrics(root: Path) -> dict[str, Any]:
    """从结构产物里读出壳体外形/内腔尺寸，供硬件定板框。

    只看两个可信来源：consistency.md（CAD 回传的包围盒）与 structure.md。
    AI 出的爆炸图上那些尺寸是模型编的，一律不采信。
    """
    cons = _read(root, "20_structure/consistency.md")
    st = _read(root, "20_structure/structure.md")
    box = _first_box(cons) or _first_box(st)
    out: dict[str, Any] = {"overall_mm": list(box) if box else None, "source": "", "lines": []}
    if box:
        out["source"] = "20_structure/consistency.md" if _first_box(cons) else "20_structure/structure.md"
    # 内腔 / 壁厚 / 安装柱相关原文行，一并交给硬件工程师自己核对
    keys = ("内腔", "壁厚", "安装柱", "限高", "净空", "开孔")
    lines = [ln.strip() for ln in st.splitlines() if any(k in ln for k in keys)]
    out["lines"] = lines[:12]
    return out


def pcb_envelope(metrics: dict[str, Any]) -> dict[str, Any]:
    """把壳体外形推成 PCB 板框建议：能装进去是前提，装不进去后面全白做。"""
    box = metrics.get("overall_mm")
    if not box or len(box) != 3:
        return {"ok": False, "hint": "未读到结构外形尺寸，先让结构阶段落盘 structure.md / consistency.md。"}
    w, d, h = float(box[0]), float(box[1]), float(box[2])
    bw, bd = round(min(w, d) - 2 * _CLEARANCE, 1), round(max(w, d) - 2 * _CLEARANCE, 1)
    area = bw * bd
    # 板面积 → 封装与层数的经验判据（保守取值，够用且不虚标）
    if area < 900:
        pkg, layers, why = "0402 为主 + QFN/DFN/BGA0.4", 4, "板面 <900mm²，2 层走不通：BGA/QSPI 扇出与地平面完整性都不够"
    elif area < 2500:
        pkg, layers, why = "0402/0603 混用 + QFN/LQFP", 2, "900–2500mm²：单面布件 + 2 层可闭合，成本最优"
    else:
        pkg, layers, why = "0603 为主 + LQFP/QFP", 2, ">2500mm²：面积宽裕，优先降成本与可维修性"
    return {
        "ok": True,
        "shell_mm": [w, d, h],
        "board_mm": [bw, bd],
        "board_area_mm2": round(area, 1),
        "keepout_hole_r_mm": _KEEPOUT_R,
        "max_component_height_mm": round(max(0.0, h / 2 - 1.5), 1),
        "suggested_package": pkg,
        "suggested_layers": layers,
        "reason": why,
        "hint": (
            f"板框按壳体内腔留 {_CLEARANCE}mm 单边余量取 {bw}×{bd}mm；"
            f"安装柱周围 {_KEEPOUT_R}mm 禁布；器件限高 {round(max(0.0, h/2 - 1.5), 1)}mm。"
            f"板面积 {round(area,1)}mm² → 封装 {pkg}、{layers} 层板（{why}）。"
            "以上为起始判据，最终以你的布线密度与阻抗要求复核。"
        ),
    }


def hardware_devices(root: Path) -> dict[str, Any]:
    """读回硬件阶段的器件与引脚，固件照着它写驱动（不能凭空编引脚）。"""
    net_raw = _read(root, "30_hardware/netlist.json")
    comps: list[dict[str, Any]] = []
    pins: list[str] = []
    try:
        data = json.loads(net_raw) if net_raw.strip() else {}
        if isinstance(data, dict):
            comps = [c for c in (data.get("components") or []) if isinstance(c, dict)]
            for c in (data.get("connections") or []):
                if isinstance(c, dict):
                    pins += [str(c.get("from") or ""), str(c.get("to") or "")]
    except json.JSONDecodeError:
        comps = []
    bom_rows = 0
    bom = _read(root, "30_hardware/bom.csv")
    if bom.strip():
        bom_rows = max(0, len([ln for ln in bom.splitlines() if ln.strip()]) - 1)
    return {
        "components": comps[:40],
        "n_components": len(comps),
        "connections": sorted({p for p in pins if p})[:40],
        "bom_rows": bom_rows,
        "has_bom": bom_rows > 0,
        "has_netlist": bool(comps),
    }


def appearance_state(root: Path) -> dict[str, Any]:
    """外观阶段现状：图**能不能用**、有没有提示词存档 —— 结构开工前必须先确认这个。

    只看「有没有 .png」是不够的：实测有子智能体在出图成功后又把 700 KB 的渲染图
    覆盖成 11 字节的 `placeholder`，纯路径判据照样放行，结构阶段就拿着假图建模。
    所以这里区分「有效图片」与「坏图」，坏图必须显式报出来。
    """
    import assets

    broken = assets.bad_images(root, "10_appearance")
    bad = {rel for rel, _why in broken}
    imgs: list[str] = []
    for p in assets.image_files(root, "10_appearance"):
        rel = p.relative_to(root).as_posix()
        if rel not in bad:
            imgs.append(rel)
    views: list[str] = []
    try:
        views = [
            p.relative_to(root).as_posix()
            for p in sorted((root / "10_appearance").rglob("*.svg"))
        ]
    except OSError:
        pass
    has_prompts = (root / "10_appearance" / "prompts.json").is_file()
    return {
        "images": imgs,
        "n_images": len(imgs),
        "broken": broken,
        "views": views,
        "has_prompts": has_prompts,
        # 有坏图时故意不放行：重出一张图只要十几秒，而拿着假图往下走会毁掉整条链
        "ready": bool(imgs),
        # 出图彻底不可用时（网关挂/无密钥）允许「文字同源」：用提示词 + 工程视图继续，
        # 但要在产物里标明形状来源，不能假装有渲染图
        "text_only_ok": (not imgs) and not broken and (has_prompts or bool(views)),
    }


# ---------------------------------------------------------------- 门禁

def _after(sid: str, ref: str) -> bool:
    """sid 在串行链上是否排在 ref 之后（即受 ref 的确认结果约束）。"""
    chain = list(stages.CHAIN)
    try:
        return chain.index(sid) > chain.index(ref)
    except ValueError:
        return False


def gate(root: Path, sid: str) -> dict[str, Any]:
    """判断某阶段能否开工。磁盘产物是唯一判据，模型说做完了不算。

    外加一道**人工确认闸门**：外观、结构这两阶段产物齐了也不算完，必须用户在界面上
    看过并点确认（或提了修改意见重做后再确认）。没有这道闸，模型会一路往下跑，
    等六阶段全跑完才发现外观比例不对 —— 那时候改外观等于整条链重做。
    """
    import hitl

    paths = stages.scan_dir(root)
    human: list[dict[str, Any]] = []
    meta = stages.meta(sid)
    ready, missing = stages.gate_ready(sid, paths)
    done_map = stages.stages_done(paths)
    deps = stages.depends_of(sid)
    upstream = [
        {"stage": d, "label": stages.stage_label(d), "done": bool(done_map.get(d))}
        for d in deps
    ]
    # 该读哪些上游文件：命中就列出来，没命中给出「缺什么」
    inputs = []
    for rel in stages.GATE_INPUTS.get(sid, []):
        hit = any(p == rel or p.endswith("/" + rel.split("/")[-1]) for p in paths)
        inputs.append({"path": rel, "present": hit})

    ctx: dict[str, Any] = {}
    if sid == "20_structure":
        app = appearance_state(root)
        ctx["appearance"] = app
        # 有坏图**且没有任何可用图**才卡住：一张好图在手时，坏的那张只提示不拦路
        if app["broken"] and not app["images"]:
            ready = False
            detail = "；".join(f"{rel}（{why}）" for rel, why in app["broken"][:3])
            missing.append(
                f"10_appearance/ 有 {len(app['broken'])} 张损坏/占位的图：{detail}。"
                "请重新调用 generate_product_image 出图（reference=auto 的图要基于上一张），"
                "不要用文本工具写图片；重出仍失败就删掉坏图，改用「提示词 + 工程视图」继续。"
            )
        elif not app["ready"] and not app["text_only_ok"]:
            ready = False
            missing.append("10_appearance/ 下没有任何渲染图或提示词存档")
    elif sid == "30_hardware":
        m = structure_metrics(root)
        ctx["structure"] = m
        ctx["pcb"] = pcb_envelope(m)
        if not m.get("overall_mm"):
            ready = False
            missing.append("20_structure/ 里读不到外形尺寸（structure.md / consistency.md）")
    elif sid == "40_firmware":
        hw = hardware_devices(root)
        ctx["hardware"] = hw
        if not (hw["has_netlist"] or hw["has_bom"]):
            ready = False
            missing.append("30_hardware/ 里没有 netlist.json 或 bom.csv")

    # 人工确认闸门：上游「必须经人确认」的阶段没被确认，下游一律不许开工。
    # 判据读磁盘上的确认记录，重启 / 续跑后依旧有效。
    if config.HUMAN_GATE:
        for gsid in config.HUMAN_GATE_STAGES:
            if not _after(sid, gsid):
                continue
            st = hitl.state_of(root, gsid)
            human.append({"stage": gsid, "label": stages.stage_label(gsid), **st})
            if st.get("done"):
                continue
            ready = False
            last = str(st.get("feedback") or "").strip()
            missing.append(
                f"「{stages.stage_label(gsid)}」还没经用户确认。"
                f'请先调用 confirm_with_user(stage="{gsid}", summary="…", files="…") '
                "把本阶段产物提交给用户确认，等它返回确认结果后再开工下游"
                + (f"；上一轮用户意见：{last[:200]}" if last else "")
            )

    return {
        "stage": sid,
        "label": meta.get("label", sid),
        "ready": ready,
        "missing": missing,
        "human_review": human,
        "upstream": upstream,
        "inputs": inputs,
        "deliverables": stages.deliverables_of(sid),
        "pass_down": meta.get("pass_down", ""),
        "context": ctx,
        "files": [p for p in paths if p.startswith(sid + "/")],
    }


def snapshot(root: Path) -> dict[str, Any]:
    """整条链的状态：哪个阶段已完成、下一个是谁、它现在能不能开工。"""
    paths = stages.scan_dir(root)
    done = stages.stages_done(paths)
    items = []
    nxt = ""
    for s in stages.STAGES:
        sid = s["id"]
        is_done = bool(done.get(sid))
        ok, _missing = stages.gate_ready(sid, paths)
        started = any(p.startswith(sid + "/") for p in paths)
        status = "done" if is_done else ("active" if started else ("ready" if ok else "blocked"))
        if not is_done and not nxt:
            nxt = sid
        items.append(
            {
                "id": sid,
                "label": s["label"],
                "status": status,
                "depends": s.get("depends") or [],
                "n_files": sum(1 for p in paths if p.startswith(sid + "/")),
                "deliverables": stages.deliverables_of(sid),
            }
        )
    return {
        "chain": stages.CHAIN,
        "stages": items,
        "next": nxt,
        "progress": stages.stage_progress(paths),
    }


# ---------------------------------------------------------------- 交付清单

# 规划项 → 阶段的关键词（与前端 web/lib/plan.ts 的 kw 保持一致，改一处两边都要改）
_PLAN_KW: dict[str, str] = {
    "00_prd": r"需求|PRD|规格|澄清|产品定义|立项",
    "10_appearance": r"外观|工业设计|造型|渲染|色彩|材质|ID\b",
    "20_structure": r"结构|机械|CAD|壳体|装配|分件|建模",
    "30_hardware": r"硬件|电路|原理图|BOM|元器件|选型|接线|网表|PCB",
    "40_firmware": r"固件|代码|Arduino|仿真|烧录|编程|软件",
    "90_review": r"评审|review|复核|汇总|验收|交付",
}


def plan_titles(todos: Iterable[dict[str, Any]]) -> dict[str, str]:
    """把模型写的任务清单映射到阶段：产物分组的标题直接取规划里那一句。"""
    out: dict[str, str] = {}
    for t in todos or []:
        content = str((t or {}).get("content") or "")
        if not content:
            continue
        for sid, kw in _PLAN_KW.items():
            if sid in out:
                continue
            if re.search(kw, content, re.I):
                out[sid] = content.strip()
                break
    return out


def file_title(sid: str, rel: str) -> str:
    """产物标题：命中阶段交付契约就用契约里的中文标题，否则回落到文件名。"""
    for d in stages.deliverables_of(sid):
        if d["file"] == rel or rel.endswith("/" + d["file"].split("/")[-1]):
            return d["title"]
    return Path(rel).name


def manifest(root: Path, todos: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """交付清单：阶段（标题取自规划）→ 产物（逐个可单独下载）。"""
    paths = stages.scan_dir(root)
    snap = snapshot(root)
    titles = plan_titles(todos or [])
    groups = []
    total_size = 0
    for s in snap["stages"]:
        sid = s["id"]
        files = []
        for rel in paths:
            if not rel.startswith(sid + "/"):
                continue
            try:
                size = (root / rel).stat().st_size
            except OSError:
                size = 0
            total_size += size
            files.append(
                {"path": rel, "name": Path(rel).name, "title": file_title(sid, rel), "size": size}
            )
        groups.append(
            {
                "id": sid,
                "label": s["label"],
                "plan_title": titles.get(sid) or s["label"],
                "status": s["status"],
                "n_files": len(files),
                "size": sum(f["size"] for f in files),
                "files": files,
            }
        )
    # 不在任何阶段目录下的文件（例如会话记录）单独归到「其它」，
    # 否则它们只在「全部打包」里出现，界面上看不见却下载得到，容易让人以为漏了
    extra = [
        p for p in paths if not any(p.startswith(s["id"] + "/") for s in stages.STAGES)
    ]
    if extra:
        files = []
        for rel in extra:
            try:
                size = (root / rel).stat().st_size
            except OSError:
                size = 0
            files.append({"path": rel, "name": Path(rel).name, "title": Path(rel).name, "size": size})
        groups.append(
            {
                "id": "",
                "label": "其它",
                "plan_title": "其它文件",
                "status": "done",
                "n_files": len(files),
                "size": sum(f["size"] for f in files),
                "files": files,
            }
        )

    return {
        "chain": stages.CHAIN,
        "stages": groups,
        "next": snap["next"],
        "progress": snap["progress"],
        "total_files": len(paths),
        "total_size": total_size,
    }


# ---------------------------------------------------------------- 打包

def _zip_bytes(root: Path, rels: list[str], prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in rels:
            p = root / rel
            if p.is_file():
                zf.write(p, arcname=(f"{prefix}/{rel}" if prefix else rel))
    return buf.getvalue()


def zip_stage(root: Path, sid: str) -> tuple[bytes, str]:
    rels = [p for p in stages.scan_dir(root) if p.startswith(sid + "/")]
    label = stages.stage_label(sid)
    name = f"{sid}_{label}.zip"
    return _zip_bytes(root, rels), name


def zip_all(root: Path, run_id: str = "", todos: Iterable[dict[str, Any]] | None = None) -> tuple[bytes, str]:
    rels = stages.scan_dir(root)
    buf = io.BytesIO()
    man = manifest(root, todos)
    prefix = run_id or root.name
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 先放一份清单：解压后知道每个文件对应规划里的哪一步
        zf.writestr(
            f"{prefix}/交付清单.md",
            _manifest_md(man, prefix),
        )
        for rel in rels:
            p = root / rel
            if p.is_file():
                zf.write(p, arcname=f"{prefix}/{rel}")
    return buf.getvalue(), f"{prefix}_全部产物.zip"


def _manifest_md(man: dict[str, Any], prefix: str) -> str:
    lines = [f"# {prefix} 交付清单", ""]
    for g in man["stages"]:
        if not g["files"]:
            continue
        lines.append(f"## {g['id']} {g['label']} —— 规划项：{g['plan_title']}")
        lines.append("")
        lines.append("| 产物 | 文件 | 大小 |")
        lines.append("|---|---|---|")
        for f in g["files"]:
            kb = f"{f['size']/1024:.1f} KB" if f["size"] < 1024 * 1024 else f"{f['size']/1048576:.2f} MB"
            lines.append(f"| {f['title']} | {f['path']} | {kb} |")
        lines.append("")
    lines.append(f"共 {man['total_files']} 个文件。")
    return "\n".join(lines)


__all__ = [
    "gate", "snapshot", "manifest", "plan_titles", "file_title",
    "structure_metrics", "pcb_envelope", "hardware_devices", "appearance_state",
    "zip_stage", "zip_all",
]
