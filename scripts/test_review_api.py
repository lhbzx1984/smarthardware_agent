"""回归：人工确认闸门的 HTTP 接口（前端就靠这两个端点）。

单元测试覆盖了 hitl 的阻塞与门禁；这里补的是「前端 → worker」这一段：
GET 能不能查到等待中的阶段、POST 能不能把用户的结论送进去并唤醒挂起的线程。
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


import store  # noqa: E402

store.create_run = lambda *a, **k: None          # type: ignore[assignment]
store.append = lambda *a, **k: None              # type: ignore[assignment]
store.recall = lambda *a, **k: []                # type: ignore[assignment]

import hitl  # noqa: E402
import server  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="reviewapi_"))
client = None
try:
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
except Exception as e:  # noqa: BLE001  没装 httpx 就跳过 HTTP 段
    print(f"  ⚠ 跳过 HTTP 段（TestClient 不可用：{type(e).__name__}）")

print("\n【1】GET /review —— 查等待中的阶段")
run = server.Run("设计一个桌面温湿度小夜灯")
run.dir = tmp
server.RUNS[run.id] = run

if client is not None:
    r = client.get(f"/api/runs/{run.id}/review")
    check("没有等待时 pending 为空", r.status_code == 200 and r.json().get("pending") is None, r.text[:120])

box: dict = {}


def waiter() -> None:
    box["res"] = hitl.request(run, "10_appearance", "三张图已出：主视、45°、爆炸",
                              ["10_appearance/render-01.png"])


t = threading.Thread(target=waiter, daemon=True)
t.start()
deadline = time.time() + 5
while time.time() < deadline and run.awaiting is None:
    time.sleep(0.02)
check("线程已进入等待", run.awaiting is not None, str(run.awaiting))

if client is not None:
    r = client.get(f"/api/runs/{run.id}/review")
    pend = (r.json() or {}).get("pending") or {}
    check("GET 能查到等待中的阶段", pend.get("stage") == "10_appearance", r.text[:200])
    check("GET 带上产物清单", "render-01.png" in " ".join(pend.get("files") or []), str(pend))
    check("GET 给出等待上限", int(pend.get("wait_seconds") or 0) > 0, str(pend))

    print("\n【2】POST /review —— 提交修改意见")
    r2 = client.post(
        f"/api/runs/{run.id}/review",
        json={"decision": "revise", "feedback": "换成暖白光，高度压到 40mm"},
    )
    check("POST 返回 ok", r2.status_code == 200 and r2.json().get("ok") is True, r2.text[:200])
    check("POST 回显阶段与决策",
          r2.json().get("stage") == "10_appearance" and r2.json().get("decision") == "revise",
          r2.text[:200])

t.join(timeout=10)
res = box.get("res") or {}
check("线程被唤醒并返回", res.get("decision") == hitl.REVISE, str(res))
check("用户原话原样带回来", "暖白光" in str(res.get("feedback")), str(res))
check("不再处于等待态", run.awaiting is None, str(run.awaiting))

print("\n【3】POST 时已经不在等待 —— 结论照样落盘（门禁读得到）")
if client is not None:
    r3 = client.post(
        f"/api/runs/{run.id}/review",
        json={"decision": "approve", "feedback": "", "stage": "20_structure"},
    )
    check("未等待时返回 ok=False 但不报错", r3.status_code == 200, r3.text[:200])
    check("结论仍写进磁盘", hitl.approved(run.dir, "20_structure") is True, "")
    r4 = client.post(f"/api/runs/{run.id}/review", json={"decision": "approve"})
    check("没带 stage 时给出提示而不是 500", r4.status_code == 200, r4.text[:200])

print("\n【4】会话详情里带上等待状态")
if client is not None:
    run2 = server.Run("另一台产品")
    run2.dir = tmp
    server.RUNS[run2.id] = run2
    box2: dict = {}

    def waiter2() -> None:
        box2["res"] = hitl.request(run2, "20_structure", "模型已出")

    t2 = threading.Thread(target=waiter2, daemon=True)
    t2.start()
    while run2.awaiting is None:
        time.sleep(0.02)
    try:
        store.get_run = lambda *a, **k: {"run_id": run2.id, "status": "running"}  # type: ignore[assignment]
        r5 = client.get(f"/api/runs/{run2.id}")
        check("详情接口带 awaiting 字段",
              (r5.json() or {}).get("awaiting", {}).get("stage") == "20_structure", r5.text[:200])
    finally:
        run2.cancel()
        t2.join(timeout=10)

print(f"\n{'=' * 46}\n通过 {PASS} 项，失败 {FAIL} 项\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
