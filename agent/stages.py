"""阶段判据：以磁盘产物反推进度，模型嘴上说没说不算数。

与前端 `web/lib/plan.ts` 的 STAGES 必须保持一致 —— 两边各写一套迟早会漂移，
判定逻辑改一处就得同步另一处。这里的正则是 JS 版的直译。

为什么判据是「路径里出现该阶段目录」而不是「以它开头」：模型偶尔把文件写进
自己臆造的子目录（实测出现过 `home/user/repos/prod-xxx/40_firmware/firmware.ino`），
严格要求顶层会把已完成阶段误判成未完成。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

# 智能硬件设计是**强相关的串行链**：PCB 板型要装进结构壳体的内腔，板型大小反过来
# 决定器件封装与层数，器件功能又决定固件要写哪些驱动。所以每一阶段都声明了
# depends（上游阶段）与 requires（开工前磁盘上必须已经存在的产物正则）。
# 门禁由 pipeline.gate() 执行，主智能体与子智能体都靠它判断「能不能开工」。
STAGES: list[dict[str, Any]] = [
    {
        "id": "00_prd",
        "label": "需求定义",
        "match": r"(?:^|/)00_prd/[^/]*\.md$",
        "depends": [],
        "requires": [],
        "deliverables": [{"file": "00_prd/prd.md", "title": "产品需求规格书（PRD）"}],
        "pass_down": "尺寸/供电/成本/环境等硬约束，是后面每一步的取值边界。",
    },
    {
        "id": "10_appearance",
        "label": "外观设计",
        "match": r"(?:^|/)10_appearance/[^/]*\.md$",
        "depends": ["00_prd"],
        "requires": [r"(?:^|/)00_prd/[^/]*\.md$"],
        "deliverables": [
            {"file": "10_appearance/appearance.md", "title": "外观设计方案"},
            {"file": "10_appearance/orthographic.svg", "title": "外观三视图"},
            {"file": "10_appearance/render-01.png", "title": "产品外观渲染图（基线）"},
        ],
        "pass_down": "外形尺寸、分模线、开孔/视窗位置 —— 结构建模的形状唯一来源。",
    },
    {
        "id": "20_structure",
        "label": "结构设计",
        "match": r"(?:/|^)20_structure/(?:structure\.md$|[^/]*\.(?:step|stp|stl)$)",
        "depends": ["10_appearance"],
        # 结构必须等外观出图：没图就建模等于重新想象一台产品，图与模型必然对不上
        "requires": [r"(?:^|/)10_appearance/[^/]*\.(?:md|png|svg|json)$"],
        "deliverables": [
            {"file": "20_structure/structure.md", "title": "结构设计说明书"},
            {"file": "20_structure/model.py", "title": "参数化 CAD 建模脚本"},
            {"file": "20_structure/consistency.md", "title": "外观-结构一致性对照"},
            {"file": "20_structure/exploded.svg", "title": "装配爆炸视图"},
        ],
        # 硬件 PCB 板型就从这几个数推出来，结构阶段必须把它们写实
        "pass_down": "壳体内腔净尺寸、安装柱位置与孔径、限高区、接口开孔坐标 —— PCB 外形的硬边界。",
    },
    {
        "id": "30_hardware",
        "label": "硬件设计",
        "match": r"(?:/|^)30_hardware/(?:[^/]*\.md$|bom\.csv$)",
        "depends": ["20_structure"],
        # 板型要能装进去：没有结构尺寸就定不了板轮廓，也定不了封装与层数
        "requires": [
            r"(?:/|^)20_structure/(?:structure\.md$|[^/]*\.(?:step|stp|stl)$)",
        ],
        "deliverables": [
            {"file": "30_hardware/hardware.md", "title": "硬件设计方案"},
            {"file": "30_hardware/pcb-layout.md", "title": "PCB 板型与装配约束（板框/封装/层数）"},
            {"file": "30_hardware/netlist.json", "title": "电路网表"},
            {"file": "30_hardware/schematic.svg", "title": "电路原理图"},
            {"file": "30_hardware/bom.csv", "title": "物料清单（BOM）"},
            {"file": "30_hardware/cirkit-steps.md", "title": "Cirkit Designer 复现步骤"},
        ],
        "pass_down": "器件清单与引脚分配、板上资源（I2C/SPI/UART/PWM 通道）—— 固件按它写驱动与仿真。",
    },
    {
        "id": "40_firmware",
        "label": "固件与仿真",
        "match": r"(?:^|/)40_firmware/",
        "depends": ["30_hardware"],
        # 软件是照着器件功能写的：BOM/引脚没定就写固件，等于凭空编引脚
        "requires": [
            r"(?:/|^)30_hardware/(?:netlist\.json$|bom\.csv$|[^/]*\.md$)",
        ],
        "deliverables": [
            {"file": "40_firmware/firmware.ino", "title": "固件源码（Arduino）"},
            {"file": "40_firmware/simulation.md", "title": "仿真步骤与测试用例"},
        ],
        "pass_down": "状态机与外设驱动清单，供评审核对「引脚与代码是否一致」。",
    },
    {
        "id": "90_review",
        "label": "交叉评审",
        "match": r"(?:^|/)90_review/[^/]*\.md$",
        "depends": ["40_firmware"],
        "requires": [
            r"(?:^|/)40_firmware/",
            r"(?:/|^)30_hardware/(?:netlist\.json$|bom\.csv$)",
        ],
        "deliverables": [{"file": "90_review/review.md", "title": "跨专业一致性评审报告"}],
        "pass_down": "",
    },
]

# 阶段 → 该阶段开工前必须读到的上游文件（相对路径，命中任一即可），供 stage_gate 直接列清单
GATE_INPUTS: dict[str, list[str]] = {
    "10_appearance": ["00_prd/prd.md"],
    "20_structure": ["10_appearance/appearance.md", "10_appearance/prompts.json"],
    "30_hardware": ["20_structure/structure.md", "20_structure/consistency.md"],
    "40_firmware": ["30_hardware/hardware.md", "30_hardware/netlist.json", "30_hardware/bom.csv"],
    "90_review": ["40_firmware/firmware.ino", "30_hardware/netlist.json", "20_structure/structure.md"],
}

# 串行链：只有一个入口、一条主链路，任何「并行委派」都得先确认两段之间没有依赖
CHAIN: list[str] = [s["id"] for s in STAGES]

_COMPILED = [(s["id"], s["label"], re.compile(s["match"], re.I)) for s in STAGES]


def _norm(paths: Iterable[str]) -> list[str]:
    return [str(p).replace("\\", "/") for p in paths]


def stages_done(paths: Iterable[str]) -> dict[str, bool]:
    normed = _norm(paths)
    return {sid: any(rx.search(p) for p in normed) for sid, _label, rx in _COMPILED}


def current_stage(done: dict[str, bool]) -> str | None:
    for sid, _label, _rx in _COMPILED:
        if not done.get(sid):
            return sid
    return None


def stage_label(sid: str) -> str:
    for i, label, _rx in _COMPILED:
        if i == sid:
            return label
    return sid


def stage_progress(paths: Iterable[str]) -> dict[str, Any]:
    done = stages_done(paths)
    n = sum(1 for sid, _l, _r in _COMPILED if done.get(sid))
    cur = current_stage(done)
    return {
        "done": n,
        "total": len(_COMPILED),
        "finished": n == len(_COMPILED),
        "current": cur or "",
        "label": stage_label(cur) if cur else "全部完成",
        "stages": [{"id": sid, "label": lab, "done": bool(done.get(sid))} for sid, lab, _r in _COMPILED],
    }


def meta(sid: str) -> dict[str, Any]:
    for s in STAGES:
        if s["id"] == sid:
            return s
    return {}


def deliverables_of(sid: str) -> list[dict[str, str]]:
    return list(meta(sid).get("deliverables") or [])


def depends_of(sid: str) -> list[str]:
    return list(meta(sid).get("depends") or [])


def gate_ready(sid: str, paths: Iterable[str]) -> tuple[bool, list[str]]:
    """开工门禁：上游要求的产物是否已落盘。返回 (是否放行, 缺失的约束描述)。"""
    normed = _norm(paths)
    missing: list[str] = []
    for pat in meta(sid).get("requires") or []:
        rx = re.compile(pat, re.I)
        if not any(rx.search(p) for p in normed):
            missing.append(pat)
    return (not missing), missing


def scan_dir(root: Path) -> list[str]:
    """扫出 run 目录下所有产物相对路径（AGENTS.md 是记忆文件，不算交付物）。

    坏图片（被文本覆盖成 `placeholder`、截断、0 字节）**不算产物**：它们会让
    「10_appearance 有 render-01.png」这种纯路径判据通过，外观阶段被误判成完成，
    结构阶段拿着假图往下建模。判据的权威是磁盘，但前提是磁盘上那份东西真能用。

    下划线开头的文件（`_human_review.json` 这类元数据）不算交付物：
    它们是平台自己写的台账，混进产物清单会让用户以为这也是设计文件。
    """
    import assets

    try:
        rels = [
            p.relative_to(root).as_posix()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "AGENTS.md" and not p.name.startswith("_")
        ]
    except OSError:
        return []
    try:
        bad = {rel for rel, _why in assets.bad_images(root)}
    except Exception:  # noqa: BLE001  校验失败不影响主判据
        bad = set()
    return [r for r in rels if r not in bad]


def progress_of_dir(root: Path) -> dict[str, Any]:
    return stage_progress(scan_dir(root))


__all__ = [
    "STAGES", "CHAIN", "GATE_INPUTS", "stages_done", "current_stage", "stage_label",
    "stage_progress", "scan_dir", "progress_of_dir", "meta", "deliverables_of",
    "depends_of", "gate_ready",
]
