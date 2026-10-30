"""阶段执行状态机：让「同一阶段被做两遍」在工具层就做不成。

为什么需要它（线上实测）：一次运行里 PRD 被写了两遍、外观图出了两遍、
confirm_with_user 挂起等了三次（14.7s / 101s / 24.7s）。根因不是模型笨，
而是**没有任何地方记录「这一阶段已经做完了」** —— 主智能体与子智能体各拿一份
上下文，谁都不知道对方做过什么，于是各做一遍。

提示词里写「不要重复做」没用（模型在长流程里必然漂移）。所以这里把状态落盘到
`XX_stage/_stage.json`，并让**昂贵工具**在开工前先问一次这个状态：
阶段已 confirmed 且用户没有新提修改意见 → 直接拒绝执行，把已存在的产物路径
回给模型，让它直接往下走。

判据设计上刻意只对「贵且会覆盖产物」的工具上锁（出图 / 建模 / 原理图），
文档类写入（save_artifact / write_file）不锁 —— 补一份漏写的文档是合理的，
重出一张 700 KB 的渲染图不是。

`_stage.json` 下划线开头：`stages.scan_dir` 会排除它，不会混进用户的产物清单。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PENDING = "pending"
WORKING = "working"
DONE = "done"        # 产物已落盘，还没经用户确认
CONFIRMED = "confirmed"
REVISED = "revised"  # 用户提了修改意见，允许（且应该）重做本阶段

_FILE = "_stage.json"

# 会对已落盘产物造成覆盖、或单次开销很大的工具 —— 这些才上锁
EXPENSIVE_TOOLS = {
    "generate_product_image": "10_appearance",
    "render_product_views": "10_appearance",
    "text_to_cad": "20_structure",
    "build_structure": "20_structure",
    "render_schematic": "30_hardware",
    "build_bom": "30_hardware",
    "check_circuit": "30_hardware",
    "check_arduino": "40_firmware",
}

# 廉价工具即使重复也只是浪费预算，不值得为它们挡住模型
_CHEAP = {"stage_gate", "confirm_with_user"}

# 一个阶段本来就要出**多份**产物的工具：外观阶段要连出基线图 / 场景图 / 爆炸图，
# 出完第一张就把阶段锁住的话，后面几张永远出不来（实测：render-02 被拦死）。
# 建模 / 原理图 / BOM 一次就是一份产物，不在这条豁免里 —— 否则换个文件名就能绕过闸门。
_MULTI_OUTPUT = {"generate_product_image", "render_product_views"}


def path_of(root: Path, sid: str) -> Path:
    return Path(root) / sid / _FILE


def load(root: Path, sid: str) -> dict[str, Any]:
    """读阶段状态。没记录 → pending。"""
    try:
        d = json.loads(path_of(Path(root), sid).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  没记录 / 记录坏了都当没做过
        return {"stage": sid, "status": PENDING, "attempts": 0, "confirmed": False}
    if not isinstance(d, dict):
        return {"stage": sid, "status": PENDING, "attempts": 0, "confirmed": False}
    d.setdefault("stage", sid)
    d.setdefault("status", PENDING)
    d.setdefault("attempts", 0)
    d.setdefault("confirmed", False)
    d.setdefault("feedback", "")
    d.setdefault("rounds", 0)
    d.setdefault("blocked", 0)      # 被护栏拦了几次（熔断用）
    return d


def save(root: Path, sid: str, **fields: Any) -> dict[str, Any]:
    d = load(root, sid)
    d.update(fields)
    d["updated"] = time.time()
    d["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        p = path_of(Path(root), sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:  # noqa: BLE001  写不进去只是少了台账，不拖垮流程
        pass
    return d


def mark_started(root: Path, sid: str, agent: str = "") -> dict[str, Any]:
    d = load(root, sid)
    if d["status"] in (CONFIRMED, DONE):
        # 已完成阶段又被开工：只累计次数，不把状态打回 working
        # （否则「确认后再读一次文件」这种无害动作会把已确认状态冲掉）
        return save(root, sid, attempts=int(d.get("attempts") or 0) + 1, agent=agent)
    return save(root, sid, status=WORKING, attempts=int(d.get("attempts") or 0) + 1, agent=agent)


def mark_done(root: Path, sid: str) -> dict[str, Any]:
    d = load(root, sid)
    if d["status"] == CONFIRMED:
        return d
    return save(root, sid, status=DONE)


def mark_confirmed(root: Path, sid: str, rounds: int = 1, feedback: str = "") -> dict[str, Any]:
    # blocked 归零：新的一轮确认，熔断计数不该继承上一轮
    return save(root, sid, status=CONFIRMED, confirmed=True, rounds=int(rounds),
                feedback=feedback or "", blocked=0)


def mark_revised(root: Path, sid: str, feedback: str = "", rounds: int = 1) -> dict[str, Any]:
    """用户提了修改意见：解锁本阶段，允许（且要求）重做。"""
    return save(root, sid, status=REVISED, confirmed=False, feedback=feedback or "", rounds=int(rounds))


def is_confirmed(root: Path, sid: str) -> bool:
    return str(load(root, sid).get("status")) == CONFIRMED


def needs_work(root: Path, sid: str) -> tuple[bool, str]:
    """这个阶段还需不需要干活。返回 (需要, 原因)。"""
    d = load(root, sid)
    st = str(d.get("status"))
    if st == CONFIRMED:
        return False, "本阶段已落盘且经用户确认"
    if st == REVISED:
        return True, f"用户提了修改意见，需要按意见重做：{d.get('feedback') or '（无具体意见）'}"
    if st == DONE:
        return False, "本阶段产物已落盘，只差用户确认"
    return True, "本阶段还没做过"


def guard(root: Path, sid: str, tool: str, files: list[str] | None = None) -> str:
    """昂贵工具开工前的幂等闸。返回空串 = 放行，非空 = 拒绝理由（直接回给模型）。

    放行例外：
    1. 状态是 pending / working / revised（用户要求改）—— 重做是正确行为。
    2. **熔断**：同一状态连拦 N 次仍坚持要干 —— 说明模型确信产物有客观错误。
       继续拦下去就是死循环（实测：confirm → 被拦 → confirm → 被拦，8 次调用白烧）。
       所以拦满次数就放行一次，并把状态退回一档，让它做完还能正常收尾。

    为什么要有熔断：护栏的目的是「别把已确认的东西重做一遍」，不是「把模型锁死」。
    模型自己发现产物错了（比如建模出来 341mm 的上盖）时，必须给它一条修正的路，
    否则它只有两条路：一直撞墙，或者带着错产物往下走。
    """
    if tool in _CHEAP:
        return ""
    d = load(root, sid)
    st = str(d.get("status"))
    if st in (PENDING, WORKING, REVISED):
        return ""

    # 补齐**新**产物不算重做：目标文件在磁盘上一个都不存在，这次不会覆盖任何已落盘的东西。
    # 实测事故：外观阶段出完 render-01 就把阶段置成 done，结果第二张图 render-02
    # 撞上「本阶段已落盘，请先请用户确认」被拦死 —— 一次出图把整个阶段锁住了。
    # 出图侧已改为「契约产物齐了才置 done」，这里再兜一层：只要是新文件就放行。
    if st == DONE and tool in _MULTI_OUTPUT and files:
        try:
            _root = Path(root)
            if not any(f and (_root / f).is_file() for f in files):
                return ""
        except OSError:
            pass

    shown = "、".join((files or [])[:6])
    blocked = int(d.get("blocked") or 0)
    # 已确认过的要更谨慎：多拦一次
    limit = 2 if st == CONFIRMED else 1
    if blocked >= limit:
        # 放行一次自我纠错：状态退回一档，做完会重新走确认流程
        save(root, sid, blocked=0, status=(DONE if st == CONFIRMED else WORKING))
        return ""
    save(root, sid, blocked=blocked + 1)
    left = limit - blocked
    again = f"\n（如果你确信这是客观错误必须改：再调用一次本工具就会被放行 —— 本闸最多再拦 {left} 次。）"

    if st == CONFIRMED:
        return (
            f"⛔ {sid} 本阶段产物**已经生成并经用户确认**，不要再调用 {tool} 重做一遍。"
            + (f"\n已有产物：{shown}" if shown else "")
            + "\n重复执行会覆盖已确认的产物、烧掉工具预算，并把同一份东西再请用户确认一次。"
            "\n正确做法：直接向主智能体回交本阶段结论，进入下一个阶段。"
            "\n只有用户通过 confirm_with_user 明确提出修改意见后，才可以重做本阶段。"
            + again
        )
    # status == DONE：产物有了但还没确认。放行会覆盖，挡住并要求先去确认
    return (
        f"⛔ {sid} 本阶段产物已经落盘（{shown or '见工作区'}），但**还没经用户确认**。"
        f"\n请先调用 confirm_with_user(stage=\"{sid}\") 请用户确认，确认通过后再决定要不要重做。"
        "\n直接重做会让「用户看到的图」和「结构拿去建模的图」变成两代不同的产品。"
        + again
    )
