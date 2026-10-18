"""结构设计内核：跑 build123d 建模脚本 → 导出 STEP / STL → 生成等轴测预览 SVG。

CAD 运行时按优先级探测（text-to-cad 官方用的是 cadgen，其环境自带 build123d）：
  1. 当前 venv 里的 build123d（快，无网络依赖）
  2. `uvx --from cadgen==0.7.20 python`（text-to-cad 官方运行时，首次会下载）

脚本在**子进程**里执行：超时可控、崩溃不影响 worker，导出结果用 JSON 从 stdout 回传。

模型写的脚本契约（纯 build123d，不依赖 cadgen）：
    from build123d import *
    shell = Box(80, 80, 30) - ...
    parts = {"底壳": shell, "上盖": lid}   # 字典；也接受列表或单个 result
"""
from __future__ import annotations

import io
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

CADGEN_SPEC = "cadgen==0.7.20"
UVX_PREFIX = [
    "uvx",
    "--no-config",
    "--managed-python",
    "--python",
    "3.13",
    "--from",
    CADGEN_SPEC,
    "python",
]
TIMEOUT = 420
# cadgen 走 uvx，首次解析环境要联网下载；探测时限给足。真正的建模走已固化的
# 解释器（毫秒级），不受这个上限影响。
CADGEN_RESOLVE_TIMEOUT = 300
MAX_PREVIEW_TRIS = 6000
_MARKER = "__CAD_RESULT__"
# 子进程空输出崩溃（rc≠0 且 stdout/stderr 全空）时的原地重试次数。
# 这种崩溃大概率是瞬时环境故障（启动即死），同脚本重发一次常能自愈 ——
# 2026-10-14 实测：链路里连「只有一个 Box」的脚本都连续空输出崩，单独跑却正常。
EMPTY_OUTPUT_RETRY = 1

# ------------------------------------------------------------------------
# build123d 0.13 已实测可用的最小 API 子集。模型最容易写错的几处都在这里点名，
# 脚本执行失败时随报错一起回传给模型，重试命中率显著提升。
# ------------------------------------------------------------------------
API_GUIDE = """【build123d 0.13 正确写法（已实测）】
from build123d import *

W, D, H = 100.0, 80.0, 40.0     # 单位 mm，XY 平面 + Z 向上
WALL, R, BASE_H = 2.0, 8.0, 30.0

with BuildPart() as shell:                       # 必须用 Builder 上下文
    Box(W, D, BASE_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(shell.edges().filter_by(Axis.Z), radius=R)
    # 掏内腔：显式减去一个内缩实体（底面/四周各留 WALL，顶面开口）
    with Locations((0, 0, WALL)):
        Box(W - 2*WALL, D - 2*WALL, BASE_H - WALL,
            align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    # 开窗 / 开口同样用 Locations + Box(SUBTRACT)
    with Locations((0, 12, BASE_H - WALL/2)):
        Box(34, 18, WALL, mode=Mode.SUBTRACT)

with BuildPart() as pcb:
    Box(70, 55, 1.6, align=(Align.CENTER, Align.CENTER, Align.MIN))

parts = {"下壳": shell.part, "主控板": pcb.part.moved(Location((0, 0, 6)))}

【尺寸自查（可选，平台本来就会回传每个零件的 bbox 与体积）】
bb = shell.part.bounding_box()           # 返回 BoundBox
print(bb.size.X, bb.size.Y, bb.size.Z)   # 三向尺寸 mm；等价写法 bb.max.X - bb.min.X
print(shell.part.volume / 1000)          # 体积 cm³

【高频错误对照】
- 错：bbox.x_length / .y_length / .z_length → 对：bbox.size.X / .size.Y / .size.Z
- 错：bb.center（当属性用）                → 对：bb.center() 是方法，要加括号
- 错：Box(...).fillet(radius=2)      → 对：fillet(builder.edges().filter_by(Axis.Z), radius=R)
- 错：Workplanes(...)                → 对：该 API 已移除，用 with Locations(...) + Box(..., mode=Mode.SUBTRACT)
- 错：offset(amount=-WALL)           → 对：offset 只生成内缩实体，不会掏空；改用上面的内腔减法
- 错：part.export_stl(...) / show()  → 对：导出由平台负责，脚本里不要写
- 错：Cylinder(r=5, h=3)             → 对：Cylinder(radius=5, height=3)
- 错：把零件直接赋给变量却不放进 parts → 对：必须 parts = {"名字": 零件}
"""


# ------------------------------------------------------------------------
# 静态检查：不用启动子进程就能抓到的写法错误。
# 以前这些写法要等脚本真跑一遍才报错，而返回值里 traceback 常被模板盖住，
# 模型看到的是同一段「参考写法」，于是原样重试三次 —— 这是"一直失败"的直接原因。
# ------------------------------------------------------------------------
import re as _re

_STATIC_RULES: list[tuple[str, str]] = [
    (r"\bWorkplanes\s*\(",
     "Workplanes 在 0.13 已移除 → 改成 with Locations((x, y, z)): Box(..., mode=Mode.SUBTRACT)"),
    (r"\bCylinder\s*\([^)]*\b(?:r|h)\s*=",
     "Cylinder 的参数名是 radius= / height=，不是 r= / h="),
    (r"\bCone\s*\([^)]*\b(?:r|h)\s*=",
     "Cone 用 bottom_radius= / top_radius= / height="),
    (r"\.fillet\s*\(\s*radius\s*=",
     "fillet 第一个参数必须是边列表 → fillet(builder.edges().filter_by(Axis.Z), radius=R)"),
    (r"\.chamfer\s*\(\s*length\s*=",
     "chamfer 第一个参数必须是边列表 → chamfer(builder.edges().filter_by(Axis.Z), length=L)"),
    (r"\boffset\s*\([^)]*amount\s*=\s*-",
     "offset(amount=-x) 只生成内缩实体、不会掏空 → 用 Locations + Box(..., mode=Mode.SUBTRACT) 减内腔"),
    (r"\.filter_by\s*\([^)]*\bfilter\s*=",
     "filter_by() 没有 filter= 参数 → 顶面边用 .filter_by(Axis.Z).sort_by(Axis.Z)[-1:]，底面用 [:1]"),
    (r"\b(?:export_stl|export_step|export_3mf)\s*\(",
     "导出由平台负责，脚本里不要写 export_*（写了会覆盖平台逻辑或直接报错）"),
    (r"\bshow\s*\(", "show() 在无界面子进程里会挂到超时 → 删掉"),
    (r"\b(?:import\s+Part\b|from\s+build123d\s+import\s+[^\n]*\bPart\b)",
     "0.13 没有顶层 Part → 用 BuildPart() 上下文里的 builder.part"),
    (r"\bAxis\s*\.\s*(?:X|Y|Z)\s*\)\s*\)?\s*\.\s*fillet",
     "链式 fillet 写法不对 → fillet(builder.edges().filter_by(Axis.Z), radius=R)"),
    (r"[\u4e00-\u9fff][，。（）]",
     "代码里混进了中文标点 → 说明文字可以写中文，但括号/逗号必须是英文半角"),
]

# 这几条只看**代码部分**：注释与字符串字面量里的中文标点完全合法
# （`# 掏内腔（放 PCB）`、`{"下壳（含内腔）": part}` 都没问题）。
# 实测旧版把注释里的中文括号也判成错误，模型只好删掉全部中文注释重写脚本 ——
# 白烧一轮，交出来的脚本还更简陋。
_CODE_ONLY_RULES = {r"[\u4e00-\u9fff][，。（）]"}
_STR_LITERAL = _re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')


def _code_part(line: str) -> str:
    """去掉注释与字符串字面量，只留会真正被 Python 解析的代码。"""
    code = line.split("#", 1)[0]
    return _STR_LITERAL.sub('""', code)


def static_check(script: str) -> list[str]:
    """返回命中的写法问题（带行号与原文摘录），没有则空列表。"""
    hits: list[str] = []
    for i, line in enumerate((script or "").splitlines(), 1):
        for pat, msg in _STATIC_RULES:
            target = _code_part(line) if pat in _CODE_ONLY_RULES else line
            if _re.search(pat, target):
                hits.append(f"  第 {i} 行 `{line.strip()[:80]}`\n    → {msg}")
                break
    # 有建模动作却没把结果交给平台
    if (script or "").strip() and _re.search(r"\bBuildPart\s*\(", script):
        if not _re.search(r"^\s*(parts|result)\s*=", script, _re.M):
            hits.append(
                "  脚本里有 BuildPart 但没有 `parts = {...}`\n"
                "    → 必须把零件放进 parts 字典，否则平台拿不到东西导出"
            )
    return hits


# ------------------------------------------------------------------------
# 自动改写：0.13 里 100% 不存在、且有唯一等价写法的属性，平台直接改掉。
# 这类错误（bbox.x_length 等）每次都要让模型重写整段脚本 —— 一轮建模几十秒到几分钟，
# 白烧。能确定地改就别让它重试。
# ------------------------------------------------------------------------
_BBOX_ATTRS = {
    "x_length": "size.X", "y_length": "size.Y", "z_length": "size.Z",
    "xlen": "size.X", "ylen": "size.Y", "zlen": "size.Z",
    "length_x": "size.X", "length_y": "size.Y", "length_z": "size.Z",
}
_BBOX_ATTR_RE = _re.compile(r"\.(" + "|".join(sorted(_BBOX_ATTRS, key=len, reverse=True)) + r")\b")

# Cylinder(r=5, h=3) 是模型最常写的错参名，0.13 只认 radius= / height=。
# 同样确定，同样直接改掉。
_CYL_RE = _re.compile(r"\bCylinder\s*\(([^)]*)\)")

# CadQuery 风格的链式 `.fillet(...)` / `.chamfer(...)`：网上教程（含官方生态文章）
# 大多是 CadQuery 写法（`edges().fillet(2)`），模型从训练数据搬来套在 build123d 上 ——
# 但 0.13 的 ShapeList 没有这两个方法，正确写法是函数式 `fillet(edges, radius)`。
# 静态检查拦住后模型重写还犯（2026-10-14 实测：同一行被拦三次）。
# 链式 → 函数式是机械变换，平台直接改。
# 保守边界：含「=」的行（可能是赋值/默认参数）和多行链不动，留给静态检查兜底。
_CHAIN_OP_RE = _re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<target>[^=\n]*?\.(?:edges|faces|vertices)\(\)[^=\n]*?)"
    r"\.(?P<op>fillet|chamfer)\((?P<args>.*)\)(?P<tail>[ \t]*(?:#.*)?)$",
    _re.MULTILINE,
)


def _fix_cylinder(m: _re.Match) -> str:
    args = m.group(1)
    new = _re.sub(r"(?<![\w.])r\s*=", "radius=", args)
    new = _re.sub(r"(?<![\w.])h\s*=", "height=", new)
    return f"Cylinder({new})"


def normalize_script(script: str) -> tuple[str, list[str]]:
    """把已确认改名/移除的写法自动改写成 0.13 的等价写法。

    只改**确定性**的那几处（BoundBox 的 *_length、Cylinder 的 r=/h=）：
    每让模型重写一轮脚本就是几十秒到几分钟，能平台改就别浪费。
    返回 (改写后的脚本, 说明列表)；没有命中就原样返回。
    """
    if not script:
        return script, []
    notes: list[str] = []

    hits: dict[str, int] = {}

    def _sub(m: _re.Match) -> str:
        key = m.group(1)
        hits[key] = hits.get(key, 0) + 1
        return "." + _BBOX_ATTRS[key]

    out = _BBOX_ATTR_RE.sub(_sub, script)
    if hits:
        detail = "、".join(f".{k}→.{_BBOX_ATTRS[k]}（{n} 处）" for k, n in hits.items())
        notes.append(
            f"平台已自动改写包围盒属性：{detail}。"
            "0.13 的 BoundBox 只有 min / max / size(Vector)，没有 *_length / *len"
        )

    cyl_hits = 0

    def _sub_cyl(m: _re.Match) -> str:
        nonlocal cyl_hits
        fixed = _fix_cylinder(m)
        if fixed != m.group(0):
            cyl_hits += 1
        return fixed

    out = _CYL_RE.sub(_sub_cyl, out)
    if cyl_hits:
        notes.append(
            f"平台已自动改写 {cyl_hits} 处 Cylinder 参数名：r= → radius=、h= → height="
        )

    chain_hits: list[str] = []

    def _sub_chain(m: _re.Match) -> str:
        op = m.group("op")
        chain_hits.append(op)
        return f"{m.group('indent')}{op}({m.group('target').strip()}, {m.group('args').strip()}){m.group('tail')}"

    out = _CHAIN_OP_RE.sub(_sub_chain, out)
    if chain_hits:
        notes.append(
            f"平台已自动改写 {len(chain_hits)} 处 CadQuery 风格链式调用："
            f"{('、'.join(sorted(set(chain_hits))))} —— build123d 0.13 是函数式 "
            f"fillet(edges, radius)，ShapeList 没有 .fillet()/.chamfer() 方法"
        )
    return out, notes


# 子进程崩溃（OCCT 内部 abort）时的典型退出码：进程直接死，stderr 一个字都没有。
_CRASH_CODES = {
    -11: "SIGSEGV（段错误）",
    -6: "SIGABRT（主动中止）",
    -1073741819: "0xC0000005 访问违规",
    -1073740791: "0xC0000409 栈缓冲溢出/快速失败",
    -1073741571: "0xC00000FD 栈溢出",
    -1073740940: "0xC0000374 堆损坏",
    -1073741521: "0xC00000EF 控件不存在（多为无界面环境调用 GUI）",
}


def crash_note(rc: int, kind: str) -> str:
    """子进程非零退出却没有任何输出时的诊断文案。

    注意归因顺序：空输出**首先**是瞬时环境故障（子进程启动即死：内存压力、
    杀软拦截、OCP DLL 加载竞争），其次才可能是 OCP 几何内核在建模中途崩。
    实测（2026-10-14）连「只有一个 Box」的最简脚本都空输出崩过，而同脚本
    单独跑完全正常 —— 把锅全甩给几何会误导模型去改一个没病的脚本。
    build() 已对空输出原地重试并做环境自检，走到这里说明重试也崩了。
    """
    why = _CRASH_CODES.get(int(rc))
    head = f"子进程退出码 {rc}"
    if why:
        head += f"（{why}）"
    return (
        f"{head}，且 stdout/stderr 全空（已自动重试仍如此）。\n"
        "先看下面的【环境自检】结果：\n"
        "  · 自检失败 → 是运行时环境坏了，不是脚本问题：不要改脚本，等几十秒原样重发即可\n"
        "  · 自检通过 → 才是 OCP 几何内核在建模时崩了，常见三个几何诱因（按出现频率）：\n"
        "    1. 圆角/倒角半径 ≥ 该方向边长的一半 → 把 R 压到 min(W, D)/2 - 0.5 以下\n"
        "    2. 减法后壁厚为 0 或负（内腔尺寸 ≥ 外形尺寸）→ 各向留 ≥ 1mm 实壁\n"
        "    3. 相减的两个实体只相切不重叠 → 让内腔盒至少比外壳少 2 倍壁厚，并往里挪 WALL\n"
        "  对齐办法：先用最简脚本（只有一个 Box + parts）验证通路，再逐段加特征。"
    )


def _env_selfcheck(argv: list[str], kind: str) -> str:
    """空输出崩溃后的环境自检：子进程 import build123d 能不能起来。

    用来区分「环境坏（重发同脚本即可）」还是「脚本把内核带崩（要改脚本）」。
    """
    if kind == "e2b":
        # 沙箱是独立环境，本机自检说明不了任何事；沙箱侧的可用状态由 _run_e2b 直接报
        return "云端沙箱内崩溃（独立内存，与本机环境无关）→ 按几何诱因排查，或稍后原样重发"
    try:
        r = subprocess.run(
            argv + ["-c", "import build123d;print('ENV_OK')"],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if r.returncode == 0 and "ENV_OK" in (r.stdout or ""):
            return f"环境自检通过（{kind} 运行时 import build123d 正常）→ 问题在脚本几何"
        detail = ((r.stderr or "") + (r.stdout or "")).strip()[-200:]
        return (
            f"环境自检失败（{kind} 运行时连 import build123d 都起不来，退出码 {r.returncode}"
            f"{('：' + detail) if detail else '，无输出'}）→ 环境问题，不是脚本问题"
        )
    except Exception as e:  # noqa: BLE001
        return f"环境自检无法执行（{type(e).__name__}）→ 按环境问题处理，稍后原样重发"


_ATTR_ERR_RE = _re.compile(
    r"AttributeError:\s*'([^']+)'\s*object\s*has\s*no\s*attribute\s*'([^']+)'"
)

# 这几个类在 0.13 里真实存在的属性（实测 introspect 得到），
# 报错时直接告诉模型「能用什么」，比让它猜快得多。
_CLASS_ATTRS = {
    "BoundBox": "min(Vector) / max(Vector) / size(Vector，取 .X .Y .Z) / diagonal / "
                "center() / add() / contains()",
    "Vector": "X / Y / Z / length / normalized() / dot() / cross()",
    "Edge": "length / radius / center() / tangent_at() / tangent_angle_at() / start_point / "
            "end_point / geom_type / normal（没有 .tangent，要切线用 tangent_at()）",
    "Shape": "volume / area / bounding_box() / center() / faces() / edges() / vertices() / moved()",
    "Solid": "volume / bounding_box() / center() / faces() / edges() / moved()",
    "BuildPart": "part / edges() / faces() / vertices() / add() / location",
}


def attr_hint(stderr: str) -> str:
    """把 AttributeError 落成「哪个类没有哪个属性 → 该用什么」的精确建议。"""
    m = _ATTR_ERR_RE.search(stderr or "")
    if not m:
        return ""
    cls, attr = m.group(1), m.group(2)
    if attr in _BBOX_ATTRS:
        axis = _BBOX_ATTRS[attr].split(".")[-1]
        return (
            f"BoundBox 没有 .{attr} → 用 bbox.{_BBOX_ATTRS[attr]}"
            f"（等价写法 bbox.max.{axis} - bbox.min.{axis}）"
        )
    known = _CLASS_ATTRS.get(cls)
    if known:
        return f"{cls} 没有 .{attr} 属性（0.13 已改名或移除）；该类可用的是：{known}"
    return f"{cls} 没有 .{attr} 属性：0.13 里已改名或移除，对照速查表换成存在的写法"


def hint_for(stderr: str) -> str:
    """按报错特征给出针对性修正建议；没有命中就回整份速查表。

    注意措辞：模板必须明确标注为「参考写法」，否则模型会把它当成错误本身，
    于是原样重发同一段脚本 —— 历史会话里出现过连续三次同样的"模板错误"。
    """
    table = [
        ("missing 1 required positional argument: 'edge_list'",
         "fillet/chamfer 需要边列表：fillet(part.edges().filter_by(Axis.Z), radius=R)"),
        ("name 'Workplanes' is not defined",
         "0.13 已移除 Workplanes，改用 with Locations((x,y,z)): Box(..., mode=Mode.SUBTRACT)"),
        ("name 'Part' is not defined", "不要 import Part，直接用 BuildPart 上下文里的 builder.part"),
        ("unexpected keyword argument 'r'", "Cylinder 用 radius= / height=，不是 r= / h="),
        ("unexpected keyword argument 'h'", "Cylinder 用 radius= / height=，不是 r= / h="),
        ("invalid syntax", "语法错误：检查括号配对与中文标点"),
        ("There are no suitable edges for chamfer or fillet",
         "圆角/倒角没选到边：圆柱/圆锥没有平行于 Z 的直边，filter_by(Axis.Z) 必定选空 → "
         "回转体要选上下沿圆边，用 filter_by(GeomType.CIRCLE).sort_by(Axis.Z)[-1:]（上沿）/[:1]（下沿）；"
         "选到边仍失败才是半径 ≥ 该方向边长的一半，把 R 调小或用 max_fillet()"),
        ("unexpected keyword argument 'filter'",
         "filter_by() 没有 filter= 参数：选顶面边用 .filter_by(Axis.Z).sort_by(Axis.Z)[-1:]，底面用 [:1]"),
        ("Standard_Failure", "OCP 内核报错：几何非法（圆角过大 / 壁厚为负 / 自相交），先按最简 Box 验证再逐段加特征"),
        ("BRep_API", "OCP 布尔运算失败：两个实体没真正相交，或相减后成空壳 → 检查 Locations 坐标与尺寸"),
        ("not a solid", "结果不是闭合实体（可能是曲面或空壳）→ 减法后要剩下正体积"),
        ("No active", "操作不在 BuildPart 上下文里 → 把建模语句放进 `with BuildPart() as p:` 块内"),
        ("cannot be converted to a Shape", "传给布尔运算的不是 Shape → 用 builder.part 而不是 builder 对象"),
        ("is not defined", "名字未定义：确认只用了 `from build123d import *` 暴露的符号"),
        ("AttributeError", "属性不存在：该 API 在 0.13 里改名或移除，照下面的参考写法改"),
    ]
    hits = [msg for key, msg in table if key in stderr]
    # 精确的属性级建议排最前：泛泛的「属性不存在」对模型基本没有行动价值
    ah = attr_hint(stderr)
    if ah:
        hits.insert(0, ah)
    if hits:
        return "修正建议：" + "；".join(hits[:3])
    return "未匹配到已知错误模式。下面是已实测可用的参考写法（**这是示例，不是你的报错**）：\n" + API_GUIDE[:1200]


# ------------------------------------------------------------------------
# 参数化兜底：脚本反复写不对时，平台按尺寸直接建一个壳体出来。
# 结构阶段是硬交付 —— 交不出 STEP/STL，后面硬件板型、固件仿真全都悬着。
# ------------------------------------------------------------------------
SHELL_TEMPLATE = '''from build123d import *

W, D, H = {W}, {D}, {H}
WALL, R = {WALL}, {R}

with BuildPart() as shell:
    Box(W, D, H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(shell.edges().filter_by(Axis.Z), radius=R)
    with Locations((0, 0, WALL)):
        Box(W - 2*WALL, D - 2*WALL, H - WALL,
            align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
{holes}
parts = {{"shell": shell.part}}
'''


def shell_script(W: float, D: float, H: float, wall: float = 2.0, r: float | None = None,
                 holes: list[tuple[float, float, float, float]] | None = None) -> str:
    """生成参数化壳体脚本。holes 每项 = (x, y, w, d)，在顶面开矩形孔。"""
    W, D, H = float(W), float(D), float(H)
    wall = max(0.8, min(float(wall), min(W, D) / 2 - 0.5))
    if r is None:
        r = min(8.0, min(W, D) / 2 - 0.5)
    r = max(0.2, min(float(r), min(W, D) / 2 - 0.5))
    body = ""
    for x, y, hw, hd in holes or []:
        body += (
            f"    with Locations(({x}, {y}, H - WALL/2)):\n"
            f"        Box({hw}, {hd}, WALL, mode=Mode.SUBTRACT)\n"
        )
    return SHELL_TEMPLATE.format(
        W=_num(W), D=_num(D), H=_num(H), WALL=_num(wall), R=_num(r), holes=body
    )


def _num(v: float) -> str:
    return f"{float(v):.2f}"


def build_shell(W: float, D: float, H: float, out_dir: Path | str, base_name: str = "model",
                wall: float = 2.0, r: float | None = None,
                holes: list[tuple[float, float, float, float]] | None = None) -> dict[str, Any]:
    """按尺寸参数直接建模（跳过模型手写脚本）。兜底路径，保证结构阶段一定有产出。"""
    return build(shell_script(W, D, H, wall=wall, r=r, holes=holes), out_dir, base_name=base_name)


_NUM_RE = r"-?\d+(?:\.\d+)?"
_ASSIGN_RE = _re.compile(
    rf"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=\s*"
    rf"({_NUM_RE}(?:\s*,\s*{_NUM_RE})*)\s*(?:#.*)?$",
    _re.M,
)
_DIM_ALIASES = {
    # 圆柱类产品常写 DIAMETER/TOTAL_H，不认这两个就解析不出尺寸、兜底模板也建不出来
    "W": ("W", "WIDTH", "L", "LENGTH", "X", "DIAMETER", "DIA", "OUTER_DIA"),
    "D": ("D", "DEPTH", "B", "W2", "Y", "DIAMETER", "DIA", "OUTER_DIA"),
    "H": ("H", "HEIGHT", "T", "THICK", "Z", "TOTAL_H", "TOTAL_HEIGHT"),
}


def parse_dims(script: str) -> dict[str, float]:
    """从脚本里解析尺寸常量（支持 `W, D, H = 40, 20, 150` 这种批量赋值）。

    拿不到三个主尺寸就返回空字典 —— 此时不做兜底，免得凭空造一个尺寸不对的壳。
    """
    consts: dict[str, float] = {}
    for m in _ASSIGN_RE.finditer(script or ""):
        names = [n.strip() for n in m.group(1).split(",")]
        vals = [float(v) for v in _re.findall(_NUM_RE, m.group(2))]
        if len(names) != len(vals):
            continue
        for n, v in zip(names, vals):
            consts[n.upper()] = v
    out: dict[str, float] = {}
    for key, aliases in _DIM_ALIASES.items():
        for a in aliases:
            if a in consts and consts[a] > 0:
                out[key] = consts[a]
                break
    if len(out) == 3:
        out["WALL"] = consts.get("WALL", 2.0)
        out["R"] = consts.get("R", 0.0) or 0.0
    return out


# ------------------------------------------------------------------- 运行时探测
def _uvx_exe() -> str:
    """uvx 可执行文件路径（Windows 上常装在 ~/.local/bin，不一定在 PATH）。"""
    import shutil

    exe = shutil.which("uvx") or shutil.which("uvx.exe")
    if exe:
        return exe
    for cand in (
        Path.home() / ".local" / "bin" / "uvx",
        Path.home() / ".local" / "bin" / "uvx.exe",
        Path.home() / "AppData" / "Roaming" / "Python" / "Scripts" / "uvx.exe",
    ):
        if cand.exists():
            return str(cand)
    return ""


def _uvx_argv() -> list[str]:
    """走 uvx 的 cadgen 前缀。慢（每次 ~21s）且并发会撞 uv 缓存锁，只用于首次解析。"""
    return [_uvx_exe() or "uvx"] + UVX_PREFIX[1:]


# uvx 的两个坑：① 每次调用都要重新解析环境（实测 ~21s）；② 同一环境的并发调用会撞
# uv 的缓存锁（表现为 returncode≠0 且 stdout/stderr 全空 —— 前端几秒一次 /health 就会撞上）。
# 所以只让 uvx 跑一次，把它环境里的 python 绝对路径问出来，之后直接调那个解释器：
# 单次开销 21s → 毫秒级，并发锁冲突也一并消失。
_CADGEN_LOCK = threading.Lock()
_cadgen_py: list[str] | None = None
_cadgen_state: bool | None = None
_cadgen_state_ts = 0.0
STATE_TTL = 300.0  # 失败结论只信 5 分钟，之后允许重新探测


def _resolve_cadgen_python(force: bool = False) -> list[str]:
    """解析出 cadgen 环境里的 python 解释器并固化。失败返回 []。"""
    global _cadgen_py
    if _cadgen_py is not None and not force:
        return _cadgen_py
    if not _uvx_exe():
        _cadgen_py = []
        return _cadgen_py
    with _CADGEN_LOCK:
        try:
            r = subprocess.run(
                _uvx_argv() + ["-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=CADGEN_RESOLVE_TIMEOUT,
            )
        except Exception:  # noqa: BLE001  下载失败/网络不通/超时都当不可用
            _cadgen_py = []
            return _cadgen_py
        exe = ""
        if r.returncode == 0:
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if line and Path(line).exists() and Path(line).name.lower().startswith("python"):
                    exe = line
        _cadgen_py = [exe] if exe else []
    return _cadgen_py


def _cadgen_argv() -> list[str]:
    """cadgen 运行时 argv：优先用已固化的解释器；还没解析过就退回 uvx（只慢这一次）。"""
    py = _resolve_cadgen_python()
    if py and Path(py[0]).exists():
        return list(py)
    return _uvx_argv()


def _cadgen_usable(force: bool = False) -> bool:
    """cadgen 运行时是否真能跑。

    成功结论永久缓存（环境不会自己变好又变坏）；失败结论只缓存 STATE_TTL ——
    否则一次 uvx 抖动就会让 CAD 能力在整个进程生命周期里判死。
    """
    global _cadgen_state, _cadgen_state_ts
    now = time.time()
    if not force and _cadgen_state is not None:
        if _cadgen_state or (now - _cadgen_state_ts) < STATE_TTL:
            return _cadgen_state
    if not _uvx_exe():
        _cadgen_state, _cadgen_state_ts = False, now
        return False

    ok = False
    for _i in range(2):  # 并发撞锁是偶发的，补一次重试再定性
        argv = _cadgen_argv()
        with _CADGEN_LOCK:
            try:
                r = subprocess.run(
                    argv + ["-c", "import build123d; print('OK')"],
                    capture_output=True,
                    text=True,
                    timeout=CADGEN_RESOLVE_TIMEOUT,
                )
                ok = r.returncode == 0 and "OK" in (r.stdout or "")
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            break
        # 固化路径失效（uv 缓存被清）就重新解析一次，别拿旧路径反复撞
        if argv != _uvx_argv():
            _resolve_cadgen_python(force=True)
    _cadgen_state, _cadgen_state_ts = ok, now
    return ok


def detect_runner() -> tuple[str, list[str]]:
    """返回 (kind, argv)。

    auto 默认**本地 venv 优先**，理由是实测出来的两条：
      · cadgen==0.7.20 硬锁 build123d>=0.11.1,<0.12，而建模模板与 API 速查表按 0.13 写，
        版本天然对不上，白送一批失败重试；
      · 本地调用毫秒级，cadgen 走 uvx 每次要重新解析环境（~21s），并发还会撞缓存锁。
    本地没装 build123d 时才走 cadgen。想强制用 text-to-cad 官方运行时：AGENT_CAD_RUNTIME=cadgen。
    """
    import config

    mode = (getattr(config, "CAD_RUNTIME", "auto") or "auto").strip().lower()
    if mode == "cadgen":
        return ("cadgen", _cadgen_argv())
    if mode == "local":
        return ("local", [sys.executable])
    if mode == "e2b":
        return ("e2b", [])
    # auto：云端沙箱优先 —— 本机内存扛不住并发 OCP（实测 8 并发 7 崩），
    # 沙箱有独立内存且更快。沙箱不可用再按 本地 → cadgen 回落。
    if _e2b_ready():
        return ("e2b", [])
    if _local_argv():
        return ("local", [sys.executable])
    if _cadgen_usable():
        return ("cadgen", _cadgen_argv())
    return ("local", [sys.executable])


_probe_cache: dict[str, tuple[bool, str, float]] = {}
PROBE_TTL = 120.0


def _probe(argv: list[str], kind: str) -> tuple[bool, str]:
    """探测某个运行时是否可用。

    成功结论永久缓存；失败结论只缓存 PROBE_TTL —— 一次 uvx 抖动不该让能力永久判死，
    但也不能让 /health 每几秒就重跑一次 20 秒的探测。
    """
    if kind == "e2b":
        # 不为了一次探活就去创建沙箱（冷启 10s+）：有 key 且 SDK 在即视为可用，
        # 真跑不通时 build() 会自动回落本地，不会让建模卡死。
        if _e2b_ready():
            _probe_cache[kind] = (True, "e2b:云端沙箱", time.time())
            return True, "e2b:云端沙箱"
        info = "e2b 不可用（缺 E2B_API_KEY 或未装 e2b-code-interpreter）"
        _probe_cache[kind] = (False, info, time.time())
        return False, info

    hit = _probe_cache.get(kind)
    if hit is not None:
        ok, info, ts = hit
        if ok or (time.time() - ts) < PROBE_TTL:
            return ok, info
    probe = (
        "import build123d,sys;print('OK', getattr(build123d,'__version__','?'), sys.version.split()[0])"
    )
    lock = _CADGEN_LOCK if kind == "cadgen" else threading.Lock()
    with lock:
        try:
            r = subprocess.run(
                argv + ["-c", probe],
                capture_output=True,
                text=True,
                timeout=CADGEN_RESOLVE_TIMEOUT if kind == "cadgen" else 60,
            )
        except FileNotFoundError:
            return False, "找不到 python/uvx，无法运行 CAD 脚本"
        except subprocess.TimeoutExpired:
            return False, f"{kind} 运行时探测超时（首次使用 cadgen 需要下载，请稍后重试）"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode == 0 and out.startswith("OK"):
        ok, info = True, f"{kind}:{out[3:].strip()}"
    else:
        detail = (err or out)[-200:]
        if not detail:
            # stdout/stderr 全空 + 非零退出 = uvx 并发撞缓存锁的典型表现，
            # 光把空字符串回给用户只会让人以为环境彻底坏了。
            detail = "子进程无输出（uvx 并发撞缓存锁的典型表现），正回落到备用运行时"
        ok, info = False, f"{kind} 运行时不可用：{detail}"
    _probe_cache[kind] = (ok, info, time.time())
    return ok, info


# ---------------------------------------------------------------- E2B 云端沙箱
# 为什么需要它：本机 8GB 内存，并发建模时 OpenBLAS 分配失败会把子进程直接打死
# （退出码 1 且 stdout/stderr 全空，连 import build123d 都过不去）。沙箱有独立内存，
# 实测冷启 22s（含 pip install build123d）、建模 2.5s，比本地还快且不占本机资源。
E2B_REMOTE = "/tmp/cad"
_E2B_LOCK = threading.RLock()
_E2B_SBX: Any = None
_E2B_BAD_UNTIL = 0.0
E2B_PENALTY = 120.0
_E2B_PULL_EXTS = (".step", ".stp", ".stl", ".3mf", ".svg", ".png")


def _e2b_ready() -> bool:
    """能不能走云端沙箱：有 key、SDK 装了、且不在失败惩罚期。"""
    import config

    if not getattr(config, "E2B_API_KEY", ""):
        return False
    if time.time() < _E2B_BAD_UNTIL:
        return False
    try:
        import e2b_code_interpreter  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _e2b_penalize() -> None:
    """沙箱侧连续不可用时限流，别让每次建模都去撞一次创建沙箱。"""
    global _E2B_BAD_UNTIL
    _E2B_BAD_UNTIL = time.time() + E2B_PENALTY


def _e2b_sandbox() -> Any:
    """取一个装好 build123d 的沙箱：能复用就复用（省掉重复的 pip install）。"""
    global _E2B_SBX
    import config

    with _E2B_LOCK:
        sbx = _E2B_SBX
        if sbx is not None:
            try:
                r = sbx.commands.run("python -c \"import build123d;print('OK')\"", timeout=60)
                if r.exit_code == 0 and "OK" in (r.stdout or ""):
                    return sbx
            except Exception:  # noqa: BLE001
                pass
            _E2B_SBX = None
        try:
            from e2b_code_interpreter import Sandbox
        except Exception as e:  # noqa: BLE001
            _e2b_penalize()
            raise RuntimeError(f"E2B SDK 不可用（{type(e).__name__}）") from e

        kw: dict[str, Any] = {"timeout": config.E2B_TIMEOUT}
        if getattr(config, "E2B_TEMPLATE", ""):
            kw["template"] = config.E2B_TEMPLATE
        try:
            sbx = Sandbox.create(**kw)
        except Exception as e:  # noqa: BLE001
            _e2b_penalize()
            raise RuntimeError(f"创建沙箱失败（{type(e).__name__}: {e}）") from e
        if not getattr(config, "E2B_TEMPLATE", ""):
            r = sbx.commands.run("pip install --quiet build123d 2>&1 | tail -3", timeout=300)
            if r.exit_code != 0:
                try:
                    sbx.kill()
                except Exception:  # noqa: BLE001
                    pass
                _e2b_penalize()
                raise RuntimeError("沙箱内安装 build123d 失败：" + (r.stdout or "")[-300:])
        _E2B_SBX = sbx
        return sbx


def _e2b_pull(sbx: Any, remote: str, out_dir: Path) -> list[str]:
    """把沙箱导出的产物取回本地工作区。预览图由本地按 STL 合成，不必回传。"""
    got: list[str] = []
    try:
        entries = sbx.files.list(remote)
    except Exception:  # noqa: BLE001
        return got
    for e in entries:
        name = getattr(e, "name", None) or str(e).rsplit("/", 1)[-1]
        if not name or name == "model.py" or not name.lower().endswith(_E2B_PULL_EXTS):
            continue
        try:
            data = sbx.files.read(f"{remote}/{name}", format="bytes")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, (bytes, bytearray)):
            data = str(data).encode("utf-8")
        if not data:
            continue
        try:
            (out_dir / name).write_bytes(data)
            got.append(name)
        except Exception:  # noqa: BLE001
            continue
    return got


def _run_e2b(script: str, out_dir: Path, base_name: str) -> dict[str, Any]:
    """在云端沙箱里跑建模脚本，产物取回本地。返回与本地同构的 rec。"""
    import config

    sbx = _e2b_sandbox()
    # 沙箱是 Linux，路径绝不能过 Path() —— Windows 下 str(Path("/tmp/cad")) 会变成
    # "\\tmp\\cad"，沙箱会把它当成一个文件名，产物全写进怪目录、一个都取不回来。
    full, _ = _build_script(script, E2B_REMOTE, base_name or "model")
    sbx.commands.run(f"rm -rf {E2B_REMOTE} && mkdir -p {E2B_REMOTE}", timeout=60)
    sbx.files.write(f"{E2B_REMOTE}/model.py", full)
    try:
        r = sbx.commands.run(
            f"cd {E2B_REMOTE} && python model.py", timeout=config.E2B_RUN_TIMEOUT
        )
        rc, out, err = r.exit_code, r.stdout or "", r.stderr or ""
    except Exception as e:  # noqa: BLE001
        # E2B 在命令非零退出时**抛异常**而不是返回 rc。脚本报错必须照样当结果带回，
        # 否则会被当成「沙箱不可用」回落本地，把真报错绕成另一套说辞。
        rc = int(getattr(e, "exit_code", 1) or 1)
        out = getattr(e, "stdout", "") or ""
        err = getattr(e, "stderr", "") or getattr(e, "message", "") or ""
        if not (out or err):
            raise  # 拿不到任何输出 = 真·沙箱故障，交给上层回落本地
        err = err or str(e)
    rec: dict[str, Any] = {
        "kind": "e2b",
        "rc": rc,
        "out": out,
        "err": err,
        "argv": [],
    }
    if rc == 0 and _MARKER in rec["out"]:
        rec["pulled"] = _e2b_pull(sbx, E2B_REMOTE, out_dir)
    return rec


def _fallback_runner(kind: str) -> tuple[str, list[str]] | None:
    """备用运行时：e2b → 本地 venv；cadgen ↔ 本地 venv。"""
    if kind == "e2b":
        alt = _local_argv()
        if alt:
            return ("local", alt)
        if _cadgen_argv()[0] and _cadgen_usable():
            return ("cadgen", _cadgen_argv())
        return None
    if kind == "cadgen":
        alt = _local_argv()
        return ("local", alt) if alt else None
    if _cadgen_argv()[0] and _cadgen_usable():
        return ("cadgen", _cadgen_argv())
    return None


def _local_argv() -> list[str] | None:
    try:
        import build123d  # noqa: F401

        return [sys.executable]
    except Exception:  # noqa: BLE001
        return None


def _fallback_chain(kind: str) -> list[tuple[str, list[str]]]:
    """主运行时之外的候选，按代价从低到高。"""
    out: list[tuple[str, list[str]]] = []
    if kind != "local":
        alt = _local_argv()
        if alt:
            out.append(("local", alt))
    if kind != "cadgen" and _uvx_exe() and _cadgen_argv()[0] and _cadgen_usable():
        out.append(("cadgen", _cadgen_argv()))
    return out


def available() -> tuple[bool, str]:
    """探测可用运行时。**主运行时失败就试备用的** —— 建模是硬交付，
    不能因为 cadgen 那一次抖动（uvx 并发撞锁、临时断网）就整体不可用。
    """
    kind, argv = detect_runner()
    ok, info = _probe(argv, kind)
    if ok:
        return True, info
    for alt_kind, alt_argv in _fallback_chain(kind):
        ok2, info2 = _probe(alt_argv, alt_kind)
        if ok2:
            return True, info2 + f"（{kind} 不可用，已回落 {alt_kind}）"
    return False, info + "；可执行 pip install build123d 装到本地 venv，或配 E2B_API_KEY 走云端沙箱"


def status() -> tuple[bool, str]:
    """/health 专用：不为了回答一次健康检查就发起昂贵的 uvx 解析。

    云端沙箱就绪或本地装了 build123d 就毫秒级返回；都没有且 cadgen 还没探测过时后台
    起线程预热，本次如实说「正在探测」，而不是把健康检查卡住十几秒。
    """
    # 按实际选中的运行时报，否则 AGENT_CAD_RUNTIME=local 时 /health 会谎报沙箱
    kind, _ = detect_runner()
    if kind == "e2b":
        return _probe([], "e2b")
    if _local_argv():
        return _probe([sys.executable], "local")
    if _cadgen_state is not None:
        return available()
    if not _uvx_exe():
        return False, "未安装 build123d，且找不到 uvx（无法启用 cadgen 运行时）"
    threading.Thread(target=_cadgen_usable, daemon=True).start()
    return False, "本地未安装 build123d，正在后台探测 cadgen 运行时（首次需下载，请稍后刷新）"


# ------------------------------------------------------------ 注入到脚本的导出段
_EXPORT_TAIL = r'''
# ---- 以下由平台注入：收集零件、导出、回传结果 ----
import json as _json, sys as _sys
from pathlib import Path as _Path

_OUT = _Path(r"{out_dir}")
_OUT.mkdir(parents=True, exist_ok=True)

def _collect(ns):
    parts = {}
    raw = ns.get("parts")
    if isinstance(raw, dict):
        parts = {str(k): v for k, v in raw.items() if v is not None}
    elif isinstance(raw, (list, tuple)):
        parts = {"part" + str(i): v for i, v in enumerate(raw, 1) if v is not None}
    elif raw is not None:
        parts = {"part1": raw}
    if not parts and ns.get("result") is not None:
        parts = {"part1": ns["result"]}
    if not parts:
        for k, v in ns.items():
            if k.startswith("_") or k in ("parts", "result"):
                continue
            if hasattr(v, "bounding_box") and hasattr(v, "volume"):
                parts[k] = v
    return parts

def _export(obj, path):
    """build123d 0.13 把导出挪到了顶层函数；老版本是 Part.export_xxx 方法。两条路都试。"""
    ext = path.suffix.lower()
    p = str(path)

    def done():
        return path.exists() and path.stat().st_size > 0

    # 1) 顶层函数（0.13+）：export_stl / export_step / export_3mf
    try:
        import build123d as _bd
        fn = {".stl": "export_stl", ".step": "export_step", ".stp": "export_step",
              ".3mf": "export_3mf"}.get(ext)
        if fn and hasattr(_bd, fn):
            getattr(_bd, fn)(obj, p)
            if done():
                return True
    except Exception:
        pass

    # 2) 方法形式（<=0.9）
    try:
        m = {".stl": "export_stl", ".step": "export_step", ".stp": "export_step",
             ".3mf": "export_3mf"}.get(ext)
        if m and hasattr(obj, m):
            getattr(obj, m)(p)
            if done():
                return True
    except Exception:
        pass

    # 3) Mesher 兜底（网格类格式）——注意 add_shape 返回 None，必须分两步写
    try:
        if ext in (".stl", ".3mf"):
            from build123d import Mesher
            mesher = Mesher()
            mesher.add_shape(obj)
            mesher.write(p)
            if done():
                return True
    except Exception:
        pass
    return False

def _slug(name, idx):
    """只保留 ASCII 字母数字：OCCT 在 Windows 上按本地代码页写文件，中文名会生成乱码副本。"""
    s = "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "" for c in str(name))
    s = s.strip("-_")[:32]
    return s or ("p" + str(idx))

_parts = _collect(dict(globals()))
_files, _report = [], []
for _i, (_name, _obj) in enumerate(_parts.items(), 1):
    _stem = "{base}_" + _slug(_name, _i)
    _ok = False
    _dims = [0.0, 0.0, 0.0]
    try:
        _bb = _obj.bounding_box()
        _dims = [round(float(_bb.max.X - _bb.min.X), 2),
                 round(float(_bb.max.Y - _bb.min.Y), 2),
                 round(float(_bb.max.Z - _bb.min.Z), 2)]
    except Exception:
        pass
    try:
        _vol = round(float(_obj.volume) / 1000.0, 3)
    except Exception:
        _vol = 0.0
    for _ext in (".step", ".stl"):
        _d = _OUT / (_stem + _ext)
        if _export(_obj, _d):
            _files.append(_d.name)
            _ok = True
    _report.append({"name": str(_name), "stem": _stem, "size_mm": _dims,
                    "volume_cm3": _vol, "exported": _ok})

print("{marker}" + _json.dumps(
    {"ok": any(r["exported"] for r in _report), "files": _files, "parts": _report,
     "count": len(_parts)}, ensure_ascii=False))
'''


# build123d 不导出 math 里的东西，而模型写圆柱阵列/圆角时几乎必用 radians / pi / cos。
# 与其让它为一两个名字重写整段脚本，不如平台按需补一行导入。
_MATH_IMPORT = (
    "from math import radians, degrees, sin, cos, tan, asin, acos, atan, atan2, "
    "sqrt, pi, ceil, floor, hypot  # 平台自动补齐：build123d 不导出这些\n"
)
_MATH_NAMES = ("radians", "degrees", "sin", "cos", "tan", "asin", "acos", "atan",
               "atan2", "sqrt", "ceil", "floor", "hypot")
_MATH_USE_RE = _re.compile(r"\b(" + "|".join(_MATH_NAMES) + r")\s*\(")


def _needs_math(script: str) -> bool:
    """脚本用到了 math 里的名字、却没自己 import math。"""
    if _re.search(r"^\s*(from\s+math\s+import|import\s+math)\b", script or "", _re.M):
        return False
    if _MATH_USE_RE.search(script or ""):
        return True
    return bool(_re.search(r"\bpi\b", script or ""))


def _build_script(user_script: str, out_dir: Path, base_name: str) -> tuple[str, int]:
    """拼出实际执行的脚本，返回 (脚本, 用户脚本的行号偏移)。

    偏移必须回传给报错处理：平台在前面加了导入行后，traceback 里的行号会比模型
    写的脚本多 offset 行 —— 不减回去，模型照行号改就会改错地方。
    """
    tail = (
        _EXPORT_TAIL.replace("{out_dir}", str(out_dir))
        .replace("{base}", base_name)
        .replace("{marker}", _MARKER)
    )
    if _needs_math(user_script):
        return f"{_MATH_IMPORT}{user_script}\n\n{tail}", 1
    return f"{user_script}\n\n{tail}", 0


_LINENO_RE = _re.compile(r'(File "[^"]*\.py", line )(\d+)(, in <module>)')


def _rebase_lineno(text: str, offset: int) -> str:
    """把 traceback 里的行号减掉平台前置行，还原成模型脚本里的真实行号。"""
    if offset <= 0 or not text:
        return text
    return _LINENO_RE.sub(
        lambda m: f"{m.group(1)}{max(1, int(m.group(2)) - offset)}{m.group(3)}", text
    )


# ------------------------------------------------------------------ STL → 预览 SVG
def _read_stl_tris(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    try:
        raw = path.read_bytes()
    except Exception:  # noqa: BLE001
        return []
    tris: list[tuple[tuple[float, float, float], ...]] = []
    try:
        if raw[:5] == b"solid":  # ASCII STL
            cur: list[float] = []
            for line in raw.decode("utf-8", "replace").splitlines():
                s = line.strip()
                if s.startswith("vertex "):
                    cur += [float(x) for x in s.split()[1:4]]
                    if len(cur) == 9:
                        tris.append(
                            ((cur[0], cur[1], cur[2]), (cur[3], cur[4], cur[5]), (cur[6], cur[7], cur[8]))
                        )
                        cur = []
            return tris
        if len(raw) < 84:
            return []
        count = struct.unpack("<I", raw[80:84])[0]
        off = 84
        for _ in range(min(count, MAX_PREVIEW_TRIS)):
            if off + 50 > len(raw):
                break
            vals = struct.unpack("<12f", raw[off : off + 48])
            tris.append(
                ((vals[3], vals[4], vals[5]), (vals[6], vals[7], vals[8]), (vals[9], vals[10], vals[11]))
            )
            off += 50
    except Exception:  # noqa: BLE001
        return tris
    return tris


def _isometric(p: tuple[float, float, float]) -> tuple[float, float]:
    x, y, z = p
    a = math.radians(45.0)
    x1 = x * math.cos(a) - y * math.sin(a)
    y1 = x * math.sin(a) + y * math.cos(a)
    b = math.radians(35.264)
    return x1, -(y1 * math.cos(b) - z * math.sin(b))


def tris_to_svg(
    tris: list[tuple[tuple[float, float, float], ...]], w: int = 680, h: int = 460
) -> str:
    if not tris:
        return ""
    proj = [_isometric(p) for t in tris for p in t]
    xs = [q[0] for q in proj]
    ys = [q[1] for q in proj]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny) or 1.0
    pad = 30.0
    scale = (min(w, h) - pad * 2) / span
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

    def tx(q: tuple[float, float]) -> tuple[float, float]:
        return ((q[0] - cx) * scale + w / 2, (q[1] - cy) * scale + h / 2)

    polys: list[str] = []
    step = max(1, len(tris) // MAX_PREVIEW_TRIS)
    for i in range(0, len(tris), step):
        a, b, c = (tx(_isometric(p)) for p in tris[i])
        polys.append(
            f'<polygon points="{a[0]:.1f},{a[1]:.1f} {b[0]:.1f},{b[1]:.1f} {c[0]:.1f},{c[1]:.1f}" '
            f'fill="rgba(45,212,191,0.09)" stroke="#2ee6a8" stroke-width="0.45"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<rect width="{w}" height="{h}" fill="#0b1220"/>'
        + "".join(polys)
        + f'<text x="16" y="{h - 14}" fill="#5d7285" font-size="12" font-family="monospace">'
        f"等轴测预览 · {len(tris)} 面</text></svg>"
    )


# ------------------------------------------------------- 离线工程视图 / 爆炸图
# 图像服务不可达时的兜底：几何是真的（来自 STEP/STL），不是模型脑补的，
# 所以尺寸关系可靠，能直接进设计文档。
SHEET_BG = "#ffffff"
SHEET_INK = "#1f2933"
SHEET_FILL = "#dbe4ec"
SHEET_EDGE = "#425466"
SHEET_DIM = "#0f7b6c"
SHEET_TEXT = "#52606d"
# 三角面抽稀上限。实测：16982 面 × 4 视图 = 6.97 MB 的 SVG，浏览器打开都卡；
# 抽到 2600 面后视觉几乎无差别，体积降一个数量级。
MAX_SHEET_TRIS = 2600


def _decimate(tris: list, limit: int) -> list:
    """均匀采样。STL 的面是连续排布的，等距抽样不会破坏轮廓。"""
    if limit <= 0 or len(tris) <= limit:
        return tris
    step = math.ceil(len(tris) / limit)
    return tris[::step]


def _project(p: tuple[float, float, float], view: str) -> tuple[float, float]:
    x, y, z = p
    if view == "front":
        return x, -z
    if view == "side":
        return y, -z
    if view == "top":
        return x, -y
    return _isometric(p)


def _bbox(tris) -> tuple[list[float], list[float]]:
    lo = [min(p[j] for t in tris for p in t) for j in range(3)]
    hi = [max(p[j] for t in tris for p in t) for j in range(3)]
    return lo, hi


def _fit_polygons(tris, view: str, w: int, h: int, fill: str, edge: str, sw: float = 0.5) -> str:
    """把三角面按视图投影并居中缩放，返回 SVG 片段。"""
    proj = [_project(p, view) for t in tris for p in t]
    xs = [q[0] for q in proj]
    ys = [q[1] for q in proj]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny) or 1.0
    pad = 26.0
    scale = (min(w, h) - pad * 2) / span
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

    def tx(q):
        return ((q[0] - cx) * scale + w / 2, (q[1] - cy) * scale + h / 2)

    return "".join(
        f'<polygon points="{a[0]:.1f},{a[1]:.1f} {b[0]:.1f},{b[1]:.1f} {c[0]:.1f},{c[1]:.1f}" '
        f'fill="{fill}" stroke="{edge}" stroke-width="{sw}"/>'
        for t in tris
        for a, b, c in [(tx(_project(p, view)) for p in t)]
    )


def load_parts(struct_dir: Path) -> list[tuple[str, list]]:
    """读目录下每个零件的 STL。文件名排序，保证顺序稳定。"""
    out: list[tuple[str, list]] = []
    for stl in sorted(Path(struct_dir).glob("*.stl")):
        tris = _read_stl_tris(stl)
        if tris:
            out.append((stl.stem, tris))
    return out


def views_sheet(parts: list[tuple[str, list]], title: str = "结构工程视图") -> str:
    """四视图图纸：等轴测 + 主视 + 侧视 + 俯视，带外形尺寸标注。"""
    if not parts:
        return ""
    all_tris = _decimate([t for _, ts in parts for t in ts], MAX_SHEET_TRIS)
    views = [("iso", "等轴测"), ("front", "主视 X-Z"), ("side", "侧视 Y-Z"), ("top", "俯视 X-Y")]
    cw, ch, pad = 340, 300, 56
    width = pad + len(views) * cw
    height = pad + ch + 110

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        f'<rect width="{width}" height="{height}" fill="{SHEET_BG}"/>',
        f'<text x="{pad - 30}" y="30" font-size="16" font-weight="bold" fill="{SHEET_INK}" '
        f'font-family="system-ui,sans-serif">{title}</text>',
        f'<text x="{pad - 30}" y="50" font-size="11" fill="{SHEET_TEXT}" font-family="monospace">'
        f'{len(parts)} 个零件 · {len(all_tris)} 面 · 单位 mm</text>',
    ]
    lo, hi = _bbox(all_tris)
    dims = [round(hi[i] - lo[i], 2) for i in range(3)]

    for i, (view, label) in enumerate(views):
        ox, oy = pad - 30 + i * cw, 70
        svg.append(
            f'<rect x="{ox + 6}" y="{oy}" width="{cw - 20}" height="{ch}" fill="{SHEET_BG}" '
            f'stroke="{SHEET_EDGE}" stroke-width="1" opacity="0.5"/>'
        )
        svg.append(f'<g transform="translate({ox + 6},{oy})">'
                   f'{_fit_polygons(all_tris, view, cw - 20, ch, SHEET_FILL, SHEET_EDGE, 0.45)}</g>')
        svg.append(
            f'<text x="{ox + 6 + (cw - 20) / 2:.0f}" y="{oy + ch + 20}" font-size="12" '
            f'text-anchor="middle" fill="{SHEET_INK}" font-family="system-ui">{label}</text>'
        )

    dy, x0, x1 = 70 + ch + 46, pad - 20, pad + 240
    svg.append(
        f'<line x1="{x0}" y1="{dy}" x2="{x1}" y2="{dy}" stroke="{SHEET_DIM}" stroke-width="1.2"/>'
        f'<line x1="{x0}" y1="{dy - 5}" x2="{x0}" y2="{dy + 5}" stroke="{SHEET_DIM}" stroke-width="1.2"/>'
        f'<line x1="{x1}" y1="{dy - 5}" x2="{x1}" y2="{dy + 5}" stroke="{SHEET_DIM}" stroke-width="1.2"/>'
        f'<text x="{(x0 + x1) / 2:.0f}" y="{dy - 8}" font-size="12" text-anchor="middle" '
        f'fill="{SHEET_DIM}" font-family="monospace">外形 {dims[0]} × {dims[1]} × {dims[2]} mm</text>'
    )
    svg.append("</svg>")
    return "".join(svg)


def exploded_sheet(parts: list[tuple[str, list]], title: str = "装配爆炸视图") -> str:
    """爆炸视图：零件分离 + 虚线引导线 + 编号 + 尺寸图例。"""
    if not parts:
        return ""
    centers = []
    for _name, tris in parts:
        lo, hi = _bbox(tris)
        centers.append([(lo[i] + hi[i]) / 2 for i in range(3)])
    zspan = max(c[2] for c in centers) - min(c[2] for c in centers)
    xspan = max(c[0] for c in centers) - min(c[0] for c in centers)
    yspan = max(c[1] for c in centers) - min(c[1] for c in centers)
    vertical = zspan >= max(xspan, yspan) * 0.6

    all_tris = [t for _, ts in parts for t in ts]
    glo, ghi = _bbox(all_tris)
    gspan = max(ghi[i] - glo[i] for i in range(3)) or 1.0
    gap = gspan * 0.28

    order = sorted(
        range(len(parts)),
        key=lambda i: centers[i][2] if vertical else (centers[i][0] ** 2 + centers[i][1] ** 2),
    )
    moved: list[list] = [[0.0, 0.0, 0.0] for _ in parts]
    for rank, idx in enumerate(order):
        if vertical:
            moved[idx][2] = (rank - (len(order) - 1) / 2) * gap
        else:
            dx, dy = centers[idx][0], centers[idx][1]
            d = math.hypot(dx, dy) or 1.0
            k = (rank + 1) * gap * 0.8
            moved[idx][0] = dx / d * k
            moved[idx][1] = dy / d * k
            moved[idx][2] = (rank - (len(order) - 1) / 2) * gap * 0.35

    # 每个零件单独抽稀：先混合再抽样会把小零件整个吃掉，图例里就会出现空壳
    per = max(200, MAX_SHEET_TRIS // max(1, len(parts)))
    parts = [(name, _decimate(tris, per)) for name, tris in parts]
    shifted = [
        (name, [tuple((p[0] + moved[i][0], p[1] + moved[i][1], p[2] + moved[i][2]) for p in t) for t in tris])
        for i, (name, tris) in enumerate(parts)
    ]

    w, h = 760, 520
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
        f'<rect width="{w}" height="{h}" fill="{SHEET_BG}"/>',
        f'<text x="26" y="30" font-size="16" font-weight="bold" fill="{SHEET_INK}" '
        f'font-family="system-ui,sans-serif">{title}</text>',
        f'<text x="26" y="50" font-size="11" fill="{SHEET_TEXT}" font-family="monospace">'
        f'{len(parts)} 个零件 · 沿{"装配轴" if vertical else "径向"}分离 · 虚线为装配引导线</text>',
    ]

    proj_all = [_isometric(p) for _, ts in shifted for t in ts for p in t]
    pxs = [q[0] for q in proj_all]
    pys = [q[1] for q in proj_all]
    span = max(max(pxs) - min(pxs), max(pys) - min(pys)) or 1.0
    scale = (min(w - 210, h - 90)) / span
    ccx, ccy = (min(pxs) + max(pxs)) / 2, (min(pys) + max(pys)) / 2

    def tx(p):
        q = _isometric(p)
        return ((q[0] - ccx) * scale + (w - 210) / 2 + 20, (q[1] - ccy) * scale + h / 2 + 10)

    palette = ["#7fb3d5", "#a3d9c6", "#f0c987", "#d9a3c7", "#b8b8e0", "#c9e0a3", "#e8b4a0", "#9fd0d8"]
    for i, (_name, tris) in enumerate(shifted):
        color = palette[i % len(palette)]
        svg.append("".join(
            f'<polygon points="{a[0]:.1f},{a[1]:.1f} {b[0]:.1f},{b[1]:.1f} {c[0]:.1f},{c[1]:.1f}" '
            f'fill="{color}" fill-opacity="0.62" stroke="{SHEET_EDGE}" stroke-width="0.45"/>'
            for t in tris
            for a, b, c in [(tx(p) for p in t)]
        ))

    for i, (_name, tris) in enumerate(parts):
        lo, hi = _bbox(tris)
        c0 = [(lo[j] + hi[j]) / 2 for j in range(3)]
        c1 = (c0[0] + moved[i][0], c0[1] + moved[i][1], c0[2] + moved[i][2])
        a, b = tx(tuple(c0)), tx(c1)
        svg.append(
            f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
            f'stroke="{SHEET_DIM}" stroke-width="1.1" stroke-dasharray="5 4" opacity="0.8"/>'
        )
        svg.append(f'<circle cx="{b[0]:.1f}" cy="{b[1]:.1f}" r="10" fill="#fff" stroke="{SHEET_DIM}" stroke-width="1.3"/>')
        svg.append(
            f'<text x="{b[0]:.1f}" y="{b[1] + 3.6:.1f}" font-size="10.5" text-anchor="middle" '
            f'fill="{SHEET_DIM}" font-family="monospace" font-weight="bold">{i + 1}</text>'
        )

    lx = w - 196
    svg.append(f'<line x1="{lx - 14}" y1="70" x2="{lx - 14}" y2="{h - 30}" stroke="{SHEET_EDGE}" stroke-width="1" opacity="0.4"/>')
    for i, (name, tris) in enumerate(parts):
        ly = 78 + i * 28
        lo, hi = _bbox(tris)
        dims = [round(hi[j] - lo[j], 1) for j in range(3)]
        pretty = name.replace("_", " ").strip()[:20]
        svg.append(f'<rect x="{lx}" y="{ly - 10}" width="12" height="12" fill="{palette[i % len(palette)]}" stroke="{SHEET_EDGE}" stroke-width="0.8"/>')
        svg.append(f'<text x="{lx + 18}" y="{ly}" font-size="11" fill="{SHEET_INK}" font-family="monospace">{i + 1}. {pretty}</text>')
        svg.append(f'<text x="{lx + 18}" y="{ly + 13}" font-size="9.5" fill="{SHEET_TEXT}" font-family="monospace">{dims[0]}×{dims[1]}×{dims[2]} mm</text>')
    svg.append("</svg>")
    return "".join(svg)


# ------------------------------------------------------------------------ 主入口
_LOCAL_LOCK = threading.RLock()
_CAD_ENV_BLOCK = ("PYTHONPATH", "PYTHONHOME")


def _child_env() -> dict[str, str]:
    """本地子进程的环境：剔除宿主注入的 PYTHONPATH shim，并把 BLAS 压成单线程。

    两条都是实测出来的：
      · PYTHONPATH 指向宿主 shim 时，3.13 子进程会间歇性报
        _PyImport_InitExternal: external importer setup failed；
      · OpenBLAS 每线程各开一份缓冲区，并发建模直接把 8GB 内存打爆
        （8 路并发 7 崩，退出码 1 且无输出）。建模不是算密集，单线程不亏。
    """
    env = {k: v for k, v in os.environ.items() if k.upper() not in _CAD_ENV_BLOCK}
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _dump_failed(script: str, out_dir: Path, base_name: str) -> str:
    """失败脚本留档：模型/人都得能拿到「上次到底提交了什么」才能改。

    返回工作区相对路径（如 20_structure/_cad_debug/model_failed.py），
    与其余产物路径同风格，子智能体能直接 read_file 回来改。
    """
    try:
        dbg = out_dir / "_cad_debug"
        dbg.mkdir(parents=True, exist_ok=True)
        p = dbg / f"{base_name or 'model'}_failed.py"
        p.write_text(script or "", encoding="utf-8")
        try:
            return p.relative_to(out_dir.parent).as_posix()
        except ValueError:
            return str(p)
    except OSError:
        return ""


def build(script: str, out_dir: Path | str, base_name: str = "model") -> dict[str, Any]:
    """执行建模脚本并导出。返回 {ok, files, parts, error, hint, runner, script_path}。

    两条铁律（都是踩出来的）：
      · 真实报错永远排在返回内容的最前面，参考写法只能当附录 —— 顺序反了模型会把
        示例当成错误，然后原样重发同一段脚本。
      · 只要主运行时给出了明确报错，就不再换另一个版本的运行时重试。cadgen 锁的是
        build123d 0.11，与 0.13 的模板天然对不上，第二次只会用另一套错误信息盖掉真因。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kind, argv = detect_runner()

    # 0) 确定性的改名/移除属性先自动改写 —— 能改就别让模型花一轮重写整段脚本
    script, notes = normalize_script(script)

    # 1) 不用跑就能抓到的写法错误，直接拦下（省一次启动，也不受运行时版本影响）
    static = static_check(script)
    if static:
        return {
            "ok": False,
            "error": "脚本未通过静态检查（不用执行就能确定的写法问题）：\n" + "\n".join(static[:8]),
            "hint": "改掉上面每一行后重试；其余部分照抄下面的参考写法。\n" + API_GUIDE[:900],
            "files": [],
            "runner": kind,
            "script_path": _dump_failed(script, out_dir, base_name),
            "static": True,
        }

    full, lineno_off = _build_script(script, out_dir, base_name or "model")
    fd, tmp = tempfile.mkstemp(suffix=".py", prefix="cad_")
    runs: list[dict[str, Any]] = []
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(full)
        stop_all = False
        for attempt in ((kind, argv), _fallback_runner(kind)):
            if attempt is None or stop_all:
                break
            a_kind, a_argv = attempt
            # e2b 走沙箱，不占本机内存；本地/ cadgen 必须串行 —— 并发 OCP 会把内存打爆
            lock = _CADGEN_LOCK if a_kind == "cadgen" else _LOCAL_LOCK
            # 空输出（rc≠0 且 stdout/stderr 全空）= 子进程启动即死，大概率是瞬时环境
            # 故障（内存压力 / 杀软 / OCP DLL 加载竞争），原地重试一次大概率自愈 ——
            # 实测同一段脚本单跑正常、链路里却连续空输出崩溃。有明确报错则绝不重试。
            empty_tries = 0
            while True:
                if a_kind == "e2b":
                    try:
                        rec = _run_e2b(script, out_dir, base_name or "model")
                    except Exception as e:  # noqa: BLE001
                        # 沙箱侧故障（创建/装包失败、网络不通）才回落本地；
                        # 脚本自身的报错会带 traceback 回来，走下面的正常分支
                        runs.append(
                            {"kind": "e2b", "rc": -1, "out": "", "argv": [],
                             "err": f"[沙箱不可用] {type(e).__name__}: {e}"}
                        )
                        break
                    runs.append(rec)
                    if rec["rc"] == 0 and _MARKER in rec["out"]:
                        stop_all = True
                        break
                    if rec["err"].strip() or rec["out"].strip():
                        stop_all = True  # 沙箱给了明确报错 = 脚本问题，换运行时只会换套说辞
                        break
                    break  # 空输出：换本地再试一次
                try:
                    with lock:
                        proc = subprocess.run(
                            a_argv + [tmp],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=TIMEOUT,
                            cwd=str(out_dir),
                            env=_child_env(),
                        )
                except subprocess.TimeoutExpired:
                    return {"ok": False, "error": f"建模超时（>{TIMEOUT}s），脚本可能存在死循环或几何过复杂。",
                            "files": [], "runner": a_kind, "script_path": _dump_failed(script, out_dir, base_name)}
                except FileNotFoundError:
                    # 解释器没了才算运行时不可用，这时才换备用
                    break
                rec = {
                    "kind": a_kind,
                    "rc": proc.returncode,
                    "out": proc.stdout or "",
                    "err": proc.stderr or "",
                    "argv": a_argv,
                }
                runs.append(rec)
                if proc.returncode == 0 and _MARKER in rec["out"]:
                    stop_all = True
                    break
                # 给了明确报错 → 不再重试也不换运行时（版本不同只会给出另一套错误信息）
                if rec["err"].strip() or rec["out"].strip():
                    stop_all = True
                    break
                # 完全没输出 = 崩溃或运行时抖动：先原地重试，再换备用运行时
                empty_tries += 1
                if empty_tries <= EMPTY_OUTPUT_RETRY:
                    continue
                break
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    if not runs:
        return {"ok": False, "error": "找不到可用的 CAD 运行时（python / uvx 均不可用）。", "files": [],
                "runner": kind, "script_path": _dump_failed(script, out_dir, base_name)}

    # 证据最足的那次说了算：traceback 越长越有信息量，空输出的运行不能盖掉它。
    # rc=-1 是「沙箱压根没起来」的占位记录，它没有脚本信息，不能盖掉真实的脚本报错。
    real = [r for r in runs if r["rc"] != -1] or runs
    best = max(real, key=lambda r: len(r["err"].strip()) * 2 + len(r["out"].strip()))
    kind = best["kind"]
    stdout, stderr, rc = best["out"], best["err"], best["rc"]

    payload: dict[str, Any] | None = None
    for line in stdout.splitlines():
        if line.startswith(_MARKER):
            try:
                payload = json.loads(line[len(_MARKER) :])
            except json.JSONDecodeError:
                pass

    if payload is None:
        # 脚本崩了：真实报错 → 崩溃诊断 → 修正建议，顺序不能反
        tail = _rebase_lineno((stderr or stdout).strip()[-1800:], lineno_off)
        if not tail:
            tail = crash_note(rc, kind)
            # 空输出才自检：区分「环境坏（原样重发）」还是「脚本把内核带崩（要改脚本）」
            tail += "\n\n【环境自检】\n" + _env_selfcheck(best.get("argv") or argv, kind)
        err = f"脚本执行失败（运行时 {kind}，退出码 {rc}）：\n{tail}"
        if lineno_off:
            err += f"\n（行号已按你写的脚本还原：平台额外注入了 {lineno_off} 行导入）"
        if len(runs) > 1:
            err += "\n\n【其它运行时的结果，供对照】\n" + "\n".join(
                f"  · {r['kind']}（退出码 {r['rc']}）：{(r['err'] or r['out']).strip()[:300] or '无输出'}"
                for r in runs
                if r is not best
            )
        return {
            "ok": False,
            "error": err,
            "hint": hint_for(stderr or stdout),
            "files": [],
            "runner": kind,
            "rc": rc,
            "notes": notes,
            "script_path": _dump_failed(script, out_dir, base_name),
        }

    if payload.get("count", 0) == 0:
        return {
            "ok": False,
            "error": "脚本执行成功，但没找到可导出的零件。请把结果赋给 parts（字典或列表）或 result。",
            "files": [],
            "runner": kind,
        }
    if not payload.get("ok"):
        return {
            "ok": False,
            "error": "零件已生成但导出失败（几何可能不是闭合实体）。请改用正的闭合体积实体再试。",
            "files": payload.get("files", []),
            "parts": payload.get("parts", []),
            "runner": kind,
        }

    files = [str(f) for f in payload.get("files", [])]
    # 预览：把所有 STL 的三角面合成一张等轴测图
    tris: list[tuple[tuple[float, float, float], ...]] = []
    for f in files:
        if f.lower().endswith(".stl"):
            tris += _read_stl_tris(out_dir / f)
    if tris:
        svg = tris_to_svg(tris[:MAX_PREVIEW_TRIS])
        pv = out_dir / f"{base_name}_preview.svg"
        pv.write_text(svg, encoding="utf-8")
        files.append(pv.name)

    return {
        "ok": True,
        "files": files,
        "parts": payload.get("parts", []),
        "runner": kind,
        "notes": notes,
        # 回传**实际执行**的脚本（可能已被 normalize 改写）：平台存盘的 model.py
        # 必须与跑通的这份一致，否则模型下次拿它当起点会再踩同一个坑。
        "script": script,
        "error": "",
    }
