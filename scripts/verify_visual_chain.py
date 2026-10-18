"""端到端验证：产品图走 Agnes + 结构图由外观提示词驱动（text-to-cad）。

用法：python scripts/verify_visual_chain.py [观察秒数]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
WATCH = float(sys.argv[1]) if len(sys.argv) > 1 else 420.0
REQ = (
    "一台 USB 供电的桌面圆柱形小夜灯：直径 90mm、高 120mm，哑光米白外壳，"
    "顶部 1.3 寸 OLED 显示温湿度，单颗电容触摸键调光，主控 ESP32-S3，成本 80 元内。"
)

EXPECT = [
    "10_appearance/appearance.md",
    "10_appearance/prompts.json",          # 出图提示词存档（结构同源的来源）
    "10_appearance/render-01.png",         # Agnes 渲染图
    "10_appearance/exploded.svg",          # 真实几何爆炸图
    "10_appearance/views-sheet.svg",       # 四视图
    "20_structure/structure.md",
    "20_structure/model.py",               # text_to_cad 存档的建模脚本
    "20_structure/consistency.md",         # 外观-结构对照表
]


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


res = post("/api/run", {"requirement": REQ, "paradigm": "standard", "settings": {}})
rid = res["run_id"]
print(f"run={rid} paradigm={res['paradigm']['label']}", flush=True)

deadline = time.time() + WATCH
while time.time() < deadline:
    try:
        info = get(f"/api/runs/{rid}")
    except Exception as e:  # noqa: BLE001
        print("状态拉取失败:", e)
        break
    if not info.get("live", True):
        break
    time.sleep(5)

files = get(f"/api/runs/{rid}/files")
paths = [f.get("path", "") for f in files.get("files", [])]
print(f"\n产物 {len(paths)} 个")
hit = {p: any(p in x for x in paths) for p in EXPECT}
for p, ok in hit.items():
    print(f"  {'OK ' if ok else '-- '} {p}")

pngs = [p for p in paths if p.endswith(".png")]
steps = [p for p in paths if p.endswith(".step")]
print(f"\nPNG（Agnes 出图）{len(pngs)}: {pngs[:6]}")
print(f"STEP {len(steps)}: {steps[:6]}")

# 一致性：提示词存档 vs 结构对照表
try:
    raw = get(f"/api/runs/{rid}/file?path=10_appearance/prompts.json")
    txt = raw.get("content") or ""
    print("\n出图提示词存档：", txt[:300].replace("\n", " "))
except Exception as e:  # noqa: BLE001
    print("读取 prompts.json 失败:", e)
try:
    raw = get(f"/api/runs/{rid}/file?path=20_structure/consistency.md")
    print("\n一致性对照表：\n", (raw.get("content") or "")[:700])
except Exception as e:  # noqa: BLE001
    print("读取 consistency.md 失败:", e)

info = get(f"/api/runs/{rid}")
print(f"\n终态 status={info.get('status')} 用时={round(info.get('elapsed') or 0)}s")
