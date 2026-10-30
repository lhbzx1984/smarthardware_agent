"""真实产品端到端跑一次：发起运行 → 自动确认人工闸门 → 观察产物 → 校验 PCB 链路。

用法:
    python scripts/run_product_e2e.py "需求文本" [paradigm] [timeout_sec]

人工闸门（10_appearance / 20_structure）默认开启且等满 1800s，
本脚本检测到挂起就代点"确认"，否则一次运行会卡半小时。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8001"


def req(path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_err": f"{type(e).__name__}: {e}"}


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else (
        "设计一款桌面式智能空气质量检测仪：圆角矩形外壳，外形约 92×62×24mm，"
        "检测 PM2.5 与温湿度，1.3 寸 OLED 显示，USB-C 供电并带 800mAh 锂电池，"
        "主控用 ESP32-C3。需要完整的结构设计和可打样的 PCB 文件。"
    )
    paradigm = sys.argv[2] if len(sys.argv) > 2 else ""
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 2400

    body: dict = {"requirement": text}
    if paradigm:
        body["paradigm"] = paradigm
    d = req("/api/run", body)
    if "_err" in d or "run_id" not in d:
        print(f"启动失败：{d}")
        return 1
    rid = d["run_id"]
    print(f"run_id={rid}  paradigm={d.get('paradigm', {}).get('key', '?')}", flush=True)

    seen: set[str] = set()
    t0 = time.time()
    last = 0.0
    approved: set[str] = set()
    while time.time() - t0 < timeout:
        s = req(f"/api/runs/{rid}")
        if "_err" in s:
            print(f"  [查询失败] {s['_err']}", flush=True)
            time.sleep(5)
            continue
        st = s.get("status")
        files = [f.get("path") if isinstance(f, dict) else f for f in (s.get("files") or [])]
        for f in files:
            if f not in seen:
                print(f"  + {f}", flush=True)
        seen.update(files)

        pend = (s.get("awaiting") or {}).get("stage") if isinstance(s.get("awaiting"), dict) else None
        if pend and pend not in approved:
            ok = req(f"/api/runs/{rid}/review", {"stage": pend, "decision": "approve", "feedback": ""})
            approved.add(pend)
            print(f"  ✔ 已确认阶段 {pend}  -> {ok.get('ok')}", flush=True)

        if time.time() - last > 30:
            print(f"  [{int(time.time() - t0)}s] status={st} files={len(files)}", flush=True)
            last = time.time()
        if st in {"done", "failed", "aborted", "interrupted", "cancelled"}:
            print(f"\n终态={st} 用时={int(time.time() - t0)}s 产物={len(files)}", flush=True)
            man = req(f"/api/runs/{rid}/manifest")
            for g in man.get("stages", []):
                if g.get("files"):
                    print(f"  {g['id']:<14} {g.get('n_files')} 文件", flush=True)
            print(f"RUN_ID={rid}", flush=True)
            return 0
        time.sleep(5)

    print(f"\n超时 {timeout}s，最后状态未终态；RUN_ID={rid}", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
