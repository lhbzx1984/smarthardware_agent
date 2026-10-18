"""回归：二进制产物守卫 + 坏图门禁。

真实事故（run `71d4723e3fff`）：`generate_product_image` 成功出了 739 KB / 733 KB 两张
渲染图，随后子智能体用 `save_artifact(..., "placeholder")` 把它们覆盖成 11 字节文本。
前端两张图都打不开，用户看到的是「外观图和渲染图生成后被删除/覆盖」。

本测试钉死三件事：
1. 文本工具不能写二进制产物（save_artifact 直接拒绝，且原文件不被破坏）
2. 坏图 / 占位内容能被识别（魔数 + 体积 + 占位词）
3. 坏图不算阶段产物：门禁不放行、进度不认它
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import assets  # noqa: E402
import config  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def real_png(path: Path, kb: int = 30) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_HEAD + b"\x00" * (kb * 1024))
    return path


print("== 1. assets 纯函数 ==")
check("png 是二进制产物", assets.is_binary("10_appearance/render-01.png"))
check("step/stl 是二进制产物", assets.is_binary("20_structure/model.step") and assets.is_binary("a/b.stl"))
check("md 不是二进制产物", not assets.is_binary("00_prd/prd.md"))
check("拒绝写 png（提示指向正确工具）",
      "generate_product_image" in assets.guard_text_write("10_appearance/render-01.png", "placeholder"))
check("拒绝写 step", bool(assets.guard_text_write("20_structure/model.step", "x")))
check("正常 markdown 放行", assets.guard_text_write("00_prd/prd.md", "# 产品需求规格书\n\n内容……") == "")
check("占位内容被识别", assets.looks_placeholder("placeholder") and assets.looks_placeholder("  TODO "))
check("占位内容整篇被拒", bool(assets.guard_text_write("40_firmware/simulation.md", "TBD")))
check("空内容被拒", bool(assets.guard_text_write("40_firmware/simulation.md", "   ")))

tmp = Path(tempfile.mkdtemp(prefix="guard_"))
run = tmp / "run1"
(run / "10_appearance").mkdir(parents=True)

bad = run / "10_appearance" / "render-01.png"
bad.write_text("placeholder", encoding="utf-8")
check("11 字节伪图被判坏", bool(assets.image_problem(bad)), assets.image_problem(bad))
check("坏图原因是占位文本", "占位" in assets.image_problem(bad), assets.image_problem(bad))

good = real_png(run / "10_appearance" / "render-02.png", 30)
check("正常 PNG 通过", assets.image_problem(good) == "", assets.image_problem(good))

tiny = real_png(run / "10_appearance" / "render-03.png", 2)
check("魔数对但体积过小仍判坏", "字节" in assets.image_problem(tiny), assets.image_problem(tiny))

check("bad_images 挑出两张坏图（伪图 + 过小）",
      sorted(r for r, _ in assets.bad_images(run))
      == ["10_appearance/render-01.png", "10_appearance/render-03.png"],
      str(assets.bad_images(run)))

print("== 2. save_artifact 拒写二进制 + 不动原文件 ==")
import tools  # noqa: E402

config.WORKSPACE = tmp / "ws"
report: list[dict] = []
tools.bind_context(run, report.append)
before = bad.read_bytes()

msg = tools.save_artifact.invoke({"path": "10_appearance/render-01.png", "content": "placeholder"})
check("save_artifact 拒绝覆盖图片", "拒绝" in msg, msg[:120])
check("原文件未被破坏", bad.read_bytes() == before)

msg2 = tools.save_artifact.invoke({"path": "20_structure/model.step", "content": "fake"})
check("save_artifact 拒绝写 step", "拒绝" in msg2, msg2[:120])

msg3 = tools.save_artifact.invoke({"path": "00_prd/prd.md", "content": "# 产品需求\n\n真实内容"})
check("正常文档照常写入", "已写入" in msg3, msg3[:120])

print("== 3. 坏图不进阶段产物（门禁 + 进度）==")
import pipeline  # noqa: E402
import stages  # noqa: E402

(run / "00_prd").mkdir(exist_ok=True)
(run / "00_prd" / "prd.md").write_text("# PRD\n\n尺寸 60×35×15mm", encoding="utf-8")
(run / "10_appearance" / "appearance.md").write_text("# 外观\n\n圆角矩形", encoding="utf-8")
(run / "10_appearance" / "prompts.json").write_text('{"prompts":[{"prompt":"圆角矩形夹子"}]}', encoding="utf-8")
# 只留那张伪图 render-01.png：模拟线上现场（真图被覆盖成 placeholder）
for extra in ("render-02.png", "render-03.png"):
    p = run / "10_appearance" / extra
    if p.exists():
        p.unlink()

paths = stages.scan_dir(run)
check("坏图不在 scan_dir 结果里", not any("render-01.png" in p for p in paths), str(paths))

app = pipeline.appearance_state(run)
check("appearance_state 报出坏图", len(app["broken"]) == 1, str(app))
check("有效图为 0 张", app["n_images"] == 0, str(app))
check("有坏图时 ready=False", app["ready"] is False, str(app))

g = pipeline.gate(run, "20_structure")
check("结构阶段门禁不放行", g["ready"] is False, str(g["missing"]))
check("门禁提示要重出图", any("损坏" in m or "重出" in m for m in g["missing"]), str(g["missing"]))

# 删掉坏图后：只剩提示词 → 允许「文字同源」继续，不能把整条链卡死
bad.unlink()
app2 = pipeline.appearance_state(run)
check("无坏图时 text_only_ok 为真", app2["text_only_ok"] is True, str(app2))
# 门禁现在多了一道人工确认闸：产物条件满足后还要用户点过确认才放行
g2a = pipeline.gate(run, "20_structure")
check("文字同源但未经用户确认 → 仍不放行",
      any("还没经用户确认" in m for m in g2a["missing"]), str(g2a["missing"]))
import hitl  # noqa: E402
hitl.save_record(run, "10_appearance", hitl.APPROVE, "")
g2 = pipeline.gate(run, "20_structure")
check("用户确认后 → 文字同源自此放行", g2["ready"] is True, str(g2["missing"]))

# 换成一张真图 → 正常就绪
real_png(run / "10_appearance" / "render-01.png", 40)
app3 = pipeline.appearance_state(run)
check("真图落盘后 ready=True 且无坏图", app3["ready"] and not app3["broken"], str(app3))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
