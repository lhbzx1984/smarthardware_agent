"""网关「等待-恢复」窗口回归：探活不通不再一击判死。

跑法：
    python scripts/test_gateway_wait.py

场景对照（对应 2026-10-13 真实事故：两次连接失败 + 一次探活超时 → run 被杀，
而网关几十秒后就恢复了）：
  1. 探活先不通、第 3 次恢复 → 等待窗口返回 True，run 续跑，不抛 RuntimeError
  2. 持续不通到上限 → 返回 False，上层才报错
  3. 等待中用户点停止 → RunCancelled 立刻穿透（不能闷头睡到上限）
  4. 等待时长豁免 deadline（started 被推后 ≈ 等待秒数 + 探活耗时）
  5. _run_with_retry 判死路径：mock 网关先死 2 次后恢复 → run_agent 被重跑成功
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import config  # noqa: E402
import guards  # noqa: E402
import server  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}  {detail}")


class FakeRun:
    """最小 Run 替身：_wait_gateway / _run_with_retry 用到的接口。"""

    def __init__(self) -> None:
        self.started = time.time()
        self.cancel_requested = False
        self.warns: list[str] = []
        self.settings = {"base_url": "https://gw.example/v1", "api_key": "k"}
        self.gateway_state = ""
        self.requirement = "需求"
        self.base_requirement = "需求"
        self.dir = Path(".")
        self.paradigm = None  # fake run_agent 不读它

    def push(self, ev: dict) -> None:
        if ev.get("type") == "warn":
            self.warns.append(str(ev.get("message", "")))

    def extend_deadline(self, seconds: float) -> None:
        self.started += seconds


# ---- 用小参数加速测试（秒级窗口），跑完还原 ---------------------------------
ORIG = (config.GATEWAY_WAIT_MAX, config.GATEWAY_PROBE_INTERVAL)
config.GATEWAY_WAIT_MAX = 8.0
config.GATEWAY_PROBE_INTERVAL = 1.0

orig_alive = server._gateway_alive
try:
    # 1) 先不通后恢复 → True
    calls = {"n": 0}

    def alive_flaky(run) -> bool:  # noqa: ANN001
        calls["n"] += 1
        return calls["n"] >= 3

    server._gateway_alive = alive_flaky
    r = FakeRun()
    t0 = time.time()
    ok = server._wait_gateway(r)
    el = time.time() - t0
    check("探活第3次恢复 → 续跑", ok is True)
    check("等待时长合理（2~6s）", 2.0 <= el <= 6.0, f"el={el:.1f}")
    check("有恢复提示", any("已恢复" in w for w in r.warns), str(r.warns))

    # 2) 持续不通 → False（到上限）
    server._gateway_alive = lambda run: False
    r2 = FakeRun()
    t0 = time.time()
    ok2 = server._wait_gateway(r2)
    el2 = time.time() - t0
    check("持续不通到上限 → False", ok2 is False)
    check("上限生效（≈8s+）", 8.0 <= el2 <= 13.0, f"el={el2:.1f}")
    check("有等待进度提示", any("仍不可达" in w for w in r2.warns), str(r2.warns))

    # 3) 等待中取消 → RunCancelled 秒级穿透
    server._gateway_alive = lambda run: False
    r3 = FakeRun()

    def cancel_soon() -> None:
        time.sleep(2.0)
        r3.cancel_requested = True

    import threading

    th = threading.Thread(target=cancel_soon, daemon=True)
    th.start()
    t0 = time.time()
    cancelled = False
    try:
        server._wait_gateway(r3)
    except guards.RunCancelled:
        cancelled = True
    el3 = time.time() - t0
    check("取消立刻穿透（≤3s）", cancelled and el3 <= 3.5, f"cancelled={cancelled} el={el3:.1f}")

    # 4) deadline 豁免：等待 + 探活耗时都被推后
    r4 = FakeRun()
    started0 = r4.started
    server._gateway_alive = lambda run: False
    server._wait_gateway(r4)
    drift = r4.started - started0
    check("deadline 豁免 ≈ 等待时长", drift >= 8.0, f"drift={drift:.1f}")

    # 5) _run_with_retry：连续网络错误 → 进等待窗口 → 恢复后重跑成功
    server._gateway_alive = alive_flaky  # 前 2 次不通，第 3 次通
    calls["n"] = 0
    agent_calls = {"n": 0}

    def fake_agent(req, dir_, push, settings, paradigm, run):  # noqa: ANN001
        agent_calls["n"] += 1
        if agent_calls["n"] < 3:
            raise ConnectionError("Connection error.")
        return "最终交付摘要"

    import main_agent  # noqa: E402

    orig_run_agent = main_agent.run_agent
    main_agent.run_agent = fake_agent
    orig_net_retry_max = config.NET_RETRY_MAX
    orig_dead_after = config.GATEWAY_DEAD_AFTER
    orig_fallback_after = config.NET_FALLBACK_AFTER
    config.NET_RETRY_MAX = 5
    config.GATEWAY_DEAD_AFTER = 1      # 第 1 次失败就探活
    config.NET_FALLBACK_AFTER = 99     # 本测试不换模型
    try:
        r5 = FakeRun()
        t0 = time.time()
        # _run_with_retry 内部 from main_agent import run_agent —— 打的是模块属性，上面已替换
        final = server._run_with_retry(r5)
        el5 = time.time() - t0
        check("网关恢复后 run 完成", final == "最终交付摘要")
        check("run_agent 重跑了 3 次", agent_calls["n"] == 3, str(agent_calls))
        check("等过恢复窗口（≥2s）", el5 >= 2.0, f"el={el5:.1f}")
        check("有恢复/重跑提示", any("已恢复" in w or "重跑" in w for w in r5.warns), str(r5.warns)[:200])
    finally:
        main_agent.run_agent = orig_run_agent
        config.NET_RETRY_MAX = orig_net_retry_max
        config.GATEWAY_DEAD_AFTER = orig_dead_after
        config.NET_FALLBACK_AFTER = orig_fallback_after
    # 6) 网关通、但每个候选都 402/401 → 判为 quota：不开跑，也不进等待窗口
    #    （真实事故：硅基流动账号余额为 0，/models 200 却每次调用 402，
    #     旧逻辑会当成「网关不可达」白等满 600 秒才报同一句话）
    import gateway  # noqa: E402

    check("classify: 全 402 → quota", gateway.classify([("a", None, "HTTP402"), ("b", None, "HTTP402")]) == "quota")
    check("classify: 全连接超时 → unreachable", gateway.classify([("a", None, "ConnectTimeout")]) == "unreachable")
    check("classify: 有可用模型 → 开跑", gateway.classify([("a", 1.2, ""), ("b", None, "HTTP402")]) == "")

    orig_pick, orig_reachable = gateway.pick_fastest, gateway.reachable
    reach_calls = {"n": 0}
    gateway.pick_fastest = lambda base, key, cands, timeout=45.0: ("", 0.0, [(c, None, "HTTP402") for c in cands])
    gateway.reachable = lambda *a, **kw: (reach_calls.__setitem__("n", reach_calls["n"] + 1) or True)
    orig_cands = config.MODEL_RACE_CANDIDATES
    config.MODEL_RACE_CANDIDATES = ["model-b"]
    try:
        r6 = FakeRun()
        t0 = time.time()
        ok6 = server._maybe_race_model(r6)
        el6 = time.time() - t0
        check("402 一律不开跑", ok6 is False)
        check("标记为 quota", r6.gateway_state == "quota", r6.gateway_state)
        check("不进等待窗口（≤2s，旧逻辑会睡 10s）", el6 <= 2.0, f"el={el6:.1f}")
        check("不再探活 /models", reach_calls["n"] == 0, str(reach_calls))
        check("提示指向余额/鉴权而非不可达", any("余额不足" in w for w in r6.warns), str(r6.warns)[:200])
    finally:
        config.MODEL_RACE_CANDIDATES = orig_cands

    # 7) 连不上但 /models 通 → 原行为不变（照常开跑，只是慢）
    gateway.pick_fastest = lambda base, key, cands, timeout=45.0: ("", 0.0, [(c, None, "ConnectTimeout") for c in cands])
    try:
        r7 = FakeRun()
        ok7 = server._maybe_race_model(r7)
        check("连接超时但 /models 通 → 照常开跑", ok7 is True)
        check("未误判为 quota", r7.gateway_state != "quota", r7.gateway_state)
    finally:
        gateway.pick_fastest, gateway.reachable = orig_pick, orig_reachable
finally:
    server._gateway_alive = orig_alive
    config.GATEWAY_WAIT_MAX, config.GATEWAY_PROBE_INTERVAL = ORIG

print(f"\n{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
