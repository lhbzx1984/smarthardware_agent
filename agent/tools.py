"""智能体可用的领域工具：知识检索、电路规则校验、BOM、Arduino 静态检查、产物落盘。

工具参数全部扁平（字符串），兼容硅基流动上各模型的 function calling 实现。
"""
from __future__ import annotations

import csv
import importlib
import json
import re
import sys
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from langchain_core.tools import tool

import config
import guards
import pathutil
from knowledge import KB

# ---- 运行上下文：当前 run 的目录、事件回调、前端运行时模型设置 ----
_run_dir: ContextVar[Path | None] = ContextVar("run_dir", default=None)
_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("sink", default=None)
_settings: ContextVar[dict[str, Any]] = ContextVar("settings", default={})
# 运行对象（server.Run）：人工确认闸门要挂起整条流、暂停运行时钟，光有目录不够
_run_obj: ContextVar[Any] = ContextVar("run_obj", default=None)


# ---- 延迟导入统一入口 -------------------------------------------------------
# 工具是在线程池里并发跑的，而 CPython 的 import 锁在「线程 A 导包根、线程 B 直接导子模块」
# 并发时会互相等成环：_DeadlockError: deadlock detected by _ModuleLock('eda.xxx')。
# 所以：① 所有延迟导入走 _mod()，首次导入串行化；② 启动时单线程预热，运行期不再首次导入。
_IMPORT_LOCK = threading.Lock()
_EDA_PKG: str | None = None


def _mod(name: str, attr: str | None = None):
    """导入模块（或取它的一个属性）。已在 sys.modules 就直接返回，不进锁。"""
    mod = sys.modules.get(name)
    if mod is None:
        with _IMPORT_LOCK:
            mod = importlib.import_module(name)
    return mod if attr is None else getattr(mod, attr)


def _eda_pkg() -> str:
    """eda 包的名字随启动方式变（worker 里是顶层 eda，脚本里是 agent.eda）。

    只解析一次并固定下来 —— 同一个文件被当成两个模块各导一遍，是后面一切混乱的源头。
    """
    global _EDA_PKG
    if _EDA_PKG is None:
        try:
            _EDA_PKG = _mod("eda").__name__
        except ImportError:
            _EDA_PKG = _mod("agent.eda").__name__
    return _EDA_PKG


def _eda(name: str, attr: str | None = None):
    """按固定包名取 eda 的子模块：_eda("kicad", "write") → kicad.write。"""
    return _mod(f"{_eda_pkg()}.{name}", attr)


def _warm_imports() -> None:
    """启动时单线程把工具会用到的 eda 子模块全导入好。

    失败不阻塞启动 —— 运行期还有 _mod() 的锁兜底，不会退化成以前的死锁。
    """
    try:
        pkg = _eda_pkg()
        for m in ("board", "netlist", "kicad", "pcb", "easyeda", "preview", "gerber"):
            _mod(f"{pkg}.{m}")
    except Exception:  # noqa: BLE001
        pass


_warm_imports()


def bind_context(
    run_dir: Path,
    sink: Callable[[dict], None] | None,
    settings: dict[str, Any] | None = None,
    run_obj: Any = None,
) -> None:
    _run_dir.set(run_dir)
    _sink.set(sink)
    _settings.set(settings or {})
    _run_obj.set(run_obj)


def _run() -> Any:
    return _run_obj.get()


def _setting(key: str, default: str = "") -> str:
    return str(_settings.get().get(key) or default)


def emit(kind: str, **payload: Any) -> None:
    fn = _sink.get()
    if fn:
        try:
            fn({"type": kind, **payload})
        except guards.RunAborted:
            # 取消/超时/超预算必须往外传，否则工具层会把中止信号吃掉
            raise
        except Exception:  # noqa: BLE001  事件推送失败不能中断智能体
            pass


# stage_gate 结果缓存：{(阶段ID, 产物快照): (时刻, 结果文本)}。
# 见 stage_gate 里的说明 —— key 里带产物快照，产物一变缓存自动失效。
_GATE_CACHE: dict[str, tuple[float, str]] = {}


def _run_path() -> Path:
    d = _run_dir.get()
    if d is None:
        d = config.WORKSPACE / "adhoc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel_of(path: str) -> str:
    """把模型给的任意形态路径归一成工作区内的相对路径（供落盘与事件记账共用）。

    模型会把宿主机绝对路径原样塞回来（多半是从上游报错信息里抄的），
    不归一化的话前端产物树里会出现 `C:/Users/...` 这种条目。
    """
    return pathutil.strip_home_user(pathutil.normalize(path, _run_path())).lstrip("/")


def _safe_target(rel: str) -> tuple[Path | None, str]:
    """把相对路径解析成沙箱内的绝对路径；越界返回 (None, 原因)。

    用 Path.parents 判断而不是字符串 startswith：后者会把 `/workspace-evil`
    当成 `/workspace` 的子路径放过（前缀相同的兄弟目录）。
    """
    base = _run_path()
    rel = pathutil.strip_home_user(pathutil.normalize(rel, base))
    if not rel or rel.startswith("~"):
        return None, "拒绝：路径为空或使用了不允许的形式。"
    if ".." in rel.split("/"):
        return None, "拒绝：路径越界，不允许使用 .. 。"
    if pathutil.is_foreign(rel):
        return None, (
            "拒绝：路径不在本次工作区内。请写工作区内的相对路径，"
            "例如 10_appearance/render-01.png。"
        )
    # 去掉前导 `/`：模型写 `/etc/passwd` 这种"看着像绝对路径"的虚拟路径时，
    # `Path(base) / "/etc/passwd"` 会被当成真绝对路径直接覆盖 base（Windows 上尤其明显），
    # 于是本该关进沙箱的路径反被判越界。先 lstrip 再拼，才是"关进笼子"。
    rel = rel.lstrip("/")
    try:
        target = (base / rel).resolve()
    except (ValueError, OSError) as e:
        return None, f"拒绝：路径非法（{e}）。"
    if target != base.resolve() and base.resolve() not in target.parents:
        return None, "拒绝：路径越界，必须写在当前项目工作区内。"
    # 返回未 resolve 的路径：Windows 上 resolve() 会加 `\\?\` 前缀，
    # 后面 relative_to(_run_path()) 与前端展示都会因此失配。
    return base / rel, ""


def _clip(text: str, limit: int = config.MAX_TOOL_RESULT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n...[已截断]"


def _stage_lock(tool: str, sid: str, files: list[str] | None = None) -> str:
    """昂贵工具开工前的幂等闸：本阶段已确认/已落盘时拒绝重做。

    线上事故：外观图出了两遍、PRD 写了两遍、同一阶段被请用户确认三次。
    提示词里叮嘱「不要重复做」挡不住上下文漂移，所以在工具层落闸。
    """
    import stagestate

    try:
        return stagestate.guard(_run_path(), sid, tool, files)
    except Exception:  # noqa: BLE001  台账读写出错不该挡住正常建模
        return ""


def _stage_done(sid: str) -> None:
    """产物落盘后把阶段置为 done（已确认的不动）。"""
    import stagestate

    try:
        stagestate.mark_done(_run_path(), sid)
    except Exception:  # noqa: BLE001
        pass


def _sync_stage(rel: str) -> None:
    """文档落盘后刷新阶段台账：有产物 → working，契约产物齐 → done。

    没有这一步的话，PRD 阶段（没有人工闸门）永远不会变成 done，
    于是没有任何地方记录「这一步做过了」，续跑时又被从头写一遍。
    """
    import stages as _st
    import stagestate

    sid = rel.split("/")[0]
    meta = next((s for s in _st.STAGES if s["id"] == sid), None)
    if not meta:
        return
    root = _run_path()
    try:
        need = [d["file"] for d in (meta.get("deliverables") or [])]
        if not need:
            return
        have = [f for f in need if (root / f).is_file()]
        if not have:
            return
        stagestate.mark_started(root, sid, "save_artifact")
        # 契约产物齐了才算 done —— 只写了一部分就置 done，会让 stage_gate
        # 误报「本阶段已完成」，剩下的文档就再也没人补了
        if len(have) >= len(need):
            stagestate.mark_done(root, sid)
    except Exception:  # noqa: BLE001  台账出错不影响落盘
        pass


def _dup_write(rel: str, content: str) -> str:
    """同一路径重复写入几乎相同的内容 → 拒绝。

    实测：一个子智能体连着三次 save_artifact 写 `00_prd/backlog.md`，
    字符数 456 / 455 / 456 —— 内容实质一样，纯粹在烧工具预算。六阶段跑下来
    预算就是这么被吃光的（那次 80 次用尽，只做到 5/6 阶段）。

    只在「已存在 + 高度相似」时拦：第一次写、或者真有实质改动，一律放行。
    """
    import difflib

    base = _run_path()
    target = base / rel.lstrip("/")
    try:
        if not target.is_file():
            return ""
        old = target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if not old:
        return ""
    if old == content:
        return (
            f"⛔ {rel} 已存在且内容完全一致，不需要再写一遍。"
            "直接把它当作已落盘的产物，继续做下一步。"
        )
    if abs(len(old) - len(content)) > max(200, len(old) * 0.25):
        return ""
    ratio = difflib.SequenceMatcher(None, old[:4000], content[:4000]).ratio()
    if ratio >= 0.95:
        return (
            f"⛔ {rel} 已存在，且本次内容与已落盘版本相似度 {ratio:.0%}（几乎一致）。"
            "重复落盘既不产生新东西又消耗工具预算，已跳过。"
            "如果确实要改，请写入**有实质差异**的内容（补充章节、修正数据），"
            "否则直接沿用现有文件继续下一步。"
        )
    return ""


PROMPT_STORE = "10_appearance/prompts.json"


def _appearance_records() -> list[dict[str, Any]]:
    """读回已存档的出图记录（含文件路径），供「后续图基于前一张」挑基线。

    模型有时会自己改写 prompts.json（结构不同），两种格式都认，能捞多少算多少。
    """
    import json

    dest, err = _safe_target(PROMPT_STORE)
    if dest is None or not dest.exists():
        return []
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    items: list[dict[str, Any]] = []
    for it in data.get("items") or []:
        if isinstance(it, dict):
            items.append(it)
    for it in data.get("prompts") or []:
        if isinstance(it, dict):
            items.append(it)
        elif isinstance(it, str):
            items.append({"prompt": it})
    return items


def _existing_image(rel: str) -> Path | None:
    """记录里的相对路径是否真的存在且有内容。"""
    if not rel:
        return None
    p, err = _safe_target(rel)
    if p is None or not p.exists() or p.stat().st_size == 0:
        return None
    return p


def _pick_reference(kind: str) -> tuple[Path | None, str]:
    """为该 kind 挑一张参考图，保证同一产品的多张图不会各画各的。

    规则：
    - 渲染图（含第二张外观图）：以**上一张渲染图**为基线，只改视角/场景
    - 爆炸图：以**第一张渲染图**（产品基线）为基线，保证拆的是同一个产品
    - 渲染图还没有 → 返回空，第一张走文生图
    """
    recs = _appearance_records()
    renders: list[str] = []
    for r in recs:
        if str(r.get("kind") or "").lower() in ("render", "appearance", ""):
            f = str(r.get("file") or r.get("path") or "")
            if f and _existing_image(f) is not None:
                renders.append(f)
    if not renders:
        return None, ""
    # 爆炸图认第一张（产品基线）；渲染图认最近一张（逐步演进）
    rel = renders[0] if kind == "exploded" else renders[-1]
    return _existing_image(rel), rel


def _resolve_reference(spec: str, kind: str) -> tuple[Path | None, str, str]:
    """解析 reference 参数。返回 (绝对路径, 相对路径, 说明)。

    spec："" / "auto" = 自动挑基线；"none"/"off" = 强制文生图；其余按路径处理。
    """
    s = (spec or "").strip()
    if s.lower() in ("none", "off", "no", "false"):
        return None, "", "已按指定关闭参考图（文生图）"
    if s and s.lower() not in ("auto", "auto", "default"):
        p, err = _safe_target(s)
        if p is None:
            return None, "", f"指定的参考图不可用（{err}），改为文生图"
        if not p.exists() or p.stat().st_size == 0:
            return None, "", f"指定的参考图不存在或为空：{s}，改为文生图"
        return p, s.replace("\\", "/").lstrip("/"), f"按指定参考图 {s} 生成"
    p, rel = _pick_reference(kind)
    if p is None:
        return None, "", "当前没有可作为基线的产品图（第一张走文生图）"
    return p, rel, f"基于已有产品图 {rel} 生成（图生图，保持同一产品）"


def _auto_filename(kind: str) -> str:
    """按已存在的序号顺延命名：render-01 → render-02 … 不覆盖已有图。"""
    base_dir = "20_structure" if kind == "exploded" else "10_appearance"
    prefix = "exploded" if kind == "exploded" else "render"
    d, _err = _safe_target(base_dir + "/")
    nums: list[int] = []
    if d is not None and d.exists():
        for p in d.glob(f"{prefix}-*.png"):
            m = re.search(r"-(\d+)\.png$", p.name)
            if m:
                nums.append(int(m.group(1)))
    return f"{base_dir}/{prefix}-{max(nums) + 1 if nums else 1:02d}.png"


def _in_stage_dir(name: str, kind: str) -> str:
    """模型指定 filename 时常只给裸文件名（render-02.png），直接落盘会掉到工作区根，
    前端分组就归到「其它文件」，下游也引用不到。

    没带目录就按用途归位：渲染图进 10_appearance，爆炸图进 20_structure。
    """
    n = (name or "").replace("\\", "/").strip().lstrip("/")
    if "/" in n:
        return n
    return ("20_structure/" if kind == "exploded" else "10_appearance/") + n


def _dedupe_rel(rel: str) -> str:
    """目标已存在就顺延编号。

    覆盖已有产品图会直接毁掉一致性链 —— 参考图被新图顶掉后，
    后续所有图都会相对一个已经不存在的基线生成。
    """
    p, err = _safe_target(rel)
    if p is None or not p.exists():
        return rel
    stem, dot, ext = rel.rpartition(".")
    if not dot:
        stem, ext = rel, "png"
    for i in range(2, 100):
        cand = f"{stem}-{i:02d}.{ext}"
        q, _e = _safe_target(cand)
        if q is not None and not q.exists():
            return cand
    return rel


def _record_appearance_prompt(
    kind: str, prompt: str, full: str, path: str, model: str = "", reference: str = ""
) -> None:
    """把每次出图用的提示词存档，供结构阶段复用（外观-结构同源的唯一可靠渠道）。

    为什么不让模型自己传：实测模型转述会丢细节、还会顺手改尺寸，
    结构阶段拿到的就是一版「看起来像但数字不对」的描述。
    """
    import json

    dest, err = _safe_target(PROMPT_STORE)
    if dest is None:
        return
    # 模型常会先手写一份更详细的 prompts.json（product / prompts / structure_notes，
    # 外形约束比工具记的还全）。工具这边只能**合并**，不能整份覆盖 ——
    # 覆盖一次，结构阶段就再也拿不到那版外形约束了。
    doc: dict[str, Any] = {}
    if dest.exists():
        try:
            loaded = json.loads(dest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                doc = loaded
        except Exception:  # noqa: BLE001  存档坏了就重建，不影响出图结果
            doc = {}
    items = [it for it in (doc.get("items") or []) if isinstance(it, dict)]
    items.append(
        {
            "kind": kind,
            "prompt": prompt,
            "full": full,
            "file": path,
            "model": model,
            "reference": reference,
            "ts": round(time.time(), 1),
        }
    )
    doc["items"] = items[-20:]
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        emit("artifact", path=PROMPT_STORE, size=dest.stat().st_size, preview="")
    except OSError:
        pass


def load_appearance_context(run_dir: Path | None = None) -> dict[str, Any]:
    """读回外观阶段的提示词与关键描述，供结构阶段保持同源。

    这个文件有两种写法，都得认：
    - 工具自动存档的 `{"items": [...]}`（每次出图追加）
    - 模型手写的 `{"prompts": [...], "structure_notes": {...}}`（实测它会顺手写一份，
      还常常带更全的外形约束）。后者会把前者覆盖掉，所以不能只认一种格式 ——
      哪一份丢了都会让结构阶段拿不到形状约束。
    """
    import json

    import assets

    base = run_dir or _run_path()
    out: dict[str, Any] = {"prompts": [], "notes": {}, "source": "", "appearance_md": "", "images": []}
    # 可用的渲染图清单：结构阶段可以用它做「形状参照物」的显式引用，
    # 坏图（被文本覆盖 / 截断）不算 —— 拿一张打不开的图当参照等于没有参照
    try:
        bad = {rel for rel, _w in assets.bad_images(base, "10_appearance")}
        out["images"] = [
            p.relative_to(base).as_posix()
            for p in assets.image_files(base, "10_appearance")
            if p.relative_to(base).as_posix() not in bad
        ]
    except Exception:  # noqa: BLE001
        out["images"] = []
    f = base / "10_appearance" / "prompts.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items = list(data.get("items") or [])
            for p in data.get("prompts") or []:
                if isinstance(p, dict) and p.get("prompt"):
                    items.append(
                        {
                            "kind": p.get("kind") or "render",
                            "prompt": str(p.get("prompt")),
                            "file": p.get("filename") or "",
                            "source": "model",
                        }
                    )
            out["prompts"] = items
            out["notes"] = data.get("structure_notes") or {}
            out["source"] = PROMPT_STORE
        except Exception:  # noqa: BLE001  存档坏了就当没有，结构阶段还有 appearance.md 兜底
            pass
    md = base / "10_appearance" / "appearance.md"
    if md.exists():
        try:
            out["appearance_md"] = md.read_text(encoding="utf-8")[:1500]
        except OSError:
            pass
    return out


@tool
def search_cirkit_docs(query: str, top_k: int = 5) -> str:
    """Search the Cirkit Designer documentation knowledge base (23 pages: tutorials, Arduino/ESP32/RPi Pico platforms, simulation, custom parts, Create Agent troubleshooting).

    Use this before deciding components, wiring, or simulation steps, so the design matches what Cirkit Designer actually supports.
    """
    emit("tool_note", name="search_cirkit_docs", detail=query)
    return _clip(KB.format(query, top_k=top_k))


def _guess_artifact_path(content: str) -> str:
    """模型只传 content 没传 path 时按内容推断落盘位置。

    背景：agnes 系模型在长参数（整段脚本/整篇文档塞进 JSON）下会丢 path 字段，
    ValidationError 回给它后下一轮还犯 —— 硬报错只会把 token 烧在同一个坑里，
    不如推断落盘并在回执里教它下次带上 path。
    """
    head = content[:2000]
    low = head.lower()
    if "from build123d import" in head:
        return "20_structure/model.py"
    if head.lstrip().startswith("#"):
        if "结构" in head or "structure" in low:
            return "20_structure/structure.md"
        if "外观" in head or "appearance" in low:
            return "10_appearance/appearance.md"
        if "硬件" in head or "hardware" in low or "bom" in low:
            return "30_hardware/hardware.md"
        if "固件" in head or "firmware" in low or "仿真" in head:
            return "40_firmware/firmware.md"
        if "评审" in head or "review" in low:
            return "90_review/review.md"
        if "需求" in head or "prd" in low:
            return "00_prd/prd.md"
    if head.lstrip().startswith("```") and ("netlist" in low or "components" in low):
        return "30_hardware/netlist.json"
    return "90_review/notes.md"


@tool
def save_artifact(path: str = "", content: str = "") -> str:
    """Persist a deliverable file into the project workspace and notify the UI.

    path is relative to the run workspace and MUST start with a stage prefix:
    00_prd/, 10_appearance/, 20_structure/, 30_hardware/, 40_firmware/, 90_review/.
    Always pass BOTH path and content.
    """
    import assets

    # 传反检测：path 是长文本而 content 是短路径 → 互换（长参数下模型会搞反顺序）
    if path and content and "\n" in path and len(path) > 200 and "\n" not in content and len(content) < 120:
        path, content = content, path
    if not content:
        return "save_artifact 需要 content（文件内容）。你这次传的 content 为空，请把完整内容放进 content 重试。"
    guessed = ""
    if not path.strip():
        path = _guess_artifact_path(content)
        guessed = (
            f"（注意：你这次没传 path，已按内容推断保存到 {path}；"
            "下次请同时传 path 和 content 两个参数）"
        )
    rel = _rel_of(path)
    target, err = _safe_target(rel)
    if target is None:
        return err
    # 二进制产物与占位内容一律拒绝：实测有子智能体在出图成功之后又用本工具把
    # 700KB 的渲染图覆盖成 11 字节的 "placeholder"，前端于是显示两张坏图。
    blocked = assets.guard_text_write(rel, content)
    if blocked:
        return blocked
    dup = _dup_write(rel, content)
    if dup:
        return dup
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    emit("artifact", path=rel, size=len(content), preview=content[:600])
    _sync_stage(rel)
    return f"已写入 {rel}（{len(content)} 字符）。{guessed}"


@tool
def check_circuit(netlist_json: str) -> str:
    """Validate a circuit netlist with electrical rules before finalizing the hardware design.

    netlist_json shape:
    {"components":[{"id":"R1","type":"resistor","value":"220ohm"}],
     "connections":[{"from":"U1.5V","to":"R1.1"},{"from":"R1.2","to":"D1.anode"}],
     "supplies":[{"net":"5V","voltage":5.0}]}
    Checks: LED without current-limiting resistor, VCC shorted to GND, duplicate pin usage,
    floating supply pins, 3V3 parts on 5V nets, missing I2C pull-ups.
    """
    emit("tool_note", name="check_circuit", detail="ERC")
    try:
        data = json.loads(netlist_json)
    except json.JSONDecodeError as e:
        return f"JSON 解析失败：{e}。请传入合法 JSON 字符串。"

    comps = {c.get("id", ""): c for c in data.get("components", []) if c.get("id")}
    conns = data.get("connections", [])
    supplies = data.get("supplies", [])

    issues: list[str] = []
    warns: list[str] = []

    def net_of(pin: str) -> str:
        return pin.split(".", 1)[-1].upper()

    # 1. 引脚重复占用
    used: dict[str, list[str]] = {}
    for c in conns:
        for p in (c.get("from"), c.get("to")):
            if p:
                used.setdefault(p, []).append(f"{c.get('from')}->{c.get('to')}")
    for pin, hits in used.items():
        if len(hits) > 1:
            warns.append(f"引脚 {pin} 被 {len(hits)} 条连线复用：{', '.join(hits[:3])}")

    # 2. 邻接表
    adj: dict[str, set[str]] = {}
    for c in conns:
        a, b = c.get("from"), c.get("to")
        if not a or not b:
            issues.append(f"连线缺少端点：{c}")
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    # 3. 电源直连地
    for a, nbrs in adj.items():
        if re.search(r"(^|\.)(5V|VCC|VIN|3V3|3\.3V)$", a, re.I):
            for b in nbrs:
                if re.search(r"(^|\.)(GND|GROUND)$", b, re.I):
                    issues.append(f"严重：电源 {a} 直连地 {b}，构成短路。")

    # 4. LED 限流电阻
    leds = [cid for cid, c in comps.items() if "led" in str(c.get("type", "")).lower() or cid.upper().startswith("D")]
    for led in leds:
        nbrs = {n.split(".")[0] for p in (f"{led}.anode", f"{led}.cathode", f"{led}.1", f"{led}.2") for n in adj.get(p, ())}
        has_r = any(comps.get(n, {}).get("type", "").lower().startswith("res") for n in nbrs)
        if not has_r:
            issues.append(f"严重：LED {led} 回路中未发现限流电阻，会烧毁 LED 或 IO 口。")

    # 5. 3V3 器件挂 5V
    for cid, c in comps.items():
        note = json.dumps(c, ensure_ascii=False)
        if re.search(r"3\.3\s*V|3V3", note, re.I):
            for p, nbrs in adj.items():
                if p.startswith(f"{cid}.") and any(re.search(r"(^|\.)(5V|VIN)$", n, re.I) for n in nbrs):
                    warns.append(f"警告：{cid} 为 3.3V 器件，却接到 5V 网络。")

    # 6. I2C 上拉
    for pin in adj:
        if re.search(r"\.(SDA|SCL)$", pin, re.I):
            nbrs = adj[pin]
            if not any("res" in comps.get(n.split(".")[0], {}).get("type", "").lower() for n in nbrs):
                warns.append(f"提示：{pin} 未见上拉电阻，长线通信可能不稳定。")
            break

    # 7. 电源引脚悬空
    if supplies and not any("GND" in p.upper() for p in adj):
        warns.append("提示：网表中未出现 GND 连接，回路不完整。")

    # 8. 元件到底有没有接进电路：漏点号的端点（C2正极）和完全没出现过的元件，
    #    都会让 PCB 上出现一只没有飞线的孤立器件
    touched = {str(q).split(".", 1)[0] for c in conns
               for q in (c.get("from"), c.get("to")) if q and "." in str(q)}
    lonely = [cid for cid in comps if cid not in touched]
    if lonely:
        warns.append("警告：" + "、".join(lonely) + " 没有任何连线（PCB 上不会有飞线），"
                                    "补 connections 或从网表里删掉")
    bad_ep = sorted({str(q) for c in conns for q in (c.get("from"), c.get("to"))
                     if q and "." not in str(q) and re.search(r"(正极|负极|阳极|阴极)$", str(q))})
    if bad_ep:
        warns.append("端点少了点号：" + "、".join(bad_ep[:6]) + " —— 要写成 位号.引脚名，"
                                   "否则这几只元件等于没接线")

    verdict = "通过" if not issues else "未通过"
    lines = [f"电路规则校验：{verdict}（{len(comps)} 个元件 / {len(conns)} 条连线）"]
    if issues:
        lines.append("\n必须修复：")
        lines += [f"  - {i}" for i in issues]
    if warns:
        lines.append("\n建议关注：")
        lines += [f"  - {w}" for w in warns]
    if not issues and not warns:
        lines.append("未发现明显电气问题（本工具为规则级检查，不能替代完整 ERC/仿真）。")
    result = "\n".join(lines)
    emit("tool_result", name="check_circuit", detail=result[:400])
    return _clip(result)


@tool
def build_bom(items_json: str) -> str:
    """Normalize a component list into a BOM, write 30_hardware/bom.csv, and return a markdown table.

    items_json: [{"name":"Arduino UNO R3","type":"mcu","value":"","qty":1,"footprint":"","note":"主控"}]
    """
    emit("tool_note", name="build_bom", detail="BOM")
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError as e:
        return f"JSON 解析失败：{e}"
    if not isinstance(items, list) or not items:
        return "items_json 必须是非空数组。"

    rows: list[dict[str, Any]] = []
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for it in items:
        key = (str(it.get("name", "")).strip(), str(it.get("value", "")).strip())
        if key in merged:
            merged[key]["qty"] += int(it.get("qty", 1) or 1)
            continue
        merged[key] = {
            "name": key[0],
            "type": str(it.get("type", "")).strip(),
            "value": key[1],
            "qty": int(it.get("qty", 1) or 1),
            "footprint": str(it.get("footprint", "")).strip(),
            "note": str(it.get("note", "")).strip(),
        }
    rows = list(merged.values())

    out = _run_path() / "30_hardware" / "bom.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["序号", "名称", "类别", "规格", "数量", "封装", "备注"])
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow(
                {
                    "序号": i,
                    "名称": r["name"],
                    "类别": r["type"],
                    "规格": r["value"],
                    "数量": r["qty"],
                    "封装": r["footprint"],
                    "备注": r["note"],
                }
            )
    emit("artifact", path="30_hardware/bom.csv", size=out.stat().st_size, preview="")

    table = ["| # | 名称 | 类别 | 规格 | 数量 |", "|---|------|------|------|------|"]
    for i, r in enumerate(rows, 1):
        table.append(f"| {i} | {r['name']} | {r['type']} | {r['value']} | {r['qty']} |")
    return _clip(
        f"BOM 共 {len(rows)} 项 / 总件数 {sum(r['qty'] for r in rows)}，已写入 30_hardware/bom.csv\n\n" + "\n".join(table)
    )


@tool
def check_arduino(code: str, expected_pins: str = "") -> str:
    """Static-check Arduino (.ino) source: structure, bracket balance, pin usage vs the netlist, blocking delays.

    expected_pins: comma separated pin numbers used by the circuit, e.g. "13,A0,2".
    """
    emit("tool_note", name="check_arduino", detail="firmware lint")
    issues: list[str] = []
    warns: list[str] = []

    if "void setup()" not in code and "void setup (" not in code:
        issues.append("缺少 void setup()。")
    if "void loop()" not in code and "void loop (" not in code:
        issues.append("缺少 void loop()。")

    for pair in ("{}", "()", "[]"):
        if code.count(pair[0]) != code.count(pair[1]):
            issues.append(f"{pair} 括号不配对：{pair[0]}={code.count(pair[0])}, {pair[1]}={code.count(pair[1])}")

    used_pins = set()
    for fn in ("pinMode", "digitalWrite", "digitalRead", "analogWrite", "analogRead"):
        for m in re.finditer(rf"{fn}\s*\(\s*([A-Za-z0-9_]+)\s*[,)]", code):
            arg = m.group(1)
            if arg.isdigit() or arg.upper().startswith("A") and arg[1:].isdigit():
                used_pins.add(arg.upper())

    expected = {p.strip().upper() for p in expected_pins.split(",") if p.strip()}
    if expected:
        missing = sorted(expected - used_pins)
        extra = sorted(used_pins - expected)
        if missing:
            warns.append(f"原理图中出现但代码未初始化的引脚：{', '.join(missing)}")
        if extra:
            warns.append(f"代码使用但网表中未出现的引脚：{', '.join(extra)}")

    if used_pins and not re.search(r"pinMode\s*\(", code):
        issues.append("使用了 IO 但未调用 pinMode() 设置方向。")
    if re.search(r"\bSerial\.(print|println)\b", code) and "Serial.begin" not in code:
        issues.append("使用了 Serial 输出但未调用 Serial.begin(baud)。")
    long_delays = [m.group(1) for m in re.finditer(r"delay\s*\(\s*(\d+)\s*\)", code) if int(m.group(1)) >= 1000]
    if len(long_delays) >= 2:
        warns.append(f"存在 {len(long_delays)} 处 >=1s 的 delay()，会阻塞主循环，建议改 millis() 非阻塞写法。")

    verdict = "通过" if not issues else "未通过"
    lines = [f"固件静态检查：{verdict}（识别引脚 {', '.join(sorted(used_pins)) or '无'}）"]
    lines += [f"  - {i}" for i in issues]
    lines += [f"  - {w}" for w in warns]
    if not issues and not warns:
        lines.append("未发现结构性问题（静态检查不能替代真实编译）。")
    return _clip("\n".join(lines))


@tool
def render_schematic(netlist_json: str = "", filename: str = "30_hardware/schematic.svg") -> str:
    """把电路网表渲染成电路原理图 SVG（离线，秒级出图，不依赖任何图像服务）。

    netlist_json：留空则自动读取 30_hardware/netlist.json（推荐 —— 与 check_circuit 校验的
    是同一份数据，保证图与校验结果一致）。也可直接传 JSON 字符串临时出图。
    网表格式与 check_circuit 完全一致：
      {"components":[{"id":"U1","type":"mcu","value":"ESP32-C3"},
                     {"id":"R1","type":"resistor","value":"220ohm"},
                     {"id":"D1","type":"led","value":"red 5mm"}],
       "connections":[{"from":"U1.GPIO8","to":"R1.1"},{"from":"R1.2","to":"D1.anode"},
                      {"from":"D1.cathode","to":"GND"},{"from":"U1.3V3","to":"3V3"}],
       "supplies":[{"net":"3V3","voltage":3.3}]}

    出图规则：顶部电源轨、底部 GND 轨，元件按网格排布，信号网用曼哈顿折线连接。
    元件类型会自动识别（电阻/电容/LED/二极管/晶振/开关/三极管/连接器/电机/蜂鸣器/IC），
    识别不到按通用盒子画，引脚从 connections 里自动抽取。

    产物写入 filename（默认 30_hardware/schematic.svg），可在产物面板直接查看。
    """
    import schematic

    emit("tool_progress", name="render_schematic", detail=filename)

    data: dict[str, Any] | None = None
    if netlist_json.strip():
        try:
            data = json.loads(netlist_json)
        except json.JSONDecodeError as e:
            return f"JSON 解析失败：{e}。请传入合法 JSON 字符串，或留空让它自动读 netlist.json。"
    else:
        src = _run_path() / "30_hardware" / "netlist.json"
        if not src.exists():
            return "找不到 30_hardware/netlist.json。请先用 save_artifact 写出网表，或直接传 netlist_json。"
        data = schematic.load_netlist(src)
        if data is None:
            return f"{src} 不是合法 JSON。请先修正网表文件。"

    ok, err = _safe_target(filename)
    if ok is None:
        return err
    dest = ok
    if dest.suffix.lower() != ".svg":
        dest = dest.with_suffix(".svg")

    title = f"电路原理图 · {len(data.get('components') or [])} 元件"
    ok, size = schematic.render_to(data, dest, title)
    if not ok:
        return "原理图渲染失败。请检查网表结构（components / connections 是否为数组）。"

    rel = dest.relative_to(_run_path()).as_posix() if str(dest).startswith(str(_run_path())) else dest.name
    emit("artifact", path=rel, size=size, preview="")

    n_c = len(data.get("components") or [])
    n_w = len(data.get("connections") or [])
    result = (
        f"电路原理图已生成：{rel}（{size/1024:.0f} KB，{n_c} 元件 / {n_w} 连线）。\n"
        f"电源与地走上下轨，信号网络用折线连接并带节点圆点。\n"
        f"在 hardware.md 里用 ![电路原理图](schematic.svg) 引用即可。"
    )
    emit("tool_result", name="render_schematic", detail=result[:300])
    return result


@tool
def emit_kicad_project(netlist_json: str = "", name: str = "schematic") -> str:
    """把电路网表导出成 KiCad 工程（.kicad_pro + .kicad_sch）—— 真 EDA 文件，不是示意图。

    与 render_schematic 的区别：那个出的是 SVG 图片（给人看），这个出的是 KiCad 原生工程
    （给 EDA 用：能改、能导网表、能进 PCB、能出 BOM）。两者读同一份网表，内容永远一致。

    netlist_json：留空则自动读取 30_hardware/netlist.json（推荐）。格式与 check_circuit 一致：
      {"components":[{"id":"U1","type":"module","value":"ESP32-C3-MINI-1"}],
       "connections":[{"from":"U1.GPIO8","to":"R1.1"}],
       "supplies":[{"net":"3V3","voltage":3.3}]}
    name：工程文件名（不含扩展名），默认 "schematic"。

    产物：30_hardware/<name>.kicad_pro、<name>.kicad_sch、<name>.kicad_sym、sym-lib-table。
    符号既内嵌在原理图里、又单独出一份符号库并登记到库表 —— 换台机器双击即开，
    跑 ERC 也不会提示「符号库未配置」。**整个 30_hardware 目录一起拷走才是完整工程。**
    封装名按器件库自动补（模块/IC/连接器用库里的真实引脚名），没匹配到的留空不臆造。
    """
    emit("tool_progress", name="emit_kicad_project", detail=name)

    kicad_write = _eda("kicad", "write")
    annotate = _eda("netlist", "annotate")
    normalize = _eda("netlist", "normalize")

    data: dict[str, Any] | None = None
    if netlist_json.strip():
        try:
            data = json.loads(netlist_json)
        except json.JSONDecodeError as e:
            return f"JSON 解析失败：{e}。请传入合法 JSON 字符串，或留空让它自动读 netlist.json。"
    else:
        src = _run_path() / "30_hardware" / "netlist.json"
        if not src.exists():
            return "找不到 30_hardware/netlist.json。请先用 save_artifact 写出网表，或直接传 netlist_json。"
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return f"{src} 不是合法 JSON（{e}）。请先修正网表文件。"

    stem = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or "schematic").strip()) or "schematic"
    ok, err = _safe_target(f"30_hardware/{stem}.kicad_sch")
    if ok is None:
        return err
    out_dir = ok.parent

    net = annotate(normalize(data, title=stem))
    if not (net.get("components") or []):
        return "网表里没有元件，无法生成工程。请先补全 components。"

    res = kicad_write(net, out_dir, stem)
    if not res.get("ok"):
        return f"KiCad 工程生成失败：{res.get('error')}。请检查网表结构后重试。"

    lines = [f"KiCad 工程已生成（{stem}）："]
    for f in res.get("files") or []:
        rel = f"30_hardware/{f['name']}"
        emit("artifact", path=rel, size=f["size"], preview="")
        lines.append(f"  - {rel}（{f['size']/1024:.1f} KB）")

    s = net.get("stats") or {}
    matched = s.get("parts_matched", 0)
    warns = net.get("warnings") or []
    lines.append(
        f"{s.get('components', 0)} 个器件 / {s.get('connections', 0)} 条连线 / {s.get('nets', 0)} 个网络；"
        f"器件库补齐封装 {matched} 个。"
    )
    lines.append("符号内嵌在 .kicad_sch 里，同时带符号库与库表，不依赖官方库；"
                 "电源与地已画成标准电源符号，悬空脚标了 no_connect。")
    if warns:
        lines.append(f"规范化提示 {len(warns)} 条，例如：" + "；".join(warns[:3]))
    iso = net.get("unconnected") or []
    if iso:
        lines.append(
            "⚠ 这些器件在 netlist.json 里没有任何连线，PCB 上不会有飞线："
            + "、".join(iso[:12])
            + "。这是网表漏写，不是导出问题 —— 回硬件阶段把它们的 connections 补上。"
        )
    lines.append("在 hardware.md 里注明「附 KiCad 工程，可用 KiCad 9+ 打开」即可。")

    result = "\n".join(lines)
    emit("tool_result", name="emit_kicad_project", detail=result[:400])
    return _clip(result)


@tool
def emit_pcb(board_json: str = "", name: str = "board") -> str:
    """把电路网表 + 结构板级约束导出成 KiCad PCB（.kicad_pcb）—— 真板子文件。

    读两份数据：
      - 30_hardware/netlist.json（器件与网络，来自 check_circuit / save_artifact）
      - 20_structure/board.json（板框硬边界：内腔尺寸、安装孔、禁布区、限高、接口靠边）
    board_json：留空则自动读取 20_structure/board.json（推荐）；也可直接传 JSON 字符串。

    board_json 结构（结构工程师负责产出，形状支持异形）：
      {"board": {"shape": "rect|rounded_rect|circle|polygon", "width_mm": 52, "height_mm": 34,
                 "corner_radius_mm": 3, "thickness_mm": 1.6,
                 "polygon_mm": [[x,y], ...]},            # shape=polygon 时给顶点
       "mounting_holes": [{"x_mm": -22, "y_mm": -12, "diameter_mm": 3.2, "plated": false}],
       "keepouts": [{"name": "电池仓", "x_mm": 12, "y_mm": 8, "w_mm": 16, "h_mm": 12}],
       "edge_constraints": [{"ref": "J1", "edge": "left"}]}
    坐标以**板中心为原点**，单位 mm。

    产出：30_hardware/<name>.kicad_pcb、<name>.pretty/（自带封装库）、fp-lib-table。
    **整个 30_hardware 目录一起拷走才是完整工程**，否则会提示封装库缺失。

    能力边界（别向用户承诺超出这个范围的东西）：
      ✅ 板框（圆角/圆形/异形多边形）、器件布局、安装孔、GND 铺铜
      ❌ **不自动布线**：信号线要在 KiCad 里用自带布线器或 Freertos/Freerouting 走线
      ❌ 焊盘是参数化近似，布线前请把封装替换成官方库对应封装（descr 里写了原目标封装名）
      ❌ 不出 Gerber：那要在 EDA 里导出
    """
    emit("tool_progress", name="emit_pcb", detail=name)

    board_load = _eda("board", "load")
    board_normalize = _eda("board", "normalize")
    annotate = _eda("netlist", "annotate")
    normalize = _eda("netlist", "normalize")
    pcb_write = _eda("pcb", "write")

    root = _run_path()

    src = root / "30_hardware" / "netlist.json"
    if not src.exists():
        return ("找不到 30_hardware/netlist.json。请先用 save_artifact 写出网表"
                "（或先调 check_circuit / emit_kicad_project）再出 PCB。")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return f"{src} 不是合法 JSON（{e}）。请先修正网表文件。"

    if board_json.strip():
        try:
            board = board_normalize(json.loads(board_json))
        except json.JSONDecodeError as e:
            return f"board_json 解析失败：{e}。留空可自动读 20_structure/board.json。"
    else:
        board = board_load(root / "20_structure" / "board.json")

    stem = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or "board").strip()) or "board"
    ok, err = _safe_target(f"30_hardware/{stem}.kicad_pcb")
    if ok is None:
        return err
    out_dir = ok.parent

    net = annotate(normalize(data, title=stem))
    if not (net.get("components") or []):
        return "网表里没有元件，无法生成 PCB。请先补全 components。"

    res = pcb_write(net, board, out_dir, stem)
    if not res.get("ok"):
        return f"PCB 生成失败：{res.get('error')}。请检查网表与 board.json 后重试。"

    main = [f for f in res.get("files") or [] if f["name"].endswith(".kicad_pcb")]
    mods = [f for f in res.get("files") or [] if f["name"].endswith(".kicad_mod")]
    for f in main + [x for x in (res.get("files") or []) if x["name"] == "fp-lib-table"]:
        emit("artifact", path=f"30_hardware/{f['name']}", size=f["size"], preview="")

    bs = board.get("stats") or {}
    s = net.get("stats") or {}
    lines = [f"KiCad PCB 已生成（{stem}）："]
    for f in main:
        lines.append(f"  - 30_hardware/{f['name']}（{f['size']/1024:.1f} KB）")
    lines.append(f"  - 30_hardware/{stem}.pretty/（{len(mods)} 个封装）、fp-lib-table（工程自带库）")
    lines.append(
        f"板框 {bs.get('shape')} {bs.get('bbox_mm')}mm，面积 {bs.get('area_mm2')} mm²，"
        f"安装孔 {bs.get('mounting_holes')} 个、禁布区 {bs.get('keepouts')} 个；"
        f"{s.get('components', 0)} 个器件已布局，GND 铺在 B.Cu。"
    )
    if board.get("warnings"):
        lines.append("板级约束提示：" + "；".join(board["warnings"][:3]))
    iso = net.get("unconnected") or []
    if iso:
        lines.append(
            "⚠ 这些器件在 netlist.json 里没有任何连线，PCB 上不会有飞线："
            + "、".join(iso[:12])
            + "。这是网表漏写，不是导出问题 —— 回硬件阶段把它们的 connections 补上。"
        )
    lines.append(
        "**未布线**：信号线请在 KiCad 里用自带布线器（或导出 DSN 交给 Freerouting）走线；"
        "焊盘是参数化近似，布线前把封装换成官方库对应封装（封装描述里写了原目标封装名）；"
        "Gerber 在 KiCad 里导出。board.json 的数值如果和结构内腔对不上，先回去改结构，别在 PCB 里硬凑。"
    )

    result = "\n".join(lines)
    emit("tool_result", name="emit_pcb", detail=result[:400])
    return _clip(result)


@tool
def emit_easyeda(name: str = "board") -> str:
    """把同一份网表再投影成嘉立创 EDA 专业版（EasyEDA Pro）原生工程 `.epro`。

    给习惯了嘉立创的用户双击即开的工程，省掉「KiCad 导入」这一步。
    要在 emit_pcb 之后调用（读的是同一份 netlist.json 与 board.json）。

    name：工程名，默认 board。产出 30_hardware/<name>.epro。

    能力边界（如实写进交付说明，别让用户以为这是完整工程）：
      ✅ PCB 段：板框（含圆角/异形）、器件布局、安装孔、网络表
      ✅ 原理图页：符号 + 引脚引出线 + 网络标签（不画跨图走线，靠 NET 并网）
      ✅ 同时产出 30_hardware/<name>.schematic.svg —— 原理图预览，浏览器直接打开就能看排版，
         交付说明里要告诉用户可以先看这张图
      ✅ **符号与封装优先取自立创官方元件库**（按型号搜 LCSC 编号，拉真符号真封装）；
         搜不到才退回本地参数化图形，此时封装是近似值、布线前要替换
      ❌ 不布线、不出 Gerber（Gerber 用 KiCad 那条链路）
      ✅ 格式对齐客户端导出的真实工程（分文件 zip + JSON Lines），
         并用 KiCad 的 EasyEDA Pro 导入器回读校验过器件数/焊盘数/板框尺寸
    """
    emit("tool_progress", name="emit_easyeda", detail=name)

    board_load = _eda("board", "load")
    easyeda_write = _eda("easyeda", "write")
    annotate = _eda("netlist", "annotate")
    normalize = _eda("netlist", "normalize")

    root = _run_path()
    src = root / "30_hardware" / "netlist.json"
    if not src.exists():
        return "找不到 30_hardware/netlist.json。请先完成硬件设计（或先调 emit_pcb）再导出。"
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return f"{src} 不是合法 JSON（{e}）。请先修正网表。"

    board = board_load(root / "20_structure" / "board.json")
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", str(name or "board").strip()) or "board"
    ok, err = _safe_target(f"30_hardware/{stem}.epro")
    if ok is None:
        return err

    net = annotate(normalize(data, title=stem))
    if not (net.get("components") or []):
        return "网表里没有元件，无法生成工程。请先补全 components。"

    res = easyeda_write(net, board, ok.parent, stem)
    if not res.get("ok"):
        return f"EasyEDA 工程生成失败：{res.get('error')}。请检查网表与 board.json 后重试。"

    for f in res.get("files") or []:
        emit("artifact", path=f"30_hardware/{f['name']}", size=f["size"], preview="")

    # 原理图预览（纯 Python 画的 SVG）：用户不必开客户端就能看排版对不对
    sch_note = ""
    try:
        _sch_svg = _eda("preview", "write_svg")
        svg = _sch_svg(ok, ok.parent / f"{stem}.schematic.svg", stem)
        emit("artifact", path=f"30_hardware/{svg.name}", size=svg.stat().st_size, preview="")
        sch_note = f"  - 30_hardware/{svg.name}（原理图预览，浏览器可直接打开）\n"
    except Exception as exc:                                     # noqa: BLE001
        sch_note = f"  （原理图预览生成失败：{type(exc).__name__}: {exc}，不影响 .epro）\n"

    iso = net.get("unconnected") or []
    lines = [
        f"EasyEDA Pro 工程已生成（{stem}）：",
        f"  - 30_hardware/{stem}.epro（{res['size']/1024:.1f} KB，{res.get('note')}）",
        sch_note.rstrip("\n"),
        "内容与 KiCad 版同源：同一份网表、同一套焊盘几何、同一份布局坐标，两块板不会对不上。",
        "打开方式：嘉立创 EDA 专业版 → 文件 → 打开 → 选这个 .epro。",
        "**如实说明**：① PCB 与原理图都有，但原理图是「每个引脚一小截引出线 + "
        "同名 NET 标签」的画法（靠网名并网，与真机的网络标签机制一致），不是手工排的走线图；"
        "② PCB 未布线；③ 格式对齐客户端导出的真实工程，PCB 段已用 KiCad 的 EasyEDA Pro "
        "导入器回读校验（器件数 / 焊盘数 / 板框尺寸都对得上），原理图段暂只能做结构自检；"
        "本机没有 EasyEDA 客户端，打开效果仍需你实测，如显示异常请反馈。",
    ]
    if iso:
        lines.append(
            "⚠ 这些器件在 netlist.json 里没有任何连线，画布上不会有飞线："
            + "、".join(iso[:12])
            + "。这是网表漏写，不是导出问题 —— 回硬件阶段把它们的 connections 补上。"
        )
    result = "\n".join(lines)
    emit("tool_result", name="emit_easyeda", detail=result[:400])
    return _clip(result)


@tool
def export_gerbers(pcb: str = "board") -> str:
    """把 KiCad PCB 导出成板厂打样要的那包文件（Gerber + 钻孔 + 坐标），并打包成 zip。

    pcb：30_hardware 下的 PCB 名（不含扩展名），默认 board，即 emit_pcb 的产物。

    产出：30_hardware/gerber/（各层 .gbr + .drl + positions.csv）与 30_hardware/gerber.zip。
    走本机 kicad-cli 无头导出；**没装 KiCad 会明确告诉你**，不会假装成功。

    能力边界（别向用户承诺超出范围的东西）：
      ✅ Gerber RS-274X、Excellon 钻孔、贴片坐标文件
      ❌ 不校验板厂工艺能力（最小线宽/线距/孔径要自己去板厂页面对）
      ❌ 板子还没布线 —— 导出来只有焊盘、板框和铺铜，信号线要在 KiCad 里布完再导出
    """
    emit("tool_progress", name="export_gerbers", detail=pcb)

    gerber_mod = _mod(f"{_eda_pkg()}.gerber")

    root = _run_path()
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", str(pcb or "board").strip()) or "board"
    src = root / "30_hardware" / f"{stem}.kicad_pcb"
    if not src.exists():
        return (f"找不到 30_hardware/{stem}.kicad_pcb。请先调 emit_pcb 生成 PCB，"
                "再导 Gerber（没板子就没东西可导）。")

    out_dir = root / "30_hardware" / "gerber"
    res = gerber_mod.export(src, out_dir)
    files = [str(f) for f in (res.get("files") or [])]
    if not files:
        return (f"Gerber 导出失败：{res.get('error') or '未知原因'}。\n"
                f"手工导出方式：{res.get('hint', '')}")

    zip_path = root / "30_hardware" / "gerber.zip"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    readme = "\n".join([
        f"{stem} 制造文件包（{stamp}）",
        "",
        "目录：gerber/（散层），本 zip 是同一批文件的打包。",
        "打样必需：顶层/底层线路、阻焊、丝印、板框（Edge_Cuts）、钻孔 .drl。",
        "贴片坐标：positions.csv。BOM：见同目录 bom.csv。",
        "",
        "注意：",
        "1. 本板由智能体生成，信号线未布线，需先在 KiCad 里完成布线再打样。",
        "2. 封装是参数化近似，布线前请替换为官方库对应封装。",
        "3. 提交板厂前请自行核对最小线宽/线距/孔径等工艺能力。",
    ])
    packed = gerber_mod.pack(files, zip_path, extra={"README.txt": readme})
    if not packed.get("ok"):
        return f"文件已导出到 30_hardware/gerber/，但打包失败：{packed.get('error')}"

    for rel in ("30_hardware/gerber.zip",):
        p = root / rel
        if p.exists():
            emit("artifact", path=rel, size=p.stat().st_size, preview="")

    need = [Path(f).name for f in gerber_mod.essential(files)]
    lines = [f"制造文件已导出（{stem}）：",
             f"  - 30_hardware/gerber.zip（{packed.get('size', 0) / 1024:.1f} KB，{packed.get('count')} 个文件）",
             f"  - 30_hardware/gerber/（散层，可直接拖进板厂下单页）",
             f"必需层 {len(need)} 个：" + "、".join(need[:8]) + ("…" if len(need) > 8 else "")]
    if res.get("failed"):
        lines.append("部分步骤没跑成功（文件可能不全）：" + "；".join(str(x)[:80] for x in res["failed"][:2]))
    lines.append("**未布线**：信号线请在 KiCad 里布完再重新导出，否则板厂拿到的是没有走线的板。")

    result = "\n".join(lines)
    emit("tool_result", name="export_gerbers", detail=result[:400])
    return _clip(result)


@tool
def generate_product_image(
    prompt: str,
    kind: str = "render",
    filename: str = "",
    reference: str = "auto",
) -> str:
    """生成产品图片并落盘到工作区（约 10-20 秒一张）。

    prompt：中文描述产品主体（形态/材质/颜色/关键细节），不要写镜头和背景，工具会自动追加。
    kind：render = 产品渲染图；exploded = 爆炸图（零件分离 + 引导线 + 编号）。
    filename：可选，形如 10_appearance/render-01.png，默认按 kind 自动命名。

    reference（一致性关键，默认别改）：
        "auto"（默认）—— 自动以**本产品已生成的第一张/上一张产品图**为参考做图生图：
        第二张外观图沿用上一张外观图，爆炸图沿用第一张外观图。同一产品形变量最小。
        "none" —— 强制文生图（只在确实要换一个全新产品时用）。
        具体路径 —— 指定参考图，如 10_appearance/render-01.png。

    同一产品的**多张图必须串行生成**：先出第一张，再基于它出第二张。
    不要并行或一次写完所有 prompt，那样每张都是独立文生图，产品会长得不一样。

    图像网关独立于文本模型：可在「模型设置 → 图像」里单独指定服务商与密钥
    （例如文本用 DeepSeek、图像用 SiliconFlow）。留空则跟随文本模型的服务商。

    返回落盘路径与 Markdown 图片引用。

    失败处理（重要）：
    - 错误里含「鉴权失败」= 密钥/网关配错，**重试多少次都不会成功**，不要再调本工具。
    - 任何失败都必须走兜底：立即调用 render_product_views 用真实 3D 几何出工程视图 /
      爆炸图，保证 10_appearance/ 与 20_structure/ 里有可视化产物，并在文档里说明
      「AI 渲染图不可用，以工程视图替代」。绝不允许外观阶段留空。
    - 其余错误（超时/限流/临时故障）可重试 1-2 次，仍失败就转兜底。
    """
    import imagegen

    kind = (kind or "render").strip().lower()
    if kind not in ("render", "exploded"):
        kind = "render"
    if not filename.strip():
        # 按用途归位：渲染图进外观阶段，爆炸图进结构阶段，避免和图文档引用对不上
        filename = _auto_filename(kind)
    else:
        filename = _in_stage_dir(filename, kind)
    rel = _dedupe_rel(_rel_of(filename))
    # 先算出目标路径再过闸：护栏要按「这次会不会覆盖已落盘的东西」判断，
    # 只看阶段状态会把「补齐第二张图」误判成「把第一张重做一遍」。
    blocked = _stage_lock("generate_product_image", "10_appearance", files=[rel])
    if blocked:
        return blocked
    dest, err = _safe_target(rel)
    if dest is None:
        return err

    ref_path, ref_rel, ref_note = _resolve_reference(reference, kind)
    emit("tool_progress", name="generate_product_image", detail=f"{kind}: {prompt[:80]}")
    if ref_path is not None:
        emit("tool_note", name="image_reference", detail=ref_note)
    # 出图固定走 Agnes，与文本模型解耦：前端把文本模型换成 DeepSeek / SiliconFlow
    # 不该让外观渲染图跟着换一家画风（同一产品的渲染图与爆炸图必须同风格）。
    # 只有用户在「模型设置 → 图像」里显式指定了图床时才覆盖。
    explicit = _setting("image_base_url", "")
    if explicit:
        base_url = explicit
        img_key = _setting("image_api_key", "") or _setting("api_key", "") or config.fallback_key(base_url)
        img_model = _setting("image_model", "")
    else:
        base_url, default_model, provider = config.resolve_image_provider()
        img_key = _setting("image_api_key", "") or config.fallback_key(base_url)
        img_model = _setting("image_model", "") or default_model
        emit("tool_note", name="image_provider", detail=f"出图服务商 {provider}（与文本模型解耦）")

    if not img_key:
        return (
            "未配置任何图像服务密钥（.env 与页面设置里都没有）。\n"
            "兜底：立即调用 render_product_views 用真实 3D 几何生成工程视图与爆炸图，"
            "不要留空也不要反复调用本工具。"
        )

    res = imagegen.generate(
        prompt=prompt,
        dest=dest,
        # 密钥必须与 base_url 同源：拿 A 家的 key 打 B 家端点必然 401
        api_key=img_key,
        kind=kind,
        base_url=base_url,
        model=img_model,
        reference=ref_path or "",
    )
    if not res.get("ok"):
        err = str(res.get("error", "未知错误"))
        emit("tool_result", name="generate_product_image", detail=err[:300])
        fallback = (
            "\n\n兜底（必须执行）：调用 render_product_views 用真实 3D 几何生成工程视图与爆炸图，"
            "并把「AI 渲染图不可用，以工程视图替代」写进文档。不要再重试本工具。"
        )
        if not res.get("retryable", False) and res.get("status") in (401, 403):
            return f"图片生成失败（鉴权失败，重试无意义）：{err}{fallback}"
        return f"图片生成失败：{err}{fallback}"

    size_kb = round(res["bytes"] / 1024, 1)
    tls_note = str(res.get("tls") or "")
    if tls_note.startswith("关闭证书校验") or "CDN" in tls_note:
        # 出图接口能过、CDN 直链被自签证书挡住：本机链路上有 HTTPS 拦截。
        # 图已经拿到了，但要让人知道这一跳是放宽校验拿到的。
        emit("tool_note", name="image_tls", detail=f"图片获取方式：{tls_note}")
    emit("artifact", path=rel, size=res["bytes"], preview="")
    # 出**一张**图不等于本阶段做完：外观阶段要连出基线图/场景图/爆炸图，
    # 以前这里直接 _stage_done，于是第二张图撞上「本阶段已落盘请确认」被拦（实测事故）。
    # 现在只刷新台账：契约产物（appearance.md + orthographic.svg + render-01.png）
    # 全齐了才置 done，没齐之前状态保持 working，后续图可以接着出。
    _sync_stage(rel)
    # 提示词必须落盘：结构阶段要靠它做「外观-结构同源」，靠模型口头转述必漂移
    # 存档只记相对路径：绝对路径换台机器就失效，结构阶段还要拿它做一致性核对
    used_ref = ref_rel if (ref_path is not None and res.get("referenced")) else ""
    _record_appearance_prompt(
        kind, prompt, res.get("prompt") or prompt, rel, res.get("model", ""), used_ref
    )
    md = f"![{kind}]({rel})"
    lines = [
        f"已生成 {kind} 图：{rel}（{size_kb} KB，耗时 {res['elapsed']}s）",
        f"一致性：{ref_note}" + (f"；{res['ref_note']}" if res.get("ref_note") else ""),
    ]
    if not used_ref:
        lines.append(
            "注意：本次未使用参考图（文生图）。若这不是本产品的第一张图，"
            "请检查上一张图是否真的落盘，否则后续图会与它不一致。"
        )
    else:
        lines.append("后续图请继续保持 reference=auto，串行生成。")
    lines.append(f"Markdown 引用：{md}\n在文档中插入该引用即可显示图片。")
    return "\n".join(lines)


@tool
def build_structure(script: str, base_name: str = "model") -> str:
    """执行 build123d 建模脚本，把结构件导出为 STEP 与 STL，并生成等轴测预览 SVG。

    做产品结构设计时**优先用 text_to_cad**：它会带上外观阶段的提示词做形状约束，
    建模后自动出四视图与爆炸图。本工具只在你要单独跑一段已有脚本时用。

    script 契约（严格遵守，否则导出不了）：
        from build123d import *
        W, D, H = 100.0, 80.0, 40.0          # 单位 mm，XY 平面 + Z 向上
        WALL, R, BASE_H = 2.0, 8.0, 30.0     # 尺寸一律用常量，不要写魔法数字

        with BuildPart() as shell:
            Box(W, D, BASE_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
            fillet(shell.edges().filter_by(Axis.Z), radius=R)
            # 掏内腔 = 显式减去内缩实体（offset 只生成内缩实体，不会掏空）
            with Locations((0, 0, WALL)):
                Box(W-2*WALL, D-2*WALL, BASE_H-WALL,
                    align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            with Locations((0, 12, BASE_H-WALL/2)):            # 开窗/开口同法
                Box(34, 18, WALL, mode=Mode.SUBTRACT)

        parts = {"下壳": shell.part, "上盖": lid.part.moved(Location((0, 0, BASE_H)))}

    已知会写错的地方（务必避开）：
        - Box(...).fillet(radius=2)        → fillet(builder.edges().filter_by(Axis.Z), radius=R)
        - Workplanes(...)                  → 0.13 已移除，用 Locations + Box(SUBTRACT)
        - Cylinder(r=5, h=3)               → Cylinder(radius=5, height=3)
        - offset(amount=-WALL)             → 不会掏空，用上面的内腔减法
        - bbox.x_length / .y_length / .z_length → BoundBox 没有这些属性，用 bbox.size.X/.Y/.Z
        - show() / export_stl() / print(大对象) → 导出由平台负责，脚本里不要写

    导出到 20_structure/：每个零件一个 .step + .stl，另出一张 <base_name>_preview.svg。
    返回每个零件的包围盒尺寸与体积（mm / cm³），可直接写进结构文档的配合尺寸表。
    脚本报错会连同 traceback 和修正建议一起返回，请据此改写后重试（最多 3 次）。
    """
    import cad

    ok, ver = cad.available()
    if not ok:
        return (
            f"CAD 内核不可用：{ver}\n\n"
            "已依次尝试本地 venv 与 cadgen（text-to-cad 官方运行时），修好任一条即可：\n"
            "1) 在 worker 的 venv 里 pip install build123d（推荐，毫秒级无网络依赖）\n"
            "2) 设 AGENT_CAD_RUNTIME=cadgen 并确保 uvx 可用（首次会联网下载）"
        )

    blocked = _stage_lock("build_structure", "20_structure")
    if blocked:
        return blocked
    emit("tool_progress", name="build_structure", detail=f"{len(script)} 字符脚本")
    out_dir = _run_path() / "20_structure"
    res = cad.build(script, out_dir, base_name=base_name or "model")

    if not res.get("ok"):
        emit("tool_result", name="build_structure", detail=(res.get("error") or "")[:400])
        text, fb_parts = _cad_fail_text(res, script, out_dir, base_name)
        if fb_parts:
            parts = cad.load_parts(out_dir)
            made = _write_view_files(parts, f"{base_name} 结构") if parts else []
            if made:
                text += "\n已生成视图：" + "、".join(f"10_appearance/{m}" for m in made)
        return _clip(text)

    for f in res.get("files", []):
        p = out_dir / f
        emit("artifact", path=f"20_structure/{f}", size=p.stat().st_size if p.exists() else 0, preview="")

    lines = [f"建模成功（build123d {ver}），导出 {len(res['files'])} 个文件到 20_structure/："]
    lines += [f"  - {r['name']}：{r['size_mm'][0]}×{r['size_mm'][1]}×{r['size_mm'][2]} mm，体积 {r['volume_cm3']} cm³" for r in res["parts"]]
    lines += [f"ℹ️ {n}" for n in (res.get("notes") or [])]
    lines.append("文件：" + "、".join(res["files"]))
    lines.append("预览：<base_name>_preview.svg，可在产物面板直接查看。")
    return _clip("\n".join(lines))


@tool
def text_to_cad(script: str = "", base_name: str = "model", appearance_prompt: str = "") -> str:
    """用 text-to-cad（cadgen / build123d）规范生成结构模型，并与外观阶段保持同源。

    **结构不另起炉灶**：外观阶段每次出图用的提示词都已存档在 10_appearance/prompts.json，
    本工具会读回来当作建模的形状约束 —— 结构件的外形、比例、分件方式必须长在
    那张渲染图上，而不是结构工程师重新想象一个产品。

    用法（两步）：
    1. 先不传 script 调用一次：工具返回外观阶段存档的提示词 + 已实测的脚本模板，
       你据此写脚本（尺寸取自外观提示词/appearance.md，不要另编）。
    2. 再带上 script 调用：建模 → 导出 STEP/STL → 自动生成四视图与爆炸图 →
       自动写 20_structure/consistency.md（外观提示词 vs 实际模型的对照表）。

    建模规范（text-to-cad 官方 skill）：
    - 单位毫米，XY 平面 + Z 向上；必须是闭合的正体积实体，不留曲面/开放壳
    - 装配用 .moved(Location((x, y, z))) 定位
    - 壁厚、拔模、倒角要有意识地表达 —— 它们就是 DFM 检查项
    - 这四个写法一定报错：Box(...).fillet(radius=)、Workplanes(...)、Cylinder(r=,h=)、
      offset(amount=-W)（不会掏空，要用 Locations + Box(mode=Mode.SUBTRACT) 减内腔）

    返回每个零件的包围盒尺寸与体积（mm / cm³），可直接写进结构文档的尺寸表。
    """
    import cad as cad_mod

    if script.strip():
        blocked = _stage_lock("text_to_cad", "20_structure")
        if blocked:
            return blocked
    out_dir = _run_path() / "20_structure"
    ctx = load_appearance_context()
    prompts = ctx.get("prompts") or []
    base_prompt = (appearance_prompt or "").strip()
    if not base_prompt and prompts:
        renders = [p for p in prompts if str(p.get("kind")) == "render"]
        pick = renders[-1] if renders else prompts[-1]
        base_prompt = str(pick.get("prompt") or "")

    # 外观没出图就建模 = 结构工程师重新想象一个产品，同源链路直接断掉。
    # 真实会话里出现过：主智能体并行委派，结构跑完时外观还没出图。
    appearance_ready = bool(base_prompt) or bool(str(ctx.get("appearance_md") or "").strip())
    if not appearance_ready:
        not_ready = (
            "⚠️ 外观阶段还没有产物（10_appearance/ 下没有渲染图，prompts.json 也没有出图记录）。\n"
            "此时建模出来的结构无法保证与外观是同一个产品。\n"
            "请先做这些，再回来建模：\n"
            "1) 确认 industrial-designer 已经完成并落盘了 10_appearance/render-01.png；\n"
            "2) 若外观出图失败，让它先走 render_product_views 兜底出工程视图；\n"
            "3) 在此之前先完成 structure.md 里不依赖几何的部分（材料、壁厚、DFM、装配顺序）。\n"
        )
        if not script.strip():
            return _clip(not_ready + "\n（不带 script 时本工具只做外观约束回传，不带 script 再来一次即可取到模板。）")
        # 带脚本硬建模：放行，但把「未同源」钉进产物，避免后面当成一致成果交付
        emit("tool_progress", name="text_to_cad", detail="外观未就绪，本次建模无法保证同源")

    # 第一步：只问外观约束，让模型按同源信息写脚本（比工具内部偷偷调 LLM 可控得多）
    if not script.strip():
        md = (ctx.get("appearance_md") or "").strip()
        lines = [
            "请提供建模脚本（script）。结构必须与外观同源，约束如下：",
            "",
            "【外观阶段存档的提示词】（出图实际使用的那一版，来自 10_appearance/prompts.json）",
            base_prompt or "（未读到：外观阶段还没出图，请先完成 10_appearance 的渲染图）",
        ]
        if md:
            lines += ["", "【appearance.md 摘要】", md[:600]]
        imgs = ctx.get("images") or []
        if imgs:
            lines += ["", "【外观阶段已产出的渲染图（形状参照物，可在结构文档里引用）】"]
            lines += [f"- {p}" for p in imgs[:4]]
        notes = ctx.get("notes") or {}
        if notes:
            lines += ["", "【外观阶段给出的结构约束】"]
            for k, v in list(notes.items())[:6]:
                lines.append(f"- {k}：{v if isinstance(v, str) else '、'.join(map(str, v))}")
        lines += [
            "",
            "【建模模板（照抄改尺寸，不要自由发挥 API）】",
            "from build123d import *",
            "W, D, H = 100.0, 80.0, 40.0            # 取自外观阶段给出的外形尺寸",
            "WALL, R, BASE_H = 2.0, 8.0, 30.0",
            "with BuildPart() as shell:",
            "    Box(W, D, BASE_H, align=(Align.CENTER, Align.CENTER, Align.MIN))",
            "    fillet(shell.edges().filter_by(Axis.Z), radius=R)",
            "    with Locations((0, 0, WALL)):",
            "        Box(W-2*WALL, D-2*WALL, BASE_H-WALL, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)",
            "parts = {'下壳': shell.part}",
            "",
            "写好后带 script 再次调用本工具；外形尺寸要与上面外观提示词一致。",
        ]
        return _clip("\n".join(lines))

    emit("tool_progress", name="text_to_cad", detail=f"{len(script)} 字符脚本")
    ok, ver = cad_mod.available()
    if not ok:
        return (
            f"CAD 内核不可用：{ver}\n\n"
            "已依次尝试本地 venv 与 cadgen（text-to-cad 官方运行时），修好任一条即可：\n"
            "1) 在 worker 的 venv 里 pip install build123d（推荐，毫秒级）\n"
            "2) 设 AGENT_CAD_RUNTIME=cadgen 并确保 uvx 可用（首次会联网下载）"
        )

    res = cad_mod.build(script, out_dir, base_name=base_name or "model")
    if not res.get("ok"):
        emit("tool_result", name="text_to_cad", detail=(res.get("error") or "")[:400])
        text, fb_parts = _cad_fail_text(res, script, out_dir, base_name)
        if fb_parts:
            parts = cad_mod.load_parts(out_dir)
            made = _write_view_files(parts, f"{base_name} 结构") if parts else []
            total = [r["size_mm"] for r in fb_parts]
            overall = [round(max(t[i] for t in total), 1) for i in range(3)] if total else [0, 0, 0]
            _write_consistency(base_prompt, overall, fb_parts, same_source=appearance_ready)
            if made:
                text += "\n已生成视图：" + "、".join(f"10_appearance/{m}" for m in made)
            text += "\n外观-结构对照已写入 20_structure/consistency.md（兜底尺寸，请核对外观图后修正）。"
        return _clip(text)

    # 脚本存一份，便于迭代（模型常常忘记 save_artifact）
    # 存**平台实际跑通的那份**（可能被自动改写过），不是模型原始的
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        sp = out_dir / "model.py"
        sp.write_text(res.get("script") or script, encoding="utf-8")
        emit("artifact", path="20_structure/model.py", size=sp.stat().st_size, preview="")
    except OSError:
        pass

    for f in res.get("files", []):
        p = out_dir / f
        emit("artifact", path=f"20_structure/{f}", size=p.stat().st_size if p.exists() else 0, preview="")

    # 建模成功就顺手把可视化补齐：四视图 + 爆炸图，避免「模型有、图没有」
    made: list[str] = []
    parts = cad_mod.load_parts(out_dir)
    if parts:
        made = _write_view_files(parts, f"{base_name} 结构")

    total = [r["size_mm"] for r in res.get("parts", [])]
    overall = [round(max(t[i] for t in total), 1) for i in range(3)] if total else [0, 0, 0]
    _write_consistency(base_prompt, overall, res.get("parts", []), same_source=appearance_ready)

    # 尺寸自检：几何畸形时**不置 done**。置了就把阶段锁住，模型发现尺寸错了也改不动
    # （护栏会拦它重做），只能 confirm → 再被拦 → 白烧工具预算。见 _cad_sanity 的注释。
    bad = _cad_sanity(res.get("parts", []), res.get("script") or script)
    if bad:
        lines = [
            "⚠️ 脚本跑通了，但**几何明显畸形**，这份不能当成果交付：",
        ]
        lines += [f"  - {b}" for b in bad]
        lines += [
            "",
            "最常见的原因：用 Sphere / Cylinder 这类**大半径辅助体**做布尔（微拱、圆角），",
            "切开之后辅助体的残余留在零件里，包围盒就虚高一大截。",
            "改法：辅助体的半径按「弦长公式」算，或直接用有界高度的凸台/放样代替球体；",
            "也可以在脚本末尾把超出设计高度的残余再减一次。",
            "",
            "**本阶段没有被锁住**，你不用去请用户确认 —— 直接改脚本重新调用本工具即可。",
            "（只有几何自检通过的建模才会把阶段置为 done 并触发人工闸门。）",
        ]
        return _clip("\n".join(lines))

    _stage_done("20_structure")

    lines = [
        f"建模成功（{ver}），导出 {len(res.get('files', []))} 个文件到 20_structure/：",
    ]
    lines += [
        f"  - {r['name']}：{r['size_mm'][0]}×{r['size_mm'][1]}×{r['size_mm'][2]} mm，体积 {r['volume_cm3']} cm³"
        for r in res.get("parts", [])
    ]
    lines += [f"ℹ️ {n}" for n in (res.get("notes") or [])]
    # 只拿到各零件各自的包围盒，没有装配位置，整体尺寸按各轴包络估算
    lines.append(f"整体外形（按各零件包围盒包络估算）：{overall[0]}×{overall[1]}×{overall[2]} mm")
    if made:
        lines.append("已生成视图：" + "、".join(f"10_appearance/{m}" for m in made))
    lines.append("外观-结构对照已写入 20_structure/consistency.md，请把它核一遍再写 structure.md。")
    return _clip("\n".join(lines))


def _cad_sanity(parts: list, script: str) -> list[str]:
    """建模「成功」但几何明显畸形时，列出问题（空列表 = 通过）。

    为什么要这一步（线上事故）：上盖用 `Sphere(半径 340)` 做微拱，布尔之后包围盒变成
    **100×100×341mm**，工具照样报「建模成功」并把 20_structure 置为 done —— 阶段一锁，
    模型发现尺寸不对也改不了（护栏拦它重做），于是 confirm → 再被拦 → confirm → 再被拦，
    8 次调用全烧在这上面，最后阶段还停在错模型上。**脚本能跑通 ≠ 模型是对的。**

    这里只判「不可能是这个产品」的硬伤，不做审美判断：
      1. 体积 ≤ 0：不是实体，或者布尔把整个零件减没了；
      2. 任一维 > 500mm：桌面设备不会有这么大的零件；
      3. 最长边 > 脚本声明外形最长边的 2.5 倍：辅助体残留的典型症状。
    """
    import cad as cad_mod

    if not parts:
        return ["没有导出任何零件"]
    dims = cad_mod.parse_dims(script or "")
    ref = max(dims.values()) if dims else 0.0
    bad: list[str] = []
    for r in parts:
        name = str(r.get("name") or "?")
        sz = [float(x) for x in (r.get("size_mm") or [0, 0, 0])]
        if float(r.get("volume_cm3") or 0) <= 0:
            bad.append(f"{name} 体积 ≤ 0（不是实体，或布尔把整个零件减没了）")
            continue
        if max(sz) > 500:
            bad.append(f"{name} 包围盒 {sz[0]:.0f}×{sz[1]:.0f}×{sz[2]:.0f}mm，"
                       f"最长边超过 500mm —— 几何失控")
            continue
        if ref and max(sz) > ref * 2.5:
            bad.append(f"{name} 包围盒 {sz[0]:.0f}×{sz[1]:.0f}×{sz[2]:.0f}mm，"
                       f"最长边 {max(sz):.0f}mm 是脚本声明外形（{ref:.0f}mm）的 "
                       f"{max(sz) / ref:.1f} 倍")
    return bad


def _cad_fail_text(res: dict, script: str, out_dir, base_name: str) -> tuple[str, list]:
    """建模失败的统一返回：真实报错在前、参考写法在后，必要时用参数化模板兜底。

    返回 (文本, 兜底成功时的 parts)；没兜住时 parts 为空。
    兜底的意义：结构阶段交不出 STEP/STL，后面硬件板型、固件仿真全都悬着，
    所以脚本写不对时平台按脚本里解析出的尺寸直接建一个壳体，至少把链路续上。
    """
    import cad as cad_mod

    lines = [f"建模失败：{res.get('error', '未知错误')}"]
    if res.get("hint"):
        lines += ["", res["hint"]]
    if res.get("script_path"):
        lines += ["", f"失败脚本已留档：{res['script_path']}（可本地直接运行复现）"]

    dims = cad_mod.parse_dims(script or "")
    if len(dims) >= 3:
        wall = max(1.0, float(dims.get("WALL", 2.0)))
        fb_script = cad_mod.shell_script(
            dims["W"], dims["D"], dims["H"], wall=wall, r=dims.get("R") or None
        )
        fb = cad_mod.build(fb_script, out_dir, base_name=base_name or "model")
        if fb.get("ok"):
            for f in fb.get("files", []):
                p = out_dir / f
                emit("artifact", path=f"20_structure/{f}",
                     size=p.stat().st_size if p.exists() else 0, preview="")
            try:
                sp = out_dir / "model.py"
                sp.write_text(fb_script, encoding="utf-8")
                emit("artifact", path="20_structure/model.py", size=sp.stat().st_size, preview="")
            except OSError:
                pass
            lines += [
                "",
                "⚠️ 你的脚本没跑通，平台已按脚本里解析出的尺寸参数兜底建模（参数化壳体模板），"
                f"外形 {dims['W']}×{dims['D']}×{dims['H']} mm、壁厚 {wall} mm。",
                f"  - 已导出：" + "、".join(fb.get("files", [])),
                "  - 平台脚本已写入 20_structure/model.py：拿它当起点，往上加开窗/开孔/卡扣后重新调用，"
                "不要从零重写（自由发挥 API 最容易再踩同一个坑）。",
            ]
            return "\n".join(lines), fb.get("parts", [])

    lines += ["", "不要原样重发同一段脚本 —— 先按上面的行号与建议改掉出问题的那一行。"]
    return "\n".join(lines), []


def _write_view_files(parts: list, title: str, kind: str = "both") -> list[str]:
    """把四视图与爆炸图写进 10_appearance/。供 text_to_cad 与 render_product_views 共用。"""
    import cad as cad_mod

    dest, err = _safe_target("10_appearance/")
    if dest is None:
        return []
    dest.mkdir(parents=True, exist_ok=True)
    want_views = kind in ("both", "views", "sheet")
    want_exploded = kind in ("both", "exploded", "explode")
    made: list[str] = []
    if want_views:
        svg = cad_mod.views_sheet(parts, title or "结构工程视图")
        if svg:
            p = dest / "views-sheet.svg"
            p.write_text(svg, encoding="utf-8")
            made.append(p.name)
            emit("artifact", path="10_appearance/views-sheet.svg", size=p.stat().st_size, preview="")
    if want_exploded:
        svg = cad_mod.exploded_sheet(parts, title or "装配爆炸视图")
        if svg:
            p = dest / "exploded.svg"
            p.write_text(svg, encoding="utf-8")
            made.append(p.name)
            emit("artifact", path="10_appearance/exploded.svg", size=p.stat().st_size, preview="")
    return made


def _write_consistency(prompt: str, overall: list, parts: list[dict], same_source: bool = True) -> None:
    """外观提示词 vs 实际模型的对照表。一致性不能靠模型自觉，得落成文件。"""
    dest, err = _safe_target("20_structure/consistency.md")
    if dest is None:
        return
    # 外观没出图就建模出来的东西，不能当成「已同源」交付 —— 在文件顶部钉死
    warn = (
        ""
        if same_source
        else (
            "> ⚠️ **本次建模未与外观同源**：建模时 10_appearance/ 下没有渲染图，"
            "也没有出图提示词存档。外形尺寸来自结构侧自行假设，**不能声称与外观图一致**。\n"
            "> 处理：先让 industrial-designer 出图（失败则走 render_product_views 兜底），"
            "再按外观提示词重建模型。\n\n"
        )
    )
    rows = "\n".join(
        f"| {r.get('name','')} | {r['size_mm'][0]}×{r['size_mm'][1]}×{r['size_mm'][2]} | {r['volume_cm3']} |"
        for r in parts
    )
    text = (
        "# 外观-结构一致性对照\n\n"
        + warn
        + "## 外观阶段使用的提示词（同源来源）\n\n"
        f"{prompt or '（未读到）'}\n\n"
        f"## 模型实际外形\n\n整体包围盒：**{overall[0]}×{overall[1]}×{overall[2]} mm**\n\n"
        "| 零件 | 包围盒 (mm) | 体积 (cm³) |\n|---|---|---|\n"
        f"{rows}\n\n"
        "## 待核对项\n\n"
        "- [ ] 外形尺寸与外观阶段宣称的一致（长宽高 ±5%）\n"
        "- [ ] 分件方式与渲染图里的分模线一致\n"
        "- [ ] 开孔/视窗位置与外观图上的可见特征对应\n"
    )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        emit("artifact", path="20_structure/consistency.md", size=dest.stat().st_size, preview="")
    except OSError:
        pass


@tool
def render_product_views(kind: str = "both", title: str = "") -> str:
    """用真实 3D 几何生成工程视图与爆炸图（离线、秒级、不依赖任何图像服务）。

    这是 generate_product_image 的兜底 —— 图像服务不可达或出图失败时，必须调用本工具，
    保证 10_appearance/ 里一定有可视化产物，不能留空。

    kind：views = 四视图图纸（等轴测/主视/侧视/俯视 + 外形尺寸）；
          exploded = 装配爆炸视图（零件分离 + 虚线引导线 + 编号 + 尺寸图例）；
          both = 两个都出（默认）。
    title：可选标题，默认按产品阶段命名。

    数据来源：20_structure/ 下已导出的 .stl（由 build_structure 产出）。
    所以调用顺序是：build_structure 建模成功 → 再调本工具。
    若 20_structure/ 为空，会提示你先建模。

    产物写到 10_appearance/views-sheet.svg 与 10_appearance/exploded.svg。
    """
    import cad

    emit("tool_progress", name="render_product_views", detail=f"kind={kind}")
    struct_dir = _run_path() / "20_structure"
    parts = cad.load_parts(struct_dir) if struct_dir.exists() else []
    if not parts:
        return (
            "20_structure/ 下没有可用的 .stl，无法生成工程视图。"
            "请先调用 build_structure 完成建模并导出 STEP/STL，再调本工具。"
        )

    ok, err = _safe_target("10_appearance/")
    if ok is None:
        return err
    out_dir = ok
    out_dir.mkdir(parents=True, exist_ok=True)

    kind = (kind or "both").strip().lower()
    made = _write_view_files(parts, title or "结构工程视图", kind)
    if not made:
        return "工程视图生成失败：读取到的 STL 三角面为空，请检查 build_structure 的导出结果。"

    total_tris = sum(len(ts) for _, ts in parts)
    result = (
        f"已按真实几何生成 {len(made)} 张图（{len(parts)} 个零件 / {total_tris} 面）："
        + "、".join(f"10_appearance/{m}" for m in made)
        + "。\n四视图含外形尺寸标注；爆炸图含装配引导线与零件编号图例。\n"
        "在文档里用 ![工程视图](views-sheet.svg) 与 ![爆炸图](exploded.svg) 引用。"
    )
    emit("tool_result", name="render_product_views", detail=result[:300])
    return result


def _stage_id(stage: str) -> str:
    """把「20_structure / 结构设计 / 结构」这类写法统一成阶段 ID。"""
    import stages as _st

    sid = (stage or "").strip()
    if not sid:
        return _st.current_stage(_st.stages_done(_st.scan_dir(_run_path()))) or "00_prd"
    for s in _st.STAGES:
        if sid == s["id"] or sid == s["label"] or sid in s["label"]:
            return str(s["id"])
    return re.sub(r"[^0-9a-z_]", "", sid.lower())


@tool
def confirm_with_user(stage: str = "", summary: str = "", files: str = "", rounds: int = 1) -> str:
    """Pause the whole run and wait for a human decision on the finished stage.

    REQUIRED at the end of these two stages (hard gate, downstream stage_gate
    refuses to open the next stage until this is approved):

    - 10_appearance: after the product render / scene / exploded images are done
    - 20_structure: after the CAD model (STEP/STL), exploded view and structure.md are done

    The call BLOCKS until the user clicks "confirm" or sends revision notes in the UI.
    Return value:

    - approved  -> go on to the next stage
    - revise    -> the user's feedback text; redo THIS stage with it, then call this
      tool again to ask for confirmation once more

    stage: 10_appearance / 20_structure (or a Chinese label like 外观设计).
    summary: one short paragraph telling the user what to look at (sizes, files, key decisions).
    files: comma separated artifact paths to show for review; leave empty to auto-collect
      everything under this stage directory.
    rounds: how many times this stage has been sent for review (1 = first review).
    """
    import hitl
    import stages as _st

    import stagestate

    root = _run_path()
    sid = _stage_id(stage)
    label = _st.stage_label(sid)
    picked = [f.strip() for f in re.split(r"[,\n;，；]+", str(files or "")) if f.strip()]
    if not picked:
        picked = [p for p in _st.scan_dir(root) if p.startswith(sid + "/")][:12]
    brief = (summary or "").strip() or f"{label}本阶段产物已生成，请确认是否符合预期。"

    # 去重：同一阶段已经确认过、且这次不是「改完再请确认」的新一轮 → 直接返回。
    # 线上事故：子智能体确认过一次（14.7s），主智能体不知道，又挂起等了一次（24.7s），
    # 用户看到「同一个产品图让我确认两遍」。判据只看 rounds：重做后 rounds 会 +1，
    # 那时必须真的再等一次，不能拿旧记录糊弄过去。
    # 两个状态源都看：_stage.json 是新的阶段台账，_human_review 是旧会话/续跑留下的确认记录。
    # 只看前者的话，续跑一个老会话时会因为台账缺失而把同一份图再请用户确认一次。
    st = stagestate.load(root, sid)
    hr = hitl.state_of(root, sid)
    already = st.get("status") == stagestate.CONFIRMED or bool(hr.get("done"))
    prev_rounds = max(int(st.get("rounds") or 0), int(hr.get("rounds") or 0))
    if already and int(rounds) <= prev_rounds:
        return (
            f"✅ 「{label}」已经在本轮确认过（第 {prev_rounds or 1} 轮），"
            "不需要再请用户确认一次 —— 直接向主智能体回交本阶段结论，进入下一个阶段。\n"
            f"已确认产物：{'、'.join(picked[:6]) or '见 ' + sid + '/ 目录'}"
        )

    run = _run()
    if run is None:
        # 没有运行对象（脚本直调 / 离线测试）：不做阻塞，按确认处理
        hitl.save_record(root, sid, hitl.APPROVE, "无运行上下文，自动放行", picked, rounds, auto=True)
        stagestate.mark_confirmed(root, sid, rounds)
        return f"✅ {label}已标记为确认（当前无运行上下文，未等待人工反馈）。"

    res = hitl.request(run, sid, brief, picked, rounds)
    if res["decision"] == hitl.APPROVE:
        stagestate.mark_confirmed(root, sid, rounds, res.get("feedback", ""))
    else:
        # 用户提了意见：解锁本阶段，允许（且要求）带着反馈重做
        stagestate.mark_revised(root, sid, res.get("feedback", ""), rounds)
    if res["decision"] == hitl.APPROVE:
        out = [
            f"✅ 用户已确认「{label}」，可以进入下游阶段。",
            f"等待时长 {res['waited']} 秒" + ("（超时自动放行）" if res["auto"] else ""),
        ]
        if res["feedback"]:
            out.append(f"用户附言：{res['feedback']}")
        out.append("继续时把本阶段的关键结论（尺寸、外形、文件路径）带进下一步的任务描述。")
        text = "\n".join(out)
    else:
        fb = res["feedback"] or "（用户未填写具体意见，请自行检查本阶段产物的合理性）"
        text = "\n".join(
            [
                f"✋ 用户对「{label}」提出修改意见，本阶段需要重做：",
                "",
                fb,
                "",
                "重做要求：",
                "- 严格按上面的意见改，改完的产物覆盖/新增到本阶段目录，再调用 confirm_with_user 申请二次确认。",
                "- 外观阶段：重出图仍走 generate_product_image 并保持同源链（第一张文生图，其后图生图），不要覆盖已确认的基线图。",
                "- 结构阶段：改 build123d 脚本后重新 text_to_cad，尺寸表必须与 CAD 回传的包围盒一致。",
                "- 上游已确认的结论（如外形尺寸）若被本次修改推翻，要在文档里写明并同步下游。",
            ]
        )
    emit("tool_result", name="confirm_with_user", detail=text[:300])
    return _clip(text, 6000)


@tool
def stage_gate(stage: str = "") -> str:
    """Check whether a design stage may start, and read the constraints handed down from upstream.

    Call this FIRST before starting any stage. It reports whether the upstream
    artifacts exist on disk and returns the cross-stage constraints you must obey:

    - 20_structure: appearance images / prompts (shape source)
    - 30_hardware: shell inner cavity, mounting bosses, height limit -> PCB outline,
      package size, layer count
    - 40_firmware: netlist + BOM -> device functions and pin mapping

    stage: one of 00_prd / 10_appearance / 20_structure / 30_hardware / 40_firmware /
    90_review, or a Chinese label like 硬件设计. Leave empty to check the next
    unfinished stage.
    """
    import hashlib

    import pipeline
    import stages as _st
    import stagestate

    root = _run_path()
    sid = _stage_id(stage)
    # 缓存：一次运行里 stage_gate 会被主智能体和每个子智能体各调一次（实测十几次），
    # 内容一模一样。按「阶段 + 当前产物快照」做 key —— 产物一变 key 就变，
    # 不会出现「刚出完图却拿到「还没有图」的旧结论」这种更糟的情况。
    try:
        snap = hashlib.md5(
            "\n".join(sorted(_st.scan_dir(root))).encode("utf-8", "ignore")
        ).hexdigest()[:10]
    except Exception:  # noqa: BLE001
        snap = ""
    ck = f"{sid}:{snap}"
    hit = _GATE_CACHE.get(ck)
    if hit and config.STAGE_GATE_CACHE_SECONDS > 0:
        age = time.time() - hit[0]
        if age < config.STAGE_GATE_CACHE_SECONDS:
            return f"{hit[1]}\n（{int(age)} 秒前的同名查询，期间产物无变化，结论一致）"
    g = pipeline.gate(root, sid)
    lines = [
        f"【阶段门禁】{g['stage']} {g['label']}：{'✅ 可以开工' if g['ready'] else '⛔ 暂不具备开工条件'}",
    ]
    # 本阶段状态：告诉调用方「这一步是不是已经做过了」。
    # 没有这一行，主智能体与子智能体各持一份上下文，谁都不知道对方做过什么，
    # 于是同一阶段被做两遍（实测 PRD 写两遍、外观图出两遍、产品图被请确认两次）。
    done_files = [p for p in _st.scan_dir(root) if p.startswith(sid + "/")]
    stt = stagestate.load(root, sid)
    status = str(stt.get("status"))
    if status == stagestate.CONFIRMED:
        lines.append(
            f"⚠️ 本阶段**已完成并经用户确认**（{stt.get('rounds') or 1} 轮），产物共 {len(done_files)} 个。"
            "不要再重做、不要再出图/建模、不要再请用户确认 —— 直接向主智能体回交本阶段结论，"
            f"然后进入下一阶段。已有产物：{'、'.join(done_files[:6]) or '见 ' + sid + '/'}"
        )
    elif status == stagestate.DONE:
        lines.append(
            f"ℹ️ 本阶段产物已落盘（{len(done_files)} 个），只差用户确认。"
            "缺哪份文档就补哪份（用 save_artifact 补写），不要整体推倒重来。"
        )
    elif done_files:
        lines.append(
            f"ℹ️ 本阶段目录下已有 {len(done_files)} 个文件，开工前先 ls 看清楚再决定补做什么。"
        )
    stagestate.mark_started(root, sid, "stage_gate")
    if g["upstream"]:
        lines.append(
            "上游：" + "、".join(f"{u['label']}({'已完成' if u['done'] else '未完成'})" for u in g["upstream"])
        )
    for h in (g.get("human_review") or []):
        flag = "✅ 用户已确认" if h.get("done") else "⏸ 等待用户确认"
        lines.append(f"人工确认闸门：{h['label']} —— {flag}")
    if g["missing"]:
        lines.append("缺失：" + "；".join(g["missing"]))
        lines.append("处理：先把缺失产物补齐再开工；确实拿不到就在文档里写明「上游未就绪，本阶段数值为假设」——不许默认沿用臆测值。")
    if g["inputs"]:
        miss_in = [i["path"] for i in g["inputs"] if not i["present"]]
        lines.append("开工前必读：" + "、".join(i["path"] for i in g["inputs"]))
        if miss_in:
            lines.append("（其中未落盘：" + "、".join(miss_in) + "）")
    ctx = g.get("context") or {}
    if ctx.get("appearance") is not None:
        a = ctx["appearance"]
        lines.append(f"外观现状：{a['n_images']} 张图{('，有提示词存档' if a['has_prompts'] else '，无提示词存档')}")
        if a["images"]:
            lines.append("可作为形状来源：" + "、".join(a["images"][:5]))
    if ctx.get("pcb"):
        p = ctx["pcb"]
        if p.get("ok"):
            lines.append(
                f"结构外形 {p['shell_mm'][0]}×{p['shell_mm'][1]}×{p['shell_mm'][2]}mm"
                f" → 板框建议 {p['board_mm'][0]}×{p['board_mm'][1]}mm（面积 {p['board_area_mm2']}mm²）"
            )
            lines.append(f"PCB 约束：{p['hint']}")
        else:
            lines.append(f"PCB 约束：{p['hint']}")
    if ctx.get("hardware"):
        h = ctx["hardware"]
        lines.append(
            f"硬件现状：网表 {h['n_components']} 个器件，BOM {h['bom_rows']} 行"
            + ("；连接端点样例：" + "、".join(h["connections"][:8]) if h["connections"] else "")
        )
    if g["deliverables"]:
        lines.append("本阶段契约产物：" + "、".join(f"{d['file']}（{d['title']}）" for d in g["deliverables"]))
    if g["pass_down"]:
        lines.append("传给下游：" + g["pass_down"])
    result = "\n".join(lines)
    _GATE_CACHE[ck] = (time.time(), result)
    emit("tool_result", name="stage_gate", detail=result[:300])
    return _clip(result)


ALL_TOOLS = [
    search_cirkit_docs,
    save_artifact,
    check_circuit,
    render_schematic,
    emit_kicad_project,
    emit_pcb,
    emit_easyeda,
    export_gerbers,
    build_bom,
    check_arduino,
    generate_product_image,
    build_structure,
    render_product_views,
    text_to_cad,
    stage_gate,
    confirm_with_user,
]
