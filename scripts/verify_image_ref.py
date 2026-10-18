"""真跑一遍：Agnes 串行出三张图，验证图生图链路与一致性。

1. 第一张 render（文生图，作为产品基线）
2. 第二张 render（图生图，只换视角）
3. 第三张 exploded（图生图，基于第一张拆零件）

判定标准：第 2/3 次返回的 url 路径含 /images/i2i/ —— 那是网关切到 i2i 队列的证据。

用法：python scripts/verify_image_ref.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import imagegen  # noqa: E402
import tools  # noqa: E402

events: list[dict] = []
tmp = Path(tempfile.mkdtemp(prefix="imgref_"))
tools.bind_context(tmp, events.append, {})

calls: list[dict[str, Any]] = []
real = imagegen.generate


def spy(**kw: Any) -> dict[str, Any]:
    t0 = time.time()
    r = real(**kw)
    r["_dt"] = round(time.time() - t0, 1)
    calls.append({**kw, "res": r})
    return r


imagegen.generate = spy  # type: ignore[assignment]

BASE = "圆柱形桌面智能夜灯，直径 90mm 高 120mm，哑光米白 ABS 外壳，"
STEPS = [
    {"kind": "render", "prompt": BASE + "顶部 1.3 寸圆形 OLED 显示窗，侧面一个旋钮，等轴测视角"},
    {"kind": "render", "prompt": "换成 45 度侧视，放到浅色木质桌面上，旁边有一杯水作尺度参考"},
    {"kind": "exploded", "prompt": "外壳、顶盖、主控板、电池沿垂直装配轴分离排列，带虚线引导线与零件编号"},
]

for i, step in enumerate(STEPS, 1):
    print(f"\n--- 第 {i} 张：{step['kind']} ---")
    out = tools.generate_product_image.invoke({"prompt": step["prompt"], "kind": step["kind"]})
    print(out)
    c = calls[-1]
    res = c["res"]
    if res.get("ok"):
        url = str(res.get("url") or "")
        mode = "i2i（图生图）" if "/i2i/" in url else "text2img（文生图）"
        print(f"    {mode}  {res.get('bytes', 0)//1024}KB  {res.get('_dt')}s  {url[:90]}")
    else:
        print("    失败:", str(res.get("error"))[:300])
        break

print("\n=== prompts.json ===")
f = tmp / "10_appearance" / "prompts.json"
if f.exists():
    import json

    for it in json.loads(f.read_text(encoding="utf-8")).get("items") or []:
        print(f"  {it.get('kind'):9} file={it.get('file')} reference={it.get('reference') or '(无)'}")

print("\n=== 产物 ===")
for p in sorted(tmp.rglob("*")):
    if p.is_file():
        print(f"  {p.relative_to(tmp)}  {p.stat().st_size//1024}KB")
print("\n工作区:", tmp)
