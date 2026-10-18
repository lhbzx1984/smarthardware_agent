"""实测：出图接口 + CDN 下载在真实网络与证书环境下的完整链路。"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import imagegen  # noqa: E402
import tools  # noqa: E402

run = Path(tempfile.mkdtemp(prefix="img_live_"))
sink: list[dict] = []
tools.bind_context(run, lambda e: sink.append(e))

t0 = time.time()
out = tools.generate_product_image.func(
    prompt="便携式土壤湿度检测仪，白色外壳配绿色包胶，OLED 小屏，底部两根不锈钢探针",
    kind="render",
    filename="10_appearance/render-01.png",
)
print(out[:600])
print()
print("耗时 %.1fs" % (time.time() - t0))
files = sorted(p.name for p in (run / "10_appearance").iterdir()) if (run / "10_appearance").exists() else []
print("落盘文件:", files)
for e in sink:
    if e.get("type") in ("artifact", "tool_note", "tool_result"):
        print("  event:", e.get("type"), e.get("name", ""), str(e.get("path") or e.get("detail") or "")[:160])

# 第二张：基于第一张做图生图（验证一致性链与下载复用）
if (run / "10_appearance" / "render-01.png").exists():
    print("\n--- 第二张（reference=auto，基于第一张）---")
    out2 = tools.generate_product_image.func(
        prompt="同一款产品，45 度俯视，展示屏幕与按键布局",
        kind="render",
        filename="10_appearance/render-02.png",
    )
    print(out2[:400])
    print("落盘文件:", sorted(p.name for p in (run / "10_appearance").iterdir()))
