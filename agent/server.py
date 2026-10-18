"""Agent Worker：FastAPI + SSE，把智能体运行过程实时推给前端。

无 SILICONFLOW_API_KEY 或 AGENT_MOCK=1 时自动进入演示模式，UI 依旧完整可跑。
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import config
import paradigms
import store

app = FastAPI(title="Product Design Agent Worker", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_EVENTS = 5000
# 高频流式事件：逐条 SSE 下发（打字机手感），但不逐条进数据库 —— 一个 30 分钟的
# run 能产出 33 万条 thinking，全量落库会把库撑爆、历史列表也全是噪声。
STREAM_TYPES = {"thinking", "token"}


class ModelSettings(BaseModel):
    """前端传入的运行时模型配置。密钥只在内存中使用，绝不落盘、不写日志。"""

    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.6
    thinking: bool = True
    thinking_body: dict[str, Any] | str | None = None


def _tool_path(name: str, args: str) -> str:
    """从工具参数里抠出被操作的文件路径，用于把「反复读同一文件」聚合成一个签名。

    参数是流式拼出来的，可能为空串 / 半截 JSON / 占位 `{}`，解析失败一律返回空。
    """
    s = (args or "").strip()
    if not s.startswith("{"):
        return ""
    try:
        d = json.loads(s)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(d, dict):
        return ""
    for k in ("file_path", "path", "filename"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().replace("\\", "/")
    return ""


class Run:
    def __init__(
        self,
        requirement: str,
        settings: dict[str, Any] | None = None,
        forced_paradigm: str = "",
        resume_dir: Path | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.requirement = requirement
        # 续跑会往 requirement 里追加「已落盘产物清单」，再续跑就会一层层叠加。
        # 这里留住最初那句需求，续跑提示词永远基于它生成。
        self.base_requirement = requirement
        self.settings = settings or {}
        self.created = time.time()
        # 续跑：复用上一轮的目录，已有产物直接作为下游阶段的输入
        self.dir = resume_dir if resume_dir else config.WORKSPACE / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.resumed_from = str(resume_dir.name) if resume_dir else ""
        self.events: list[dict[str, Any]] = []
        self.q: queue.Queue[dict[str, Any]] = queue.Queue()
        self.done = False
        self.lock = threading.Lock()
        # 事件持久化：seq 是入库主键的一部分，重放时靠它续传
        self.seq = 0
        self.started = self.created
        # 落库状态由执行体填写，worker 收尾时统一提交
        self.db_status = "done"
        self.db_summary = ""
        self.db_error = ""
        # ---- 护栏状态 ----
        self.gateway_state = ""         # 开跑前的网关判定："" / "quota" / "unreachable"
        self.cancel_requested = False   # 前端「停止生成」
        self.cancel_at = 0.0            # 取消请求发出的时刻，看门狗据此判断卡了多久
        self.abort_reason = ""          # 被中止时记下原因，收尾写进 error 字段
        self.tool_calls = 0
        self.call_sig: dict[tuple[str, str], int] = {}
        # 预算按代价加权：read_file 这类廉价只读调用不该和出图/CAD 等价消耗预算
        self.tool_weight = 0.0
        # 「停滞」判据用的进展时刻：只要还在出产物/拿到有效结果，重复读文件就是正常工作
        self.last_progress = self.created
        self.progress_marks = 0
        # 已达到阈值但没停滞的工具：只提示一次，且不重复刷屏
        self.warned_tools: set[str] = set()
        # 目标达成判据：以落盘产物为准（不信模型自觉回写），齐全后给一段收尾宽限期
        self.artifacts: set[str] = set()
        self.goal_at = 0.0
        # 磁盘扫描节流：模型常绕过 save_artifact 直接 write_file，只认事件会让判据永远不齐
        self._last_scan = 0.0
        # 内存事件列表有上限；被裁掉的条数要记着，否则 SSE 游标会错位（表现为重复推送/丢事件）
        self.events_base = 0
        # thinking/token 聚合缓冲：按 agent+type 分桶，攒够窗口再写一条进库
        self.agg: dict[str, dict[str, Any]] = {}
        # ---- 决策范式（Orchestrator 路由结果）----
        # 进化基因：同类需求历史失败过几次 → 决定要不要多加一层反思
        try:
            hits = store.recall(requirement, limit=5)
            history_fail = sum(1 for h in hits if (h.get("status") or "") not in ("done", ""))
        except Exception:  # noqa: BLE001  检索失败按「没踩过坑」处理，不影响选型
            history_fail = 0
        self.paradigm = paradigms.route(
            requirement, history_fail=history_fail, forced=forced_paradigm or config.PARADIGM
        )
        # 显式设了 AGENT_MAX_SECONDS / AGENT_TOOL_BUDGET 就当作绝对硬上限压过档位值
        self.max_seconds = min(self.paradigm.max_seconds, config.HARD_MAX_SECONDS) if config.HARD_MAX_SECONDS else self.paradigm.max_seconds
        # 软时限之上的硬上限：到点还没跑完就不再续期（交给自动续跑兜底）
        self.hard_seconds = config.RUN_HARD_SECONDS or self.max_seconds * 2
        self.timeout_extends = 0        # 已自动续期次数
        self.timeout_warned = False     # 时限预警只推一次
        # 自动续跑：由上一轮超时中止的 run 触发，复用同一个工作区目录接着做
        self.auto_resume_count = 0
        self.auto_resume_of = ""
        self.auto_resumed = False   # 本轮是否已经派过续跑，防止重复开新会话
        # ---- 人工确认闸门 ----
        # 外观/结构做完要停下来等人看。等待期不是空转：护栏放行、时钟后拨（见 shift_clock）
        self.awaiting: dict[str, Any] | None = None    # 正在等人的阶段信息
        self.awaiting_event: threading.Event | None = None
        self.awaiting_answer: dict[str, Any] | None = None
        self.awaiting_at = 0.0
        self.tool_budget = min(self.paradigm.tool_budget, config.HARD_TOOL_BUDGET) if config.HARD_TOOL_BUDGET else self.paradigm.tool_budget
        self.n_reflexions = 0
        self.goal_hit = False
        store.create_run(self.id, requirement, self.settings)

    # ---------------- 事件推送 ----------------
    def push(self, ev: dict[str, Any]) -> None:
        """所有事件的唯一入口：先过护栏，再决定「逐条 SSE + 聚合落库」。

        会抛 guards.RunAborted（取消/超时/超预算/目标达成），由上层中断整条流。
        """
        # 覆盖而不是 setdefault：聚合类事件（thinking / token）自带 payload.ts，
        # 那是**首帧**时间，比它后面已经推走的事件还早 —— 实测出现过 -4.4s 的负间隔，
        # 前端按 ts 排序就会把思考内容插到上一条之前。统一以「入队时刻」为准，
        # 原始时间留在 ts_model 里备用。
        if "ts" in ev:
            ev["ts_model"] = ev["ts"]
        ev["ts"] = time.time()
        etype = str(ev.get("type") or "")

        # 产物先记账再过护栏：目标达成的判据就是这些路径
        if etype == "artifact" and ev.get("path"):
            self.artifacts.add(str(ev["path"]).replace("\\", "/"))
        # 写类工具落盘同样计入（磁盘是权威）：否则「六阶段齐全」永远判不出来
        if etype in ("artifact", "tool_end"):
            self._maybe_scan_goal()
        self._guard(etype, ev)
        if etype == "artifact":
            self._check_goal()

        with self.lock:
            self.events.append(ev)
            if len(self.events) > MAX_EVENTS:
                dropped = len(self.events) - MAX_EVENTS
                del self.events[:dropped]
                self.events_base += dropped
            seq = self.seq
            self.seq += 1

        if etype in STREAM_TYPES:
            # 高频流式事件：SSE 照推（前端打字机不变），落库走聚合
            self._agg(etype, ev)
        else:
            # 关键事件落库前先把聚合刷掉，保证重放时的先后顺序可读
            self._flush_agg()
            store.append(self.id, seq, ev)
        self.q.put(ev)

    def shift_clock(self, seconds: float) -> None:
        """把已用时间往回拨：等待人工反馈 / 限流退避这段时间不该算进运行时限。

        不拨的话会出现「用户看了 10 分钟图，回来点确认，护栏立刻判超时中止」这种荒唐结果。
        """
        s = max(0.0, float(seconds or 0.0))
        self.started += s
        self.last_progress += s
        if self.goal_at:
            self.goal_at += s

    def _guard(self, etype: str, ev: dict[str, Any]) -> None:
        """护栏：取消 / 总时限 / 工具预算 / 重复调用。

        重复调用的判定刻意做得很保守 —— 误杀的代价远大于漏判：
        判错了会把一次本来能交付的运行砍成「超出预算中止」，而真循环最多多烧一点 token。
        所以必须同时满足「同一签名够多次」+「期间没有任何进展」才动手。
        """
        import guards

        # 已经中止过：放行最后那几条收尾事件（warn/done），否则前端看不到结束提示
        if self.abort_reason:
            return

        if self.cancel_requested:
            self.abort_reason = "用户手动停止"
            raise guards.RunCancelled(self.abort_reason)

        # 正在等人确认（外观/结构）：等待不是空转 —— 时限、停滞、工具预算一律放行。
        # 时钟由 shift_clock 在恢复时后拨，否则「用户看了十分钟图再点确认」会被判超时。
        if self.awaiting:
            return

        # 目标达成后的收尾宽限期：产物齐了还一直跑，多半是在反复「再优化一版」
        if self.goal_at and (time.time() - self.goal_at) > config.GOAL_GRACE_SECONDS:
            self.abort_reason = (
                f"六阶段产物已齐全，收尾宽限 {int(config.GOAL_GRACE_SECONDS)} 秒后按目标达成交付"
            )
            raise guards.RunGoal(self.abort_reason)

        over = time.time() - self.started
        # 接近时限先预警（只推一次），让前端/用户知道这轮跑得偏久
        if not self.timeout_warned and over > self.max_seconds * config.TIMEOUT_WARN_RATIO:
            self.timeout_warned = True
            self.push(
                {
                    "type": "warn",
                    "message": (
                        f"已用掉 {int(over)} 秒（档位时限 {int(self.max_seconds)} 秒）的 "
                        f"{int(over / max(self.max_seconds, 1) * 100)}%；只要还在产出新内容就会自动延长时间。"
                    ),
                }
            )
        if over > self.max_seconds:
            idle = time.time() - self.last_progress
            # 到点不等于失败：**停滞**才是失败。还在往前推进就自动续期，
            # 否则一次「图还在出、产物还在落」的正常长跑会被时钟砍断（实测 904s 那次）。
            if over < self.hard_seconds and idle <= config.STALL_SECONDS:
                new_limit = min(over + config.TIMEOUT_EXTEND_SECONDS, self.hard_seconds)
                if new_limit > self.max_seconds:
                    old = self.max_seconds
                    self.max_seconds = new_limit
                    self.timeout_extends += 1
                    self.timeout_warned = False  # 新窗口重新计预警
                    self.push(
                        {
                            "type": "warn",
                            "message": (
                                f"已达档位时限 {int(old)} 秒，但 {int(idle)} 秒内仍在产出新内容，"
                                f"自动延长至 {int(self.max_seconds)} 秒（第 {self.timeout_extends} 次，"
                                f"硬上限 {int(self.hard_seconds)} 秒）。"
                            ),
                        }
                    )
                    return
            why: list[str] = []
            if idle > config.STALL_SECONDS:
                why.append(f"{int(idle)} 秒没有新进展")
            if over >= self.hard_seconds:
                why.append(f"已达硬上限 {int(self.hard_seconds)} 秒")
            self.abort_reason = (
                f"{self.paradigm.label} 档位时限 {int(self.max_seconds)} 秒已到（已跑 {int(over)} 秒"
                + ("，" + "、".join(why) if why else "")
                + "）"
            )
            raise guards.RunTimeout(self.abort_reason)

        # 进展记账：新产物 / **写类**工具成功都算「还在往前走」。
        # 只读工具不算 —— 它幂等、无副作用，把它的返回内容当进展，等于给
        # 「反复回读自己刚写的报告」发免死金牌（实测 37 次 read_file 不肯收尾）。
        read_only_end = (
            etype == "tool_end" and str(ev.get("name") or "") in config.READ_ONLY_TOOLS
        )
        progressed = etype == "artifact" or (
            etype == "tool_end"
            and bool(str(ev.get("result") or "").strip())
            and not read_only_end
        )
        if progressed:
            self.last_progress = float(ev.get("ts") or time.time())
            self.progress_marks += 1

        if etype != "tool_start":
            return

        name = str(ev.get("name") or "")
        args = str(ev.get("args") or "")
        self.tool_calls += 1
        cheap = name in config.READ_ONLY_TOOLS or name in config.CHEAP_TOOLS
        self.tool_weight += config.TOOL_WEIGHT_READ if cheap else 1.0

        if self.tool_weight > self.tool_budget:
            self.abort_reason = (
                f"{self.paradigm.label} 档位工具预算 {self.tool_budget} 次已用尽，为避免无效循环自动中止"
            )
            raise self._abort_signal(self.abort_reason)

        sig = (self._sig_of(name, args), "")
        n = self.call_sig.get(sig, 0) + 1
        self.call_sig[sig] = n
        limit = self._repeat_limit(name, args)
        if n < limit:
            return

        # 达到次数不等于空转：只要这段时间还在出东西，就只提示不中止。
        # 提示按工具名去重且全局最多 3 条 —— 委派子智能体这类调用天然高频，
        # 每换一组参数就提示一次会把核心过程刷成一片黄条，反而盖住真正的问题。
        if (time.time() - self.last_progress) <= config.STALL_SECONDS:
            if name not in self.warned_tools and len(self.warned_tools) < 3:
                self.warned_tools.add(name)
                self.push(
                    {
                        "type": "warn",
                        "message": (
                            f"{name} 已重复 {n} 次，但期间仍在产出新内容，按正常多轮调用放行"
                        ),
                    }
                )
            return

        self.abort_reason = (
            f"{name} 重复 {n} 次且 {int(config.STALL_SECONDS)} 秒内没有任何新进展，判定为无效循环并中止"
        )
        raise self._abort_signal(self.abort_reason)

    def _sig_of(self, name: str, args: str) -> str:
        """去重签名。只读工具按「文件」聚合而不是按「文件+offset」——
        模型回读自己的报告时每次 offset 都不同，按原文签名永远凑不出重复，
        于是「反复读同一文件」这种最典型的空转反而漏判。"""
        if name not in config.READ_ONLY_TOOLS:
            return f"{name}\x00{args[:160]}"
        p = _tool_path(name, args)
        return f"{name}\x00{p}" if p else f"{name}\x00"

    def _repeat_limit(self, name: str, args: str) -> int:
        """同一签名允许重复多少次才值得警惕 —— 按调用代价分档。"""
        if name in config.READ_ONLY_TOOLS:
            # 同一个文件反复读：比普通只读更该警惕，限额压低
            if _tool_path(name, args):
                return config.REPEAT_LIMIT_SAMEFILE
            return config.REPEAT_LIMIT_READ
        if not args.strip():
            # 参数没抓到（网关不逐块下发 / 工具无参），签名退化成工具名，证据不足
            return config.REPEAT_LIMIT_NOARG
        return config.REPEAT_LIMIT

    def _abort_signal(self, reason: str) -> "Exception":
        """按已交付程度决定中止的「定性」。

        产物已经凑得差不多时，重复调用只是收尾阶段的碎动作 —— 这时应该按目标达成
        交付，而不是把它渲染成「超出预算中止」，否则用户看到的就是
        「东西都在了，却告诉我跑失败了」。
        """
        import guards
        import stages

        try:
            prog = stages.stage_progress(self.artifacts)
        except Exception:  # noqa: BLE001
            prog = {}
        done = int(prog.get("done") or 0)
        if done >= config.GOAL_MIN_STAGES:
            self.goal_hit = True
            return guards.RunGoal(
                f"{reason}；已产出 {done}/{prog.get('total', 6)} 个阶段产物，按当前进度交付"
            )
        return guards.RunBudget(reason)

    def _check_goal(self) -> None:
        """六阶段产物齐全 → 记下时刻，进入收尾宽限期。

        判据走 stages.stage_progress（与前端 plan.ts 同一套），不读模型写的 todo ——
        实测模型常常只在开头写一次清单，之后做到哪都不回写。
        """
        if self.goal_at or not self.artifacts:
            return
        import stages

        try:
            prog = stages.stage_progress(self.artifacts)
        except Exception:  # noqa: BLE001  判据出错只是少一次提前收尾，不影响主流程
            return
        if not prog.get("finished"):
            return
        self.goal_at = time.time()
        self.goal_hit = True
        self.push({"type": "goal_reached", "grace": int(config.GOAL_GRACE_SECONDS), "progress": prog})

    def _maybe_scan_goal(self) -> None:
        """磁盘是产物的唯一权威：节流扫描 run 目录，把落盘产物并入目标判据。

        为什么不能只认 `artifact` 事件：那个事件只有 `save_artifact` 会发，而子智能体
        更常用 `write_file` / `edit_file` 直接落盘。实测一次运行磁盘上六阶段 28 个文件
        全齐，事件里却只有 1 个 artifact —— 于是「六阶段齐全」的判据永远判不出来，
        规划显示 6/6 完成、任务却一直转，最后被用户手动掐掉。
        """
        now = time.time()
        if now - self._last_scan < config.DISK_SCAN_SECONDS:
            return
        self._last_scan = now
        try:
            import stages

            found = set(stages.scan_dir(self.dir))
        except Exception:  # noqa: BLE001  扫描失败只是少一次提前收尾，不影响主流程
            return
        if not found:
            return
        fresh = found - self.artifacts
        if fresh:
            self.artifacts |= fresh
            self.last_progress = now
            self.progress_marks += len(fresh)
        self._check_goal()

    def _agg(self, etype: str, ev: dict[str, Any]) -> None:
        key = f"{etype}\x00{ev.get('agent') or ''}"
        now = float(ev.get("ts") or time.time())
        with self.lock:
            rec = self.agg.get(key)
            if rec is None:
                rec = {"text": "", "n": 0, "first": now}
                self.agg[key] = rec
            rec["text"] += str(ev.get("text") or "")
            rec["n"] += 1
            due = (now - rec["first"]) >= config.AGG_SECONDS or len(rec["text"]) >= config.AGG_CHARS
        if due:
            self._flush_agg(key)

    def _flush_agg(self, key: str | None = None) -> None:
        rows: list[tuple[int, dict[str, Any]]] = []
        with self.lock:
            for k in [key] if key else list(self.agg):
                rec = self.agg.pop(k, None)
                if not rec or not rec["text"]:
                    continue
                etype, _, agent = k.partition("\x00")
                seq = self.seq
                self.seq += 1
                rows.append(
                    (
                        seq,
                        {
                            "type": etype,
                            "agent": agent,
                            "text": rec["text"][: config.AGG_CHARS * 3],
                            "agg": True,
                            "n": rec["n"],
                            # 落库时间必须用**刷出时刻**而不是首帧时间：
                            # 聚合事件是攒够 AGG_SECONDS 才写的，它的 seq 比这期间推走的所有
                            # 事件都大，ts 却是几秒甚至二十几秒之前 —— 实测出现 -21.5s 的负间隔，
                            # 前端按 ts 排序就会把整段思考插到上一条之前。首帧时间留作 ts_first。
                            "ts": time.time(),
                            "ts_first": rec["first"],
                        },
                    )
                )
        for seq, payload in rows:
            store.append(self.id, seq, payload)

    def cancel(self) -> None:
        self.cancel_requested = True
        self.cancel_at = time.time()
        # 正在等人确认：立刻唤醒它，否则用户点了停止还得再等上一轮心跳才响应
        ev = getattr(self, "awaiting_event", None)
        if ev is not None:
            ev.set()

    def cancel_expired(self) -> bool:
        """取消已发出、但智能体迟迟没有走到下一个事件点（堵在模型长连接上）。"""
        return bool(self.cancel_requested) and (
            time.time() - self.cancel_at > config.CANCEL_GRACE_SECONDS
        )

    def extend_deadline(self, seconds: float) -> None:
        """把总时限往后推。

        退避等待期间智能体并没有在跑，不该算进「已用时间」—— 否则限流退避
        反而会把自己跑进超时中止，等于因为等得太久而被判超时。
        """
        self.started += seconds


RUNS: dict[str, Run] = {}


# ---------------------------------------------------------------- 演示模式
DEMO_STEPS: list[tuple[str, dict[str, Any]]] = [
    (
        "todo",
        {
            "items": [
                {"content": "需求分析与规格定义", "status": "in_progress"},
                {"content": "外观设计方案", "status": "pending"},
                {"content": "结构设计与 DFM", "status": "pending"},
                {"content": "硬件原理图与 BOM", "status": "pending"},
                {"content": "固件开发与仿真", "status": "pending"},
                {"content": "交叉评审", "status": "pending"},
            ]
        },
    ),
    (
        "thinking",
        {
            "agent": "main",
            "text": "用户要的是一台桌面级温湿度监测小夜灯。先定三件事：\n"
            "1) 传感链路 —— DHT11 便宜但精度 ±2℃、采样间隔 ≥1s；SHT30 用 I2C 更稳但贵一倍。成本约束 80 元内，选 DHT11。\n",
        },
    ),
    # 流程记账类动作：真实运行里这些会成对出现十几次，用来演示「核心过程」视图的折叠
    ("tool_start", {"agent": "main", "name": "write_todos", "args": '[{"content":"需求分析与规格定义","status":"in_progress"}]'}),
    ("tool_end", {"agent": "main", "name": "write_todos", "result": "任务清单已更新（6 项）"}),
    ("tool_start", {"agent": "main", "name": "ls", "args": "/"}),
    ("tool_end", {"agent": "main", "name": "ls", "result": "AGENTS.md"}),
    ("tool_start", {"agent": "main", "name": "read_file", "args": "/AGENTS.md"}),
    ("tool_end", {"agent": "main", "name": "read_file", "result": "（已读取长期记忆）"}),
    ("tool_start", {"agent": "main", "name": "write_todos", "args": '[{"content":"需求分析与规格定义","status":"completed"}]'}),
    ("tool_end", {"agent": "main", "name": "write_todos", "result": "任务清单已更新（6 项）"}),
    (
        "thinking",
        {
            "agent": "main",
            "text": "2) 显示方案 —— 0.96 寸 SSD1306 OLED 是成本最优解，I2C 只占两线，和 DHT11 不冲突。\n"
            "3) 交互 —— 一个轻触键切页，加光敏电阻做夜间自动点灯。\n"
            "4) 供电 —— USB 5V 直供，省掉锂电与充电管理，成本再降 12 元。先建任务清单，再逐阶段委派。",
        },
    ),
    ("token", {"agent": "main", "text": "收到需求，开始拆解。"}),
    ("tool_start", {"agent": "requirements-analyst", "name": "save_artifact", "args": "00_prd/prd.md"}),
    ("tool_end", {"agent": "requirements-analyst", "name": "save_artifact", "result": "已写入 00_prd/prd.md"}),
    (
        "artifact",
        {
            "path": "00_prd/prd.md",
            "size": 1200,
            "preview": "# 产品需求定义\n\n桌面式温湿度监测小夜灯 …",
        },
    ),
    ("tool_start", {"agent": "hardware-engineer", "name": "search_cirkit_docs", "args": "DHT11 Arduino UNO"}),
    (
        "tool_end",
        {
            "agent": "hardware-engineer",
            "name": "search_cirkit_docs",
            "result": "命中 tutorials/create-simulation-lcd：DHT11 接 5V/D2/GND，需 10k 上拉。",
        },
    ),
    ("tool_start", {"agent": "hardware-engineer", "name": "check_circuit", "args": "ERC"}),
    (
        "tool_end",
        {
            "agent": "hardware-engineer",
            "name": "check_circuit",
            "result": "电路规则校验：通过（8 个元件 / 11 条连线）",
        },
    ),
    ("artifact", {"path": "30_hardware/netlist.json", "size": 800, "preview": '{"components":[{"id":"U1",…'}),
    ("artifact", {"path": "30_hardware/bom.csv", "size": 500, "preview": "序号,名称,类别,规格,数量…"}),
    ("tool_start", {"agent": "firmware-engineer", "name": "check_arduino", "args": "pins 2,13"}),
    (
        "tool_end",
        {"agent": "firmware-engineer", "name": "check_arduino", "result": "固件静态检查：通过（识别引脚 2, 13）"},
    ),
    ("artifact", {"path": "40_firmware/firmware.ino", "size": 2400, "preview": "// 温湿度小夜灯\n#define DHTPIN 2 …"}),
    ("artifact", {"path": "10_appearance/appearance.md", "size": 3000, "preview": "# 外观设计\n\n设计定位：温和 / 极简 / 亲和 …"}),
    ("artifact", {"path": "20_structure/structure.md", "size": 2600, "preview": "# 结构设计\n\n外形 92×92×110 mm …"}),
    ("artifact", {"path": "90_review/review.md", "size": 1800, "preview": "# 交叉评审\n\nP0：0 条；P1：2 条 …"}),
    ("token", {"agent": "main", "text": "六阶段全部完成，产物已落盘。"}),
]


def _run_demo(run: Run) -> None:
    run.push({"type": "run_start", "run_id": run.id, "mode": "demo", "requirement": run.requirement})
    _demo_body(run)
    run.push({"type": "done", "summary": "演示流程结束。配置 SILICONFLOW_API_KEY 后重启 worker 即可切换为真实智能体。"})


def _demo_body(run: Run) -> None:
    for kind, payload in DEMO_STEPS:
        time.sleep(0.6)
        run.push({"type": kind, **payload})
    demo_dir = run.dir
    (demo_dir / "00_prd").mkdir(parents=True, exist_ok=True)
    (demo_dir / "00_prd" / "prd.md").write_text(
        "# 产品需求定义（演示数据）\n\n> 这是演示模式产物，配置 SILICONFLOW_API_KEY 后即为真实生成。\n",
        encoding="utf-8",
    )


def _is_quota_error(msg: str) -> bool:
    low = msg.lower()
    return "30001" in msg or "insufficient" in low or "balance" in low or "402" in msg


# ---------------------------------------------------------------- 真实执行
# 流式一旦开始就没法续传，网络抖动会直接打断整轮。这里做整体重跑兜底：
# 只重跑「连接类」错误（鉴权/参数错误重跑多少次都一样），且已在 workspace 落盘的产物会保留。
_NET_ERR = ("ConnectionError", "Timeout", "timeout", "APITimeoutError", "RemoteProtocolError", "ServerDisconnected")


def _is_network_error(detail: str) -> bool:
    return any(k in detail for k in _NET_ERR)


GATEWAY_DOWN_HINT = (
    f"网关持续不可达：候选模型全部连接超时，已等待恢复 {int(config.GATEWAY_WAIT_MAX)} 秒，/models 探活仍不通。"
    "这是网关侧拥塞或维护，不是本项目的配置问题 —— 已落盘的产物不受影响，稍后重试即可；"
    "着急的话可以在设置里换一个网关（硅基流动 / DeepSeek / 智谱等）。"
)

# 「网关通但调不动」：402 余额不足 / 401 key 无效 / 404 模型不存在。
# 这类失败等待和重试都救不了 —— 以前会被当成「网关不可达」白等 600 秒再报一样的错。
GATEWAY_QUOTA_HINT = (
    "网关能连通，但所有候选模型都调不动（余额不足 / key 无效 / 模型不存在）。"
    "等网关恢复没用，需要换一个可用的网关或给当前账号充值 —— 已落盘的产物不受影响。"
)


def _probe_state(results: list[tuple[str, float | None, str]]) -> str:
    try:
        import gateway

        return gateway.classify(results)
    except Exception:  # noqa: BLE001
        return ""


def _maybe_race_model(run: "Run") -> bool:
    """开跑前并发探测候选模型，把本次运行切到当前最快的那个。返回 False = 网关整体不通，别开跑。

    同一个网关下不同模型的排队深度差一个数量级，而且**随时间变化**：昨天 flash 12.8s /
    pro-alpha 1.4s，今天两个都在 40s 以上还夹着连接失败。写死模型名等于把选路押在运气上。
    一次探测几十秒，换后面二三十轮往返全走快的那个。
    """
    if not config.MODEL_RACE:
        return
    s = dict(run.settings or {})
    base = (s.get("base_url") or "").strip() or config.DEFAULT_BASE_URL
    key = (s.get("api_key") or "").strip() or config.fallback_key(base)
    cur = (s.get("model") or "").strip() or config.MODEL
    cands = [cur, *config.MODEL_RACE_CANDIDATES]
    if config.FALLBACK_MODEL and config.FALLBACK_MODEL not in cands:
        cands.append(config.FALLBACK_MODEL)
    if len(dict.fromkeys(cands)) < 2 or not key:
        return True

    import gateway

    # 探测最长要 60 秒，不先说一句的话前端就是一片空白，看着像卡死了
    run.push(
        {
            "type": "tool_note",
            "name": "gateway",
            "detail": f"正在探测网关排队情况（{'、'.join(dict.fromkeys(cands))}），选最快的模型开跑…",
        }
    )
    best, sec, results = gateway.pick_fastest(base, key, cands, config.MODEL_RACE_TIMEOUT)
    if not best:
        state = _probe_state(results)
        if state == "quota":
            # 网关在线，但每个候选都卡在鉴权/额度/模型名上：等待恢复和换模型都救不了，
            # 再等 600 秒只会晚 10 分钟才告诉用户同一件事。
            run.gateway_state = "quota"
            run.push(
                {
                    "type": "warn",
                    "message": f"{GATEWAY_QUOTA_HINT}（{gateway.describe(results)}）",
                }
            )
            return False
        # 两个候选都没响应，先别急着判死 —— 网关偶发抖动很常见。隔 10 秒再问一次 /models
        #（给 20 秒超时，网关拥塞时 /models 本身也要十几秒），
        # 通了就说明只是慢，照常开跑；不通才是真的挂了，早说比让用户干等十几分钟强。
        time.sleep(10)
        if gateway.reachable(base, key, timeout=20.0):
            run.push(
                {
                    "type": "warn",
                    "message": f"网关探测超时（{gateway.describe(results)}），但服务仍可达 —— 按原模型 {cur} 开跑，会比较慢。",
                }
            )
            return True
        run.gateway_state = "unreachable"
        run.push({"type": "warn", "message": GATEWAY_DOWN_HINT})
        return False
    run.settings = {**s, "model": best}
    verb = f"{cur} → {best}" if best != cur else f"沿用 {best}"
    run.push({"type": "tool_note", "name": "gateway", "detail": f"网关选路：{verb}（首字节 {sec}s；{gateway.describe(results)}）"})
    return True


def _gateway_alive(run: "Run") -> bool:
    """分不清「只是慢」还是「整体挂了」时问一次 /models。探活本身失败按活着算，不改变原行为。"""
    try:
        import gateway

        s = run.settings or {}
        base = (s.get("base_url") or "").strip() or config.DEFAULT_BASE_URL
        key = (s.get("api_key") or "").strip() or config.fallback_key(base)
        return gateway.reachable(base, key, timeout=20.0)
    except Exception:  # noqa: BLE001
        return True


def _wait_gateway(run: "Run") -> bool:
    """网关探活不通后的「等待-恢复」窗口：周期性探活，通了就返回 True 让上层续跑。

    为什么不是一击判死：实测网关的短暂拥塞（几十秒到几分钟）远比「真的下线」常见，
    一次 /models 超时就把整个 run 标成 error，等于把「等一分钟就能自愈」的抖动
    变成用户可见的失败弹窗 —— 本次真实事故：两次连接失败 + 一次探活超时 → run 被杀，
    而网关本身几十秒后就恢复了。

    - 分段 sleep（1s 步进）：用户点「停止」要在 1 秒内响应，不能闷头睡到上限
    - extend_deadline 豁免等待时长：等网关不该算进「已用时间」
    - 进度提示按间隔节流：别每秒刷一条 warn 把事件流淹没
    """
    import guards

    if config.GATEWAY_WAIT_MAX <= 0:
        return False
    t0 = time.time()
    last_note = 0.0
    run.push(
        {
            "type": "warn",
            "message": (
                "网关暂时不可达，进入等待恢复（最多 "
                f"{int(config.GATEWAY_WAIT_MAX)} 秒，恢复后自动续跑，产物不会丢）。"
                "也可以点「停止」放弃本轮。"
            ),
        }
    )
    while True:
        waited = time.time() - t0
        if waited >= config.GATEWAY_WAIT_MAX:
            return False
        # 分段睡：取消要秒级响应
        run.extend_deadline(1.0)
        time.sleep(1.0)
        if run.cancel_requested:
            raise guards.RunCancelled("等待网关恢复期间被用户停止")
        waited = time.time() - t0
        # 到探活点了才问一次；探活本身最多 20s，这段时间也不在干活，同样豁免
        if waited - last_note >= config.GATEWAY_PROBE_INTERVAL:
            last_note = waited
            pt = time.time()
            ok = _gateway_alive(run)
            run.extend_deadline(time.time() - pt)
            if ok:
                run.push(
                    {
                        "type": "warn",
                        "message": f"网关已恢复（等待 {int(waited)} 秒），自动续跑，已落盘产物保留。",
                    }
                )
                return True
            run.push(
                {
                    "type": "warn",
                    "message": (
                        f"网关仍不可达（已等 {int(waited)}/{int(config.GATEWAY_WAIT_MAX)} 秒），"
                        "继续等待恢复…"
                    ),
                }
            )


def _resume_hint_for_retry(run: "Run") -> str:
    """网络中断后重跑用的上下文：把已完成阶段写进需求，避免从头再来一遍。

    没有这段，重跑的智能体不知道上一轮做到哪了 —— 会重新读需求、重新写 PRD、重新出图。
    产物虽不会覆盖（有阶段幂等锁），但每一轮都要白烧好几次往返，
    用户看到的就是「重试一次就慢一截」。
    """
    try:
        import pipeline
        import stages as st

        snap = pipeline.snapshot(run.dir)
        done_ids = [x["id"] for x in snap["stages"] if x["status"] == "done"]
        nxt = snap.get("next") or ""
    except Exception:  # noqa: BLE001
        return ""
    if not done_ids or not nxt or nxt not in st.CHAIN:
        return ""
    labels = "、".join(st.stage_label(i) for i in done_ids)
    return (
        f"【网络中断后续跑】本工作区已完成 {len(done_ids)} 个阶段（{labels}），产物均已落盘："
        f"**不要重做、不要重新生成已有文件**，直接从「{st.stage_label(nxt)}」继续。\n\n"
        + (run.base_requirement or run.requirement)
    )


def _switch_fallback_model(run: "Run") -> str:
    """网关持续排队时换备用模型。前提是目标模型在这个网关真的存在 —— 拿 A 家的模型名
    去打 B 家的端点只会 404，白换一次还多等一轮。

    返回实际切换到的模型名，没切换返回空串。
    """
    s = dict(run.settings or {})
    cur = (s.get("model") or config.MODEL).strip()
    target = (config.FALLBACK_MODEL or "").strip()
    if not target or target == cur:
        return ""
    base = (s.get("base_url") or config.DEFAULT_BASE_URL).rstrip("/")
    key = (s.get("api_key") or "").strip() or config.fallback_key(base)
    if not key:
        return ""
    try:
        import httpx

        with httpx.Client(verify=config.ssl_verify(), timeout=20.0) as c:
            r = c.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        ids = [str(m.get("id", "")) for m in (r.json() or {}).get("data", [])]
    except Exception:  # noqa: BLE001  探测失败就按「不能换」处理，不影响主重试链路
        return ""
    if target not in ids:
        return ""
    s["model"] = target
    run.settings = s
    return target


# 限流（429）与网络抖动是两回事：网络断了重连就好，限流必须**等窗口过去**再试，
# 立刻重跑只会再撞一次。免费额度尤其常见 —— 实测连续跑十几分钟就会吃到。
_RATE_MARKERS = ("429", "rate limit", "rate_limit", "ratelimit", "too many requests", "RateLimitError")


def _is_rate_limit(detail: str) -> bool:
    low = detail.lower()
    return any(m.lower() in low for m in _RATE_MARKERS)


RATE_LIMIT_HINT = (
    "网关限流（429）：当前额度按时间窗口限制请求频率，已自动退避重跑 {n} 次仍未通过。\n"
    "已落盘的产物保留在会话目录里。处理：稍后重试；高频使用请升级 Token Plan，"
    "或在前端「模型设置」换成其他服务商。"
)


# 证书被换掉 = 有人在中间拦（Clash TUN / 公司网 / 校园网对特定域名做 HTTPS 中间人）。
# 这类错误重跑多少次都一样，必须给可操作的提示，而不是笼统的「网络中断」。
_SSL_HINT = (
    "网关被拦截或不可达：HTTPS 证书不是签发机构原件，或连接被重置。\n"
    "常见原因：本机代理（如 127.0.0.1:7897 的 Clash/Mihomo）开了 TUN 或规则拦截，"
    "或公司/校园网对该域名做 HTTPS 中间人。\n"
    "处理：关掉代理后重启 worker；必须走代理时把其根证书导出为 pem 并设 AGENT_CA_BUNDLE；"
    "排障期可临时 AGENT_VERIFY_SSL=0。也可在前端「模型设置」换其他服务商。"
)


def _is_ssl_error(detail: str) -> bool:
    low = detail.lower()
    return "certificate" in low or "ssl" in low


def _run_with_retry(run: Run, max_attempts: int = 0) -> str:
    from main_agent import run_agent  # 延迟导入，演示模式下无需加载大模型依赖
    import guards

    max_attempts = max_attempts or config.NET_RETRY_MAX
    # 限流重试单独计次：它比网络抖动常见得多，也值得多等几轮
    rate_tries = 0
    net_tries = 0
    switched = ""
    attempt = 1
    while True:
        try:
            return run_agent(run.requirement, run.dir, run.push, run.settings, run.paradigm, run)
        except guards.RunAborted:
            # 主动中止（取消/超时/超预算）不是网络抖动，重跑既无意义也不该发生
            raise
        except Exception as e:  # noqa: BLE001
            detail = f"{type(e).__name__}: {e}"
            # SSL 被拦 = 环境层面的确定性问题，重跑纯属浪费时间，直接报出来
            if _is_ssl_error(detail):
                run.push({"type": "warn", "message": _SSL_HINT})
                raise RuntimeError(_SSL_HINT) from e

            if _is_rate_limit(detail):
                rate_tries += 1
                if rate_tries >= config.RATE_LIMIT_ATTEMPTS:
                    raise
                # 撞了就把配额窗口记满：否则下一个请求还会按原节奏打出去再撞一次，
                # 于是「每次都弹限流提示」。记满后请求前会主动等窗口滑过去。
                try:
                    import quotaguard

                    quotaguard.penalize()
                except Exception:  # noqa: BLE001
                    pass
                wait = min(config.RATE_LIMIT_BACKOFF * rate_tries, config.RATE_LIMIT_MAX_WAIT)
                run.push(
                    {
                        "type": "warn",
                        "message": (
                            f"网关限流（429），等待 {int(wait)} 秒后自动续跑"
                            f"（第 {rate_tries + 1}/{config.RATE_LIMIT_ATTEMPTS} 次）。"
                            "已落盘的产物会保留，不会从头重来。"
                        ),
                    }
                )
                time.sleep(wait)
                run.extend_deadline(wait)  # 等待不算进已用时间
                continue

            if attempt >= max_attempts or not _is_network_error(detail):
                raise
            attempt += 1
            net_tries += 1
            # 网关整体挂掉时，剩下的退避和重跑全是白等 —— 先探活。但探活不通≠判死：
            # 短暂拥塞远比真下线常见，先进入等待-恢复窗口，恢复就续跑；等到上限仍不通才收手。
            if config.GATEWAY_DEAD_AFTER and net_tries >= config.GATEWAY_DEAD_AFTER and not _gateway_alive(run):
                if not _wait_gateway(run):
                    raise RuntimeError(
                        f"网关持续不可达：连续多次连接失败，等待恢复 {int(config.GATEWAY_WAIT_MAX)} 秒后探活仍不通。"
                        "这一般是网关侧拥塞或维护，不是本项目的配置问题 —— 已落盘的产物会保留，稍后重试即可。"
                    )
                # 网关回来了：net_tries 清零重新计，避免刚恢复跑一轮又抖、立刻再进等待窗口
                net_tries = 0
                switched = ""  # 恢复后重新允许换模型（排队格局可能已经变了）
            # 连续排队到阈值就换模型：网关常常是「一个模型堵，另一个空」，
            # 原地重试只是再排一次队（实测 flash 12.8s / pro-alpha 1.4s）。
            if not switched and net_tries >= config.NET_FALLBACK_AFTER:
                switched = _switch_fallback_model(run)
                if switched:
                    run.push(
                        {
                            "type": "warn",
                            "message": f"网关持续排队，后续改用备用模型 {switched} 继续（工作区与已落盘产物不变）。",
                        }
                    )
            # 重跑前把进度写进需求：否则新的一轮不知道做到哪了，会从头再走一遍
            hint = _resume_hint_for_retry(run)
            if hint:
                run.requirement = hint
                run.push({"type": "warn", "message": "已把本轮进度写进续跑上下文，重跑不会重做已完成阶段。"})
            wait = min(config.NET_RETRY_BACKOFF * net_tries, 60)
            run.push(
                {
                    "type": "warn",
                    "message": (
                        f"网关连接抖动（{type(e).__name__}），{int(wait)} 秒后自动重跑"
                        f"（第 {attempt}/{max_attempts} 次）。已落盘的产物会保留，不会从头重来。"
                    ),
                }
            )
            time.sleep(wait)
            run.extend_deadline(wait)  # 等待不算进已用时间


def _run_real(run: Run) -> None:
    import guards

    run.push({"type": "run_start", "run_id": run.id, "mode": "live", "requirement": run.requirement})
    try:
        try:
            alive = _maybe_race_model(run)
        except Exception:  # noqa: BLE001  选路只是加速手段，探失败就按原模型跑
            alive = True
        if not alive and getattr(run, "gateway_state", "") != "quota":
            # 开跑时网关不通：同样先等恢复（短拥塞比真下线常见），等到上限才按失败收场。
            # 等待期间用户点「停止」会从这里抛 RunCancelled —— 必须留在本 try 里，
            # 否则会被 worker 兜底记成 error，把「主动停止」误判成失败。
            # quota 不进这个窗口：等网关恢复救不了余额不足，只会晚十分钟才说同一句话。
            if _wait_gateway(run):
                try:
                    alive = _maybe_race_model(run)
                except Exception:  # noqa: BLE001
                    alive = True
        if not alive:
            hint = GATEWAY_QUOTA_HINT if getattr(run, "gateway_state", "") == "quota" else GATEWAY_DOWN_HINT
            run.db_status, run.db_error = "error", hint
            run.push({"type": "warn", "message": hint})
            run.push({"type": "done", "summary": f"**未能开跑**：{hint}"})
            return
        final = _run_with_retry(run)
        run.db_summary = final
        run.push({"type": "done", "summary": final[-6000:]})
    except guards.RunAborted as e:
        # 主动中止：不是故障，给一句人话，并把已落盘的产物原样保留
        reached = e.status == "done"   # 目标达成不算错误，error 字段留空
        run.db_status = e.status
        run.db_error = "" if reached else str(e)
        run.db_summary = run.db_summary or (
            "六阶段产物已交付（目标达成）。" if reached
            else "本次运行被中止（" + e.label + "），已产出的文件保留在会话目录里。"
        )
        try:
            run.push({"type": "warn", "message": f"{e.label}：{e}"})
            # 超时但还有阶段没做完 → 立刻决定自动续跑，并把提示推在 done 之前 ——
            # 前端收到 done 就会关闭事件流，晚推的提示它看不到，用户会以为这轮白跑了
            try:
                _maybe_auto_resume(run)
            except Exception:  # noqa: BLE001
                pass
            run.push({
                "type": "done",
                "summary": f"**{e.label}**：{e}\n\n已产出的产物已保留，可在「设计产物」里查看。",
            })
        except Exception:  # noqa: BLE001  中止后连收尾事件都推不动也无所谓
            pass
    except Exception as e:  # noqa: BLE001
        detail = f"{type(e).__name__}: {e}"
        run.db_status, run.db_error = "error", detail
        if _is_rate_limit(detail):
            # 限流不是流程缺陷，也不该降级成演示（那等于拿假数据糊弄）。
            # 如实说明 + 保留已落盘产物，用户稍后重试即可。
            hint = RATE_LIMIT_HINT.format(n=config.RATE_LIMIT_ATTEMPTS)
            run.db_error = hint
            run.push({"type": "warn", "message": hint})
            run.push({
                "type": "done",
                "summary": f"**网关限流（429）**：{hint}\n\n已产出的产物已保留，可在「设计产物」里查看。",
            })
            return
        if _is_quota_error(detail):
            # 额度问题不是流程缺陷，降级演示让用户仍能看到完整链路
            run.push(
                {
                    "type": "warn",
                    "message": "SiliconFlow 账户额度不足，已自动切换为演示流程。充值后重启 worker 即恢复真实生成。",
                }
            )
            try:
                _demo_body(run)
                run.push(
                    {
                        "type": "done",
                        "summary": "当前为**降级演示**：模型额度不足，产物为示意数据。充值后重启 worker 即可获得真实设计。",
                    }
                )
            except Exception as e2:  # noqa: BLE001
                run.push({"type": "error", "message": f"降级演示失败：{type(e2).__name__}: {e2}"})
                run.db_status = "error"
                return
            # 降级演示也算「交付完成」，额度原因记在 error 字段里，历史列表能看到
            run.db_status = "done"
            run.db_summary = "降级演示：模型额度不足，产物为示意数据。"
            return
        run.push({"type": "error", "message": f"{detail}\n{traceback.format_exc()[-1500:]}"})


def _effective_key(settings: dict[str, Any] | None) -> str:
    s = settings or {}
    base_url = (s.get("base_url") or "").strip() or config.DEFAULT_BASE_URL
    return (s.get("api_key") or "").strip() or config.fallback_key(base_url)


def _start(run: Run) -> None:
    demo = config.MOCK or not _effective_key(run.settings)
    target = _run_demo if demo else _run_real
    # 决策范式先亮相：前端据此显示「本轮走哪种思考方式」，出问题时也知道该看哪一档的预算
    try:
        run.push({"type": "paradigm", **run.paradigm.as_dict()})
    except Exception:  # noqa: BLE001  推不动不影响执行
        pass

    def worker() -> None:
        try:
            target(run)
        except Exception as e:  # noqa: BLE001  兜底：执行体漏抛的异常也要记进历史
            run.db_status = "error"
            run.db_error = f"{type(e).__name__}: {e}"
        finally:
            run.done = True
            # 这里必须包住：取消过之后 push 会再抛 RunAborted，而下面的落库无论如何都要跑
            try:
                run.push({"type": "closed"})
            except Exception:  # noqa: BLE001
                pass
            try:
                store.index_files(run.id, run.dir)  # 以磁盘为准对账，防漏记
                import stages

                prog = stages.progress_of_dir(run.dir)
                elapsed = time.time() - run.started
                missing = [s["id"] for s in (prog.get("stages") or []) if not s.get("done")]
                # Reflexion：从本次终态沉淀「错误模式 → 修正动作」，下次同类任务直接避开。
                # 失败/中止的会话照样沉淀 —— 能学的恰恰是失败。
                run.n_reflexions = store.save_reflexions(
                    run.id,
                    run.db_status,
                    run.db_error,
                    missing,
                    elapsed,
                    len(stages.scan_dir(run.dir)),
                )
                store.finish_run(
                    run.id, run.db_status, run.db_summary, run.db_error, elapsed,
                    paradigm=run.paradigm.key,
                    n_reflexions=run.n_reflexions,
                    goal_hit=run.goal_hit,
                )
                # 情景记忆：只记交付成功的，失败的会话记进去只会污染下次的参考
                if run.db_status == "done":
                    store.save_memory(
                        run.id,
                        run.requirement,
                        run.db_summary,
                        run.db_status,
                        f"{prog['done']}/{prog['total']}",
                        len(stages.scan_dir(run.dir)),
                        elapsed,
                    )
                # 自动续跑已经在 _run_real 里（done 之前）决定并启动，这里不再重复触发
            except Exception:  # noqa: BLE001  落库失败不能影响已经跑完的流程
                pass

    threading.Thread(target=worker, name=f"run-{run.id}", daemon=True).start()


# ---------------------------------------------------------------- API
class RunRequest(BaseModel):
    requirement: str
    settings: ModelSettings | None = None
    # 强制指定决策范式（lean/standard/review/explore），留空 = 按任务 DNA 自动路由
    paradigm: str = ""


def _warm_cad() -> None:
    """后台预热 CAD 运行时。

    build123d 首次 import 要十几秒（拉起 OCP 内核），放在启动线程里做掉，
    /health 与第一次建模就不用干等；预热失败也不影响启动，真调用时再报错。
    """
    try:
        import cad

        cad.available()
    except Exception:  # noqa: BLE001
        pass


threading.Thread(target=_warm_cad, daemon=True).start()


@app.get("/health")
def health() -> dict[str, Any]:
    default = config.server_default()
    try:
        import cad

        # 用非阻塞版本：/health 几秒一次，不能每次都去解析一次 uvx 环境（~21s）
        cad_ok, cad_info = cad.status()
    except Exception as exc:  # CAD 是可选能力，探测失败不能拖垮健康检查
        cad_ok, cad_info = False, f"未安装：{exc}"
    # 出图配置：优先显示「最近一次会话实际用的」，没有才退回服务端默认（.env）。
    # 只按 .env 显示会误导 —— 用户在前端把图像模型改成 agnes-image-2.5-flash 后，
    # 这里仍吐 .env 的 2.1，看起来像设置没生效（实测踩过这个坑）。
    env_endpoint, env_model = config.resolve_image(config.DEFAULT_BASE_URL)
    last_cfg = store.latest_settings()
    # 前端的「留空」会以空串/空白落库，必须 strip 后再判有效性，
    # 否则一个空格就被当成「用户指定过」，把 .env 默认顶掉。
    _set_model = str(last_cfg.get("image_model") or "").strip()
    _set_base = str(last_cfg.get("image_base_url") or "").strip()
    img_model = _set_model or env_model
    img_endpoint = _set_base or env_endpoint
    # 凭据不落库：会话里配过自己的图像 key 时库里只剩 ***，只能标注「来自前端设置」。
    _last_key = str(last_cfg.get("image_api_key") or "").strip()
    img_key_display = (
        "***(前端设置)"
        if _last_key and set(_last_key) == {"*"}
        else config.mask(config.fallback_key(img_endpoint) or config.fallback_key(config.DEFAULT_BASE_URL))
    )
    img_source = "recent-run" if (_set_model or _set_base) else "env-default"
    img_host = img_endpoint.split("//")[-1].split("/")[0]
    try:
        from tools import ALL_TOOLS

        tool_names = [getattr(t, "name", "") for t in ALL_TOOLS]
    except Exception:  # noqa: BLE001  工具加载失败不能拖垮健康检查
        tool_names = []
    return {
        "ok": True,
        "mode": "demo" if (config.MOCK or default["has_key"] == "0") else "live",
        "model": config.MODEL,
        "runs": len(RUNS),
        # 前端首次打开时用它对齐默认模型（不含密钥，密钥始终留在服务端 .env）
        "default": default,
        "cad": {"ok": cad_ok, "info": cad_info},
        # 图像链路诊断：401 时先看这里，确认「打到哪个网关 + 用的哪把 key」
        # source=recent-run 表示取自最近一次会话的前端设置；env-default 表示沿用 .env
        "image": {
            "host": img_host,
            "model": img_model,
            "key": img_key_display,
            "source": img_source,
            "env_model": env_model,
        },
        "store": store.stats(),
        # 工具清单：前端能直接确认「这次到底有哪些能力在线」
        "tools": tool_names,
    }


@app.post("/api/run")
def create_run(req: RunRequest) -> dict[str, Any]:
    text = req.requirement.strip()
    if not text:
        raise HTTPException(400, "需求不能为空")
    run = Run(text, req.settings.model_dump() if req.settings else None, req.paradigm)
    RUNS[run.id] = run
    _start(run)
    return {"run_id": run.id, "dir": str(run.dir), "paradigm": run.paradigm.as_dict()}


@app.get("/api/paradigms")
def list_paradigms(q: str = "") -> dict[str, Any]:
    """四档范式 + 当前需求会被路由到哪一档（供前端展示与手动覆盖）。"""
    auto = paradigms.route(q, forced=config.PARADIGM) if q.strip() else paradigms.STANDARD
    return {
        "items": [p.as_dict() for p in (paradigms.LEAN, paradigms.STANDARD,
                                        paradigms.REVIEW, paradigms.EXPLORE)],
        "auto": auto.as_dict(),
        "forced": config.PARADIGM,
    }


# ---------------------------------------------------------------- 模型设置
@app.get("/api/providers")
def list_providers() -> dict[str, Any]:
    import providers

    return {"providers": providers.public(), "default_model": config.MODEL}


class ModelsRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""


# /models 拉得通 ≠ 能调用：实测某网关 /models 200 列出 98 个模型，chat 却一律 402（余额为 0）。
# 「测试连接」只问 /models 的话，用户会拿到一个「连接成功」，然后开跑必失败。
_SKIP_FOR_CHAT = ("embedding", "rerank", "asr", "tts", "ocr", "captioner", "bge", "gte", "m3e", "jina", "clip")
# 优先挑名字像对话模型的那个，别拿嵌入 / 重排模型去发 chat（会 400「模型不存在」）
_CHAT_HINT = ("instruct", "chat", "turbo", "flash", "pro", "plus", "mini", "8b", "7b", "14b", "32b")


def _pick_chat_model(ids: list[str]) -> str:
    cands = [m for m in ids if not any(k in m.lower() for k in _SKIP_FOR_CHAT)] or ids
    pref = [m for m in cands if any(k in m.lower() for k in _CHAT_HINT)]
    return (pref or cands)[0] if (pref or cands) else ""


def _probe_chat(base: str, key: str, model: str, timeout: float = 45.0) -> dict[str, Any]:
    """用极小请求真调一次，区分「能用 / 额度或鉴权挡着 / 连不上」。"""
    import httpx

    try:
        with httpx.Client(verify=config.ssl_verify(), timeout=timeout) as cli:
            r = cli.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "只回复一个字：好"}], "max_tokens": 8},
            )
    except Exception as e:  # noqa: BLE001
        return {"state": "unreachable", "detail": f"{type(e).__name__}: {e}"[:120], "model": model}
    if r.status_code == 200:
        return {"state": "ok", "detail": "调用成功", "model": model}
    state = "quota" if r.status_code in (401, 402, 403, 404, 422, 429) else "error"
    return {"state": state, "detail": f"HTTP{r.status_code} {r.text[:120]}", "model": model}


@app.post("/api/models")
def list_models(req: ModelsRequest) -> JSONResponse:
    """用用户自己的 key 拉远端模型列表，兼作「测试连接」——顺带真调一次，
    免得「连接成功」后面跟着一轮必失败的运行。"""
    import providers

    base = (req.base_url or "").strip().rstrip("/")
    key = (req.api_key or "").strip()
    if not base:
        raise HTTPException(400, "请先填写 Base URL")
    if not key:
        raise HTTPException(400, "请先填写 API Key")
    url = f"{base}/models"
    try:
        r = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(r, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        ids = sorted({str(m.get("id")) for m in (data.get("data") or []) if m.get("id")})
        if not ids:
            raise HTTPException(400, "接口返回为空，请检查 Base URL 是否正确（通常以 /v1 结尾）")
        probe: dict[str, Any] = {}
        chat_model = _pick_chat_model(ids)
        if chat_model:
            probe = _probe_chat(base, key, chat_model)
        return JSONResponse({"models": ids, "count": len(ids), "probe": probe})
    except HTTPException:
        raise
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise HTTPException(400, f"网关返回 {e.code}：{body}") from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"连接失败：{type(e).__name__}: {e}") from None


class ClarifyRequest(BaseModel):
    requirement: str = Field(min_length=1)
    settings: ModelSettings | None = None


@app.post("/api/clarify")
def clarify_req(req: ClarifyRequest) -> JSONResponse:
    """生成需求补全清单：固定骨架 + 按需求定制选项。模型不可用只用规则定制，一样出问卷。"""
    import clarify

    plan = clarify.clarify(req.requirement.strip(), req.settings.model_dump() if req.settings else None)
    return JSONResponse(plan)


def _sse(ev: dict[str, Any]) -> str:
    import json

    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@app.get("/api/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")

    loop = __import__("asyncio").get_running_loop()

    async def gen():
        # 游标是「绝对条数」：内存列表会被 MAX_EVENTS 裁掉头部，用相对下标会错位重推
        idx = run.events_base
        terminal = ("done", "error", "closed")
        yield _sse({"type": "hello", "run_id": run.id})
        last_beat = time.time()
        while True:
            with run.lock:
                pending = run.events[max(0, idx - run.events_base):]
                idx = run.events_base + len(run.events)
                done = run.done
            for ev in pending:
                last_beat = time.time()
                yield _sse(ev)
            if done or any(e.get("type") in terminal for e in pending):
                return
            # 心跳：静默超过阈值就推一条。它**不进 events 列表、不落库** ——
            # 目的是让前端分清「还在跑（有心跳）」和「后端假死（连心跳都没有）」，
            # 后者是最容易被误判成「都结束了还在运行中」的故障。
            if time.time() - last_beat > config.HEARTBEAT_SECONDS:
                yield _sse({
                    "type": "heartbeat",
                    "ts": time.time(),
                    "elapsed": round(time.time() - run.started, 1),
                    "tools": run.tool_calls,
                })
                last_beat = time.time()
            # 取消看门狗：护栏只能在事件到达时中断，而智能体可能正堵在一次模型调用里
            # （几十秒不推事件）。取消后超过宽限期就先给前端收口 —— 后台线程跑完
            # 仍会照常落盘，产物不丢，只是 UI 不再陪着转圈。
            if not done and run.cancel_expired():
                yield _sse({
                    "type": "warn",
                    "message": "已停止：智能体正堵在一次模型调用里，界面先收口，"
                               "后台跑完后产物仍会写进本次会话。",
                })
                yield _sse({
                    "type": "done",
                    "summary": "**已停止**（等待模型响应超时，界面先收口）。已产出的产物保留在会话里。",
                })
                return
            try:
                # 1 秒唤醒一次就够了：再长会拖慢心跳，再短是空转
                await loop.run_in_executor(None, lambda: run.q.get(timeout=1))
            except Exception:  # noqa: BLE001  超时仅用于唤醒，事件始终以 events 列表为准
                pass

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _run_root(run_id: str) -> Path:
    """取运行目录。worker 重启后内存里的 Run 会丢，这里按 id 回落到磁盘目录，
    保证之前生成的产物链接依旧可访问（run_id 只允许安全字符，杜绝穿越）。"""
    run = RUNS.get(run_id)
    if run:
        return run.dir
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id or ""):
        d = config.WORKSPACE / run_id
        if d.is_dir():
            return d
    raise HTTPException(404, "run not found")


def _safe_path(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise HTTPException(400, "路径越界")
    return target


def _resolve_file(root: Path, rel: str, base: str = "") -> Path:
    """按「原样 → 相对当前文档目录 → 同文件名兜底」三级查找。

    文档里写的图片路径常常是相对当前 md 的（![爆炸图](exploded-01.png)），
    甚至被模型放到了别的子目录，逐级兜底才能保证图能显示出来。
    """
    cands: list[Path] = []
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if rel:
        cands.append(_safe_path(root, rel))
    if base:
        b = base.replace("\\", "/").strip("/")
        if b and not rel.startswith(b + "/"):
            cands.append(_safe_path(root, f"{b}/{rel}"))
    for c in cands:
        if c.is_file():
            return c
    # 同名兜底：模型偶尔把图放到别的阶段目录
    name = Path(rel).name
    if name:
        for p in sorted(root.rglob(name)):
            if p.is_file():
                return p
    raise HTTPException(404, "file not found")


@app.get("/api/runs/{run_id}/files")
def list_files(run_id: str) -> JSONResponse:
    root = _run_root(run_id)
    items = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "AGENTS.md":
            rel = p.relative_to(root).as_posix()
            items.append({"path": rel, "size": p.stat().st_size, "mtime": p.stat().st_mtime})
    return JSONResponse({"run_id": run_id, "files": items})


@app.get("/api/runs/{run_id}/file")
def read_file(request: Request, run_id: str, path: str) -> JSONResponse:
    root = _run_root(run_id)
    p = _resolve_file(root, path, base=request.query_params.get("base", ""))
    return JSONResponse(
        {"path": p.relative_to(root).as_posix(), "content": p.read_text(encoding="utf-8", errors="replace")}
    )


_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".step": "application/step", ".stp": "application/step",
    ".stl": "model/stl", ".3mf": "model/3mf", ".pdf": "application/pdf",
}


@app.get("/api/runs/{run_id}/raw")
def read_raw(request: Request, run_id: str, path: str = "", base: str = "") -> Response:
    """二进制产物（渲染图 / 爆炸图 / 预览 SVG / STEP / STL）直接以字节流返回。"""
    root = _run_root(run_id)
    p = _resolve_file(root, path, base)
    media = _MIME.get(p.suffix.lower(), "application/octet-stream")
    return Response(
        content=p.read_bytes(),
        media_type=media,
        headers={"Cache-Control": "public, max-age=300"},
    )


# ---------------------------------------------------------------- 交付清单与下载
def _latest_todos(run_id: str) -> list[dict[str, Any]]:
    """取会话最后一次 write_todos 写下的规划清单 —— 产物分组的标题就用它。"""
    try:
        evs = store.load_events(run_id, limit=20000)
    except Exception:  # noqa: BLE001  事件读不到就退回「用阶段名当标题」
        return []
    items: list[dict[str, Any]] = []
    for e in evs:
        if str(e.get("type") or "") == "todo" and isinstance(e.get("items"), list):
            items = e["items"]
    return items


def _content_disposition(name: str) -> str:
    """中文文件名：ASCII 兜底 + RFC 5987。只给 filename 会被各家浏览器转成乱码。"""
    from urllib.parse import quote

    ascii_name = "".join(c if (c.isascii() and c not in '\\/"') else "_" for c in name) or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@app.get("/api/runs/{run_id}/manifest")
def api_manifest(run_id: str) -> JSONResponse:
    """交付清单：阶段（标题取自规划清单）→ 产物（逐个可下载）。"""
    import pipeline

    root = _run_root(run_id)
    man = pipeline.manifest(root, _latest_todos(run_id))
    return JSONResponse({"run_id": run_id, **man})


@app.get("/api/runs/{run_id}/download")
def api_download(
    run_id: str, path: str = "", stage: str = "", scope: str = "", base: str = ""
) -> Response:
    """三种粒度：
    - `?path=20_structure/structure.md` 单文件
    - `?stage=30_hardware` 该阶段全部产物打包（zip 内保留阶段目录结构）
    - `?scope=all` 全部产物打包（zip 内附一份交付清单 md，按规划标题分组）
    """
    import pipeline

    root = _run_root(run_id)
    if scope == "all":
        data, name = pipeline.zip_all(root, run_id, _latest_todos(run_id))
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": _content_disposition(name)},
        )
    if stage:
        import stages as _st

        sid = stage.strip()
        if sid not in {s["id"] for s in _st.STAGES}:
            raise HTTPException(400, f"未知阶段：{sid}")
        data, name = pipeline.zip_stage(root, sid)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": _content_disposition(name)},
        )
    if path:
        p = _resolve_file(root, path, base)
        media = _MIME.get(p.suffix.lower(), "application/octet-stream")
        return Response(
            content=p.read_bytes(),
            media_type=media,
            headers={"Content-Disposition": _content_disposition(p.name)},
        )
    raise HTTPException(400, "需要 path / stage / scope=all 之一")


def _resume_text(
    root: Path,
    run_id: str,
    base_requirement: str,
    man: dict[str, Any],
    done_ids: list[str],
    from_stage: str,
) -> str:
    """续跑提示词：把已落盘产物列清楚，让模型接着做而不是重做。"""
    import stages as st

    lines = [f"【续跑会话 {run_id}】以下是已落盘的产物，直接复用，不要重做：", ""]
    for g in man["stages"]:
        if not g["files"]:
            continue
        lines.append(f"- {g['id']} {g['label']}（{g['plan_title']}）：" + "、".join(f["path"] for f in g["files"]))
    lines.append("")
    # 已确认过的阶段要点名：续跑时模型不知道上一轮用户点过确认，
    # 于是把已确认的外观图又出一遍、又把同一份图请用户确认一次（实测就是这个现象）。
    try:
        import stagestate

        confirmed = [
            f"{s['id']} {s['label']}"
            for s in st.STAGES
            if stagestate.is_confirmed(root, s["id"])
        ]
    except Exception:  # noqa: BLE001
        confirmed = []
    if confirmed:
        lines.append(f"**已由用户确认、不要重做的阶段**：{'、'.join(confirmed)}。")
        lines.append(
            "这些阶段只需 ls 列出已有产物并向回交，禁止重新出图 / 重新建模 / 再次请用户确认。"
        )
    lines.append("")
    lines.append(f"本轮从 **{from_stage} {st.stage_label(from_stage)}** 开始，按串行链依次完成剩余阶段；")
    lines.append(f"上游已完成：{'、'.join(done_ids) or '（无）'}。开工前先调 stage_gate 确认上游齐备。")
    return (base_requirement or "").strip() + "\n\n" + "\n".join(lines)


def _maybe_auto_resume(run: "Run") -> None:
    """超时中止但还有剩余阶段 → 自动开一轮续跑，复用同一工作区。

    为什么值得自动做：900 秒的档位是给「一轮」定的，而六阶段串行链本身就要十几分钟，
    超时中止时产物往往已经攒到 4/6。让用户手动点「继续」既容易忘，也容易被误认为失败。
    自动续跑把「时间到」变成「分段跑完」，同时保留硬性上限 —— 连续 AUTO_RESUME_MAX
    轮都没做完就真的停手并如实报告，不会无限烧下去。
    """
    import pipeline
    import stages as st

    if run.auto_resumed or run.db_status != "timeout" or run.cancel_requested:
        return
    if run.auto_resume_count >= config.AUTO_RESUME_MAX:
        return
    try:
        prog = st.progress_of_dir(run.dir)
    except Exception:  # noqa: BLE001
        return
    done = int(prog.get("done") or 0)
    if prog.get("finished") or done <= 0:
        # 一点产物都没有说明问题不在时间上（配置/网关），续跑只是重复失败
        return
    try:
        man = pipeline.manifest(run.dir, _latest_todos(run.id))
        snap = pipeline.snapshot(run.dir)
        from_stage = snap.get("next") or ""
        if not from_stage or from_stage not in st.CHAIN:
            return
        done_ids = [s["id"] for s in snap["stages"] if s["status"] == "done"]
        text = _resume_text(run.dir, run.id, run.base_requirement, man, done_ids, from_stage)
    except Exception:  # noqa: BLE001  续跑准备失败就算了，至少原会话的产物还在
        return

    run.auto_resumed = True   # 同一轮只自动续跑一次
    nxt = Run(text, run.settings, run.paradigm.key, resume_dir=run.dir)
    nxt.auto_resume_count = run.auto_resume_count + 1
    nxt.auto_resume_of = run.auto_resume_of or run.id
    nxt.base_requirement = run.base_requirement
    RUNS[nxt.id] = nxt
    try:
        run.push(
            {
                "type": "warn",
                "message": (
                    f"超时中止，但已完成 {done}/{prog.get('total', 6)} 个阶段 → "
                    f"已自动续跑（第 {nxt.auto_resume_count} 轮，新会话 {nxt.id}），"
                    f"从「{st.stage_label(from_stage)}」继续。"
                ),
            }
        )
    except Exception:  # noqa: BLE001
        pass
    _start(nxt)
    try:
        nxt.push(
            {
                "type": "warn",
                "message": (
                    f"本会话由 {nxt.auto_resume_of} 超时后自动续跑（第 {nxt.auto_resume_count} 轮），"
                    f"已有产物直接复用，从「{st.stage_label(from_stage)}」继续。"
                ),
            }
        )
    except Exception:  # noqa: BLE001
        pass


class ResumeRequest(BaseModel):
    """从已有会话接着往下做：复用上轮产物，只跑下游阶段。"""
    from_stage: str = ""
    settings: ModelSettings | None = None
    paradigm: str = ""


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str, req: ResumeRequest) -> dict[str, Any]:
    """续跑：外观图已有时，没必要重出一遍 —— 直接从结构（或指定阶段）往下做。

    依赖链是串行的，续跑点之后的每一步仍按顺序走；续跑点之前的产物原样保留，
    作为下游的输入（结构读外观提示词、硬件读结构内腔、固件读 BOM）。
    """
    import pipeline
    import stages as st

    old = store.get_run(run_id)
    if not old:
        raise HTTPException(404, "run not found")
    root = _run_root(run_id)

    snap = pipeline.snapshot(root)
    man = pipeline.manifest(root, _latest_todos(run_id))
    done_ids = [s["id"] for s in snap["stages"] if s["status"] == "done"]
    from_stage = (req.from_stage or "").strip() or snap["next"] or ""
    if from_stage and from_stage not in st.CHAIN:
        raise HTTPException(400, f"未知阶段：{from_stage}")
    if not from_stage:
        raise HTTPException(400, "所有阶段都已产出，没有需要续跑的部分")

    text = _resume_text(root, run_id, (old.get("requirement") or "").strip(), man, done_ids, from_stage)

    run = Run(text, req.settings.model_dump() if req.settings else None, req.paradigm, resume_dir=root)
    RUNS[run.id] = run
    _start(run)
    return {
        "run_id": run.id,
        "dir": str(run.dir),
        "resumed_from": run_id,
        "from_stage": from_stage,
        "done_stages": done_ids,
        "paradigm": run.paradigm.as_dict(),
    }


# ---------------------------------------------------------------- 历史会话
@app.get("/api/runs")
def api_list_runs(limit: int = 50, offset: int = 0, q: str = "") -> JSONResponse:
    """历史会话列表。会话、事件、产物全部来自 SQLite，重启不丢。"""
    return JSONResponse({"runs": store.list_runs(limit=limit, offset=offset, keyword=q)})


@app.get("/api/runs/{run_id}")
def api_get_run(run_id: str) -> JSONResponse:
    data = store.get_run(run_id)
    if not data:
        raise HTTPException(404, "run not found")
    data["live"] = run_id in RUNS and not RUNS[run_id].done
    data["events"] = store.count_events(run_id)
    run = RUNS.get(run_id)
    if run is not None:
        import hitl

        data["awaiting"] = hitl.pending_of(run)
    return JSONResponse(data)


@app.get("/api/runs/{run_id}/events")
def api_run_events(run_id: str, after: int = 0, limit: int = 20000) -> JSONResponse:
    """事件重放：默认从头。after=N 表示「前 N 条已收到」，用于断线续传。"""
    if not store.get_run(run_id):
        raise HTTPException(404, "run not found")
    evs = store.load_events(run_id, after=after, limit=limit)
    return JSONResponse({"run_id": run_id, "after": after, "count": len(evs), "events": evs})


class ReviewRequest(BaseModel):
    """人工确认结论：approve = 放行下游，revise = 按反馈重做本阶段。"""
    decision: str = "approve"
    feedback: str = ""
    stage: str = ""


@app.get("/api/runs/{run_id}/review")
def api_get_review(run_id: str) -> JSONResponse:
    """当前有没有在等人的阶段。刷新页面后靠它把确认面板恢复出来。"""
    run = RUNS.get(run_id)
    if not run:
        return JSONResponse({"pending": None, "run_id": run_id})
    import hitl

    pend = hitl.pending_of(run)
    return JSONResponse({"run_id": run_id, "pending": pend})


@app.post("/api/runs/{run_id}/review")
def api_post_review(run_id: str, req: ReviewRequest) -> JSONResponse:
    """用户给出结论，唤醒挂起的运行。"""
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    import hitl

    pend = getattr(run, "awaiting", None)
    if not pend:
        # 已经不在等待（比如页面停留太久后点确认）：结论照样落盘，门禁读得到
        stage = (req.stage or "").strip()
        if not stage:
            return JSONResponse({"ok": False, "run_id": run_id, "note": "当前没有等待确认的阶段"})
        dec = hitl.APPROVE if req.decision.strip().lower() in ("approve", "ok", "确认", "通过") else hitl.REVISE
        hitl.save_record(run.dir, stage, dec, req.feedback)
        return JSONResponse({"ok": True, "run_id": run_id, "stage": stage, "note": "已记录（运行未在等待）"})
    ok = hitl.respond(run, req.decision, req.feedback)
    return JSONResponse(
        {
            "ok": bool(ok),
            "run_id": run_id,
            "stage": pend.get("stage") or "",
            "decision": "approve" if req.decision.strip().lower() in ("approve", "ok", "确认", "通过") else "revise",
        }
    )


@app.post("/api/runs/{run_id}/cancel")
def api_cancel_run(run_id: str) -> JSONResponse:
    """停止正在跑的会话。护栏在下一个事件到达时中断整条流，已产出的产物保留。"""
    run = RUNS.get(run_id)
    if not run:
        # 不在内存里 = 这次 worker 没跑过它（重启过或还没开始）
        data = store.get_run(run_id)
        if not data:
            raise HTTPException(404, "run not found")
        return JSONResponse({"ok": False, "run_id": run_id, "note": "该会话不在本进程运行，无需停止"})
    if run.done:
        return JSONResponse({"ok": False, "run_id": run_id, "note": "会话已结束"})
    run.cancel()
    return JSONResponse({"ok": True, "run_id": run_id, "note": "已发出停止请求，智能体将在当前步骤后中断"})


@app.delete("/api/runs/{run_id}")
def api_delete_run(run_id: str) -> JSONResponse:
    """彻底删除一次会话：库记录 + 事件流 + 产物清单 + 情景记忆 + 反思 + 磁盘产物目录。

    「确认移除」= 真删。只删库不删盘的话，启动时 backfill 会把 workspace 下那个还在的
    目录重新扫回历史（表现为「删了，重启又回来了」），所以这里连盘一起删，
    并落一条墓碑兜底 —— 万一目录被占用删不掉，也不会再复活。
    """
    if run_id in RUNS and not RUNS[run_id].done:
        raise HTTPException(400, "该会话仍在运行，不能删除")
    res = store.delete_run(run_id, purge_disk=True)
    if not res.get("ok"):
        raise HTTPException(404, "run not found")
    RUNS.pop(run_id, None)
    note = "已删除会话记录、事件流、产物清单与该会话的全部磁盘产物"
    if not res.get("purged"):
        note += "（产物目录被占用，已标记不再恢复）"
    return JSONResponse({"ok": True, "run_id": run_id, "purged": res.get("purged"), "note": note})


@app.get("/api/stats")
def api_stats() -> JSONResponse:
    return JSONResponse({**store.stats(), "live_runs": sum(1 for r in RUNS.values() if not r.done)})


if __name__ == "__main__":
    config.WORKSPACE.mkdir(parents=True, exist_ok=True)
    store.init()   # 内含 reap_orphans()：回收上次进程被杀留下的僵尸 running 会话
    # store 是后加的：把磁盘上已有的旧会话补进历史，之前跑完的也能在面板里翻到
    try:
        _bf = store.backfill()
        if _bf:
            print(f"[worker] 回填历史会话 {_bf} 条")
    except Exception as _e:  # noqa: BLE001  回填失败不该挡住启动
        print(f"[worker] 回填历史失败：{type(_e).__name__}: {_e}")
    _d = config.server_default()
    _st = store.stats()
    print(f"[worker] mode={'demo' if (config.MOCK or _d['has_key'] == '0') else 'live'} "
          f"provider={_d['provider']} model={config.MODEL} port={config.PORT}")
    print(f"[worker] db={_st['db']} runs={_st['runs']} events={_st['events']} artifacts={_st['artifacts']}")
    uvicorn.run(app, host="127.0.0.1", port=config.PORT, log_level="warning")
