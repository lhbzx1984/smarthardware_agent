"""实跑验证：确认 tool_start 的 args 不再为空、只读重复不再被误杀。

用法：python scripts/verify_loop_guard.py [观察秒数]
只观察不写库（读 SSE 事件流），跑完打印统计。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import Counter

BASE = "http://127.0.0.1:8001"
WATCH = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
REQ = "一台 USB 供电的桌面小风扇：直径 120mm，三档风速，静音优先，成本 40 元内。"


def post(path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


res = post("/api/run", {"requirement": REQ, "paradigm": "lean", "settings": {}})
rid = res["run_id"]
print(f"run={rid} paradigm={res['paradigm']['label']}")

# 轮询只为等它跑一段；事件一次性全量读取 —— /api/runs/{id}/events 不返回 seq，
# 增量续传会重复计数（实测把 7 次调用数成 41 次），这里不能按游标累加。
status = ""
deadline = time.time() + WATCH
while time.time() < deadline:
    try:
        info = get(f"/api/runs/{rid}")
    except Exception as e:  # noqa: BLE001
        print("拉取状态失败:", e)
        break
    if not info.get("live", True):
        status = "done"
        break
    time.sleep(3)

starts: list[dict] = []
warns: list[str] = []
for e in get(f"/api/runs/{rid}/events?after=0&limit=20000").get("events", []):
    t = e.get("type")
    if t == "tool_start":
        starts.append({"name": e.get("name"), "args": e.get("args") or ""})
    elif t == "warn":
        warns.append(str(e.get("message")))
    elif t == "done":
        status = "done"

def useless(a: str) -> bool:
    return str(a).strip() in ("", "{}", "[]", "null", "None")


empty = [s for s in starts if useless(s["args"])]
print(f"\n工具调用 {len(starts)} 次，其中参数无信息量 {len(empty)} 条")
for s in empty[:5]:
    print("   空参数:", s["name"])
sample = [s for s in starts if not useless(s["args"])][:6]
print("\n参数样本：")
for s in sample:
    print(f"   {s['name']}: {str(s['args'])[:90]}")
sig = Counter((s["name"], str(s["args"])[:80]) for s in starts)
print("\n重复最多的签名：")
for k, v in sig.most_common(6):
    print(f"   {v:>3} × {k[0]}  args={k[1][:60]!r}")
if warns:
    print("\n告警：")
    for w in warns[:8]:
        print("   -", w[:120])
else:
    print("\n无告警（没有触发重复提示 / 中止）")
run_info = get(f"/api/runs/{rid}")
print(f"\n终态 status={run_info.get('run', {}).get('status')} error={run_info.get('run', {}).get('error') or '-'}")
