"""人工确认闸门（human-in-the-loop）。

为什么必须有这道闸：
- 外观图「长什么样」只有人看了才算数 —— 模型自己判断「这张挺好」，下一秒就照着它
  去建结构、定板型，等六阶段跑完才发现审美/比例不对，返工成本是整条链。
- 结构同理：壁厚、分件、能不能装，人一眼能看出模型看不出的荒唐。

所以这两个阶段做完要**停下来等人**：确认就放行下游，有意见就带着反馈重做本阶段。
闸门做成硬门禁（下游 stage_gate 会查确认记录），不指望模型「记得先问一句」。

等待期间的处理有三条容易踩的坑，都已处理：
1. **不消耗运行时限** —— 等人不是空转，护栏在等待期直接放行，恢复时把时钟往后拨。
2. **可持久化** —— 确认结果写进阶段目录的 JSON，重启 / 续跑后查得到。
3. **不会无限挂死** —— 超过 HUMAN_WAIT_SECONDS 按自动放行处理并记进记录。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import config

APPROVE = "approve"
REVISE = "revise"


# ---------------------------------------------------------------- 确认记录（落盘）
def review_path(root: Path, stage: str) -> Path:
    return Path(root) / stage / config.HUMAN_REVIEW_FILE


def state_of(root: Path, stage: str) -> dict[str, Any]:
    """读某阶段的人工确认记录。没确认过 → {"done": False}。"""
    p = review_path(Path(root), stage)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  没记录 / 记录坏了都算「未确认」
        return {"stage": stage, "decision": "", "done": False}
    if not isinstance(d, dict):
        return {"stage": stage, "decision": "", "done": False}
    d.setdefault("stage", stage)
    d["done"] = str(d.get("decision") or "") == APPROVE
    return d


def approved(root: Path, stage: str) -> bool:
    return bool(state_of(root, stage).get("done"))


def save_record(
    root: Path,
    stage: str,
    decision: str,
    feedback: str = "",
    files: list[str] | None = None,
    rounds: int = 1,
    auto: bool = False,
) -> Path:
    """把确认结论写进阶段目录。JSON 而非 md：门禁要读，机器可读优先。"""
    d = {
        "stage": stage,
        "decision": APPROVE if decision == APPROVE else REVISE,
        "decision_text": "确认通过" if decision == APPROVE else "需修改",
        "feedback": (feedback or "").strip(),
        "files": list(files or []),
        "rounds": int(rounds),
        "auto": bool(auto),
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = review_path(Path(root), stage)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:  # noqa: BLE001  写不进去只是少了持久化，不拖垮流程
        pass
    return p


# ---------------------------------------------------------------- 等待人工反馈
def _clear(run: Any) -> None:
    run.awaiting = None
    run.awaiting_event = None
    run.awaiting_at = 0.0
    run.awaiting_answer = None


def request(run: Any, stage: str, summary: str = "", files: list[str] | None = None, rounds: int = 1) -> dict[str, Any]:
    """挂起当前运行，等用户确认。返回 {"decision","feedback","auto","waited"}。

    只能在智能体的工作线程里调用（会阻塞）。等待期间不消耗运行时限 —— 那是等人类，
    不是空转，用时钟去砍它属于误伤。
    """
    files = [str(f) for f in (files or [])][:24]
    if not config.HUMAN_GATE:
        save_record(run.dir, stage, APPROVE, "闸门已关闭（AGENT_HUMAN_GATE=0），自动放行", files, rounds, auto=True)
        return {"decision": APPROVE, "feedback": "", "auto": True, "waited": 0.0}

    ev = threading.Event()
    run.awaiting = {
        "stage": stage,
        "label": "",
        "summary": (summary or "").strip(),
        "files": files,
        "rounds": rounds,
        "asked_at": time.time(),
    }
    run.awaiting_event = ev
    run.awaiting_at = time.time()
    run.awaiting_answer = None

    try:
        run.push(
            {
                "type": "awaiting",
                "stage": stage,
                "summary": run.awaiting["summary"],
                "files": files,
                "rounds": rounds,
                "wait_seconds": int(config.HUMAN_WAIT_SECONDS),
            }
        )
    except Exception:  # noqa: BLE001  推事件失败（比如已被取消）也要能继续下面的等待判定
        pass

    waited = 0.0
    step = max(1.0, float(config.HUMAN_BEAT_SECONDS))
    try:
        while waited < config.HUMAN_WAIT_SECONDS:
            ev.wait(step)
            # 取消的判据要放在「唤醒之后」再看一次：用户点停止时事件是被立刻 set 的，
            # 若只在循环开头判 cancel，事件先到就把这轮跳过了（表现为停止后仍在等人）。
            if run.cancel_requested:
                run.awaiting_answer = {"decision": APPROVE, "feedback": "", "auto": True, "reason": "用户已停止本次运行"}
                break
            if run.awaiting_answer is not None:
                break
            waited = time.time() - run.awaiting_at
        else:
            run.awaiting_answer = {
                "decision": APPROVE,
                "feedback": "",
                "auto": True,
                "reason": f"等待人工反馈超时（{int(config.HUMAN_WAIT_SECONDS)} 秒），自动放行",
            }
    finally:
        ans = run.awaiting_answer or {
            "decision": APPROVE,
            "feedback": "",
            "auto": True,
            "reason": "等待中断，自动放行",
        }
        waited = time.time() - run.awaiting_at
        _clear(run)
        # 等人不算运行时间：把起点与「上次进展」一起往后拨，护栏的时限/停滞判据才不会被误触发
        try:
            run.shift_clock(waited)
        except Exception:  # noqa: BLE001
            pass

    decision = APPROVE if ans.get("decision") == APPROVE else REVISE
    feedback = str(ans.get("feedback") or "")
    if ans.get("reason"):
        feedback = (feedback + ("；" if feedback else "") + str(ans["reason"])).strip("；")
    auto = bool(ans.get("auto"))
    save_record(run.dir, stage, decision, feedback, files, rounds, auto=auto)
    try:
        run.push(
            {
                "type": "reviewed",
                "stage": stage,
                "decision": decision,
                "feedback": feedback[:600],
                "auto": auto,
            }
        )
    except Exception:  # noqa: BLE001
        pass
    return {"decision": decision, "feedback": feedback, "auto": auto, "waited": round(waited, 1)}


def respond(run: Any, decision: str, feedback: str = "") -> bool:
    """用户（或前端）给出结论，唤醒等待中的线程。"""
    pend = getattr(run, "awaiting", None)
    if not pend:
        return False
    dec = APPROVE if str(decision).strip().lower() in ("approve", "ok", "确认", "通过", "1", "true") else REVISE
    run.awaiting_answer = {"decision": dec, "feedback": (feedback or "").strip()}
    # 记录先落盘：即使智能体线程已经不在了，门禁与前端也看得到这次结论
    try:
        save_record(
            run.dir,
            str(pend.get("stage") or ""),
            dec,
            feedback,
            list(pend.get("files") or []),
            int(pend.get("rounds") or 1),
        )
    except Exception:  # noqa: BLE001
        pass
    ev = getattr(run, "awaiting_event", None)
    if ev is not None:
        ev.set()
    return True


def pending_of(run: Any) -> dict[str, Any] | None:
    """给前端查询用：当前有没有在等人的阶段。"""
    pend = getattr(run, "awaiting", None)
    if not pend:
        return None
    return {
        "run_id": getattr(run, "id", ""),
        "stage": pend.get("stage") or "",
        "summary": pend.get("summary") or "",
        "files": list(pend.get("files") or []),
        "rounds": int(pend.get("rounds") or 1),
        "asked_at": pend.get("asked_at") or 0.0,
        "waited": round(time.time() - float(getattr(run, "awaiting_at", 0.0) or 0.0), 1),
        "wait_seconds": int(config.HUMAN_WAIT_SECONDS),
    }
