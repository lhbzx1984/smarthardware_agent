"""外观→结构同源自检：Agnes 出图 + 提示词存档 + text-to-cad 复用外观提示词建模。

钉死三件事：
1. 产品图（渲染图/爆炸图）走 Agnes，与文本模型网关解耦。
2. 出图提示词**自动落盘**，结构阶段拿得到 —— 不靠模型口头转述。
3. text_to_cad 能把外观提示词取回来当形状约束，建模后自动出视图与一致性对照表。

用法：python scripts/test_visual_chain.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import tools  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def test_image_provider() -> None:
    print("[1] 出图服务商固定为 Agnes（与文本模型解耦）")
    base, model, provider = config.resolve_image_provider()
    check("默认出图服务商是 agnes", provider == "agnes", f"{provider} / {base}")
    check("端点指向 Agnes", "agnes" in base.lower(), base)
    check("配了对应的密钥", bool(config.fallback_key(base)))
    check("有默认图像模型", bool(model), model)


def test_prompt_archived() -> None:
    print("[2] 出图提示词自动存档（结构阶段的唯一可靠来源）")
    tmp = Path(tempfile.mkdtemp())
    captured: dict[str, Any] = {}
    events: list[dict] = []

    import imagegen

    def fake_generate(**kw: Any) -> dict[str, Any]:
        captured.update(kw)
        dest = Path(kw["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        return {"ok": True, "bytes": dest.stat().st_size, "elapsed": 1.0,
                "model": "agnes-image-2.1-flash", "prompt": kw["prompt"]}

    imagegen.generate = fake_generate  # type: ignore[assignment]
    tools.bind_context(tmp, events.append, {"base_url": "https://api.siliconflow.cn/v1"})

    prompt = "圆柱形桌面小夜灯，直径 90mm 高 120mm，哑光米白外壳，顶部 1.3 寸 OLED"
    out = tools.generate_product_image.invoke({"prompt": prompt, "kind": "render"})
    check("调用成功", "已生成" in out, out[:120])
    check("出图端点不跟随文本模型网关", "agnes" in str(captured.get("base_url", "")), str(captured.get("base_url")))

    f = tmp / "10_appearance" / "prompts.json"
    check("提示词已存档", f.exists(), str(f))
    if f.exists():
        import json

        data = json.loads(f.read_text(encoding="utf-8"))
        items = data.get("items") or []
        check("存档含本次提示词", bool(items) and prompt in str(items[-1].get("prompt", "")), str(items[:1]))
        check("存档记录了图片路径", bool(items) and "render-01.png" in str(items[-1].get("file", "")))

    ctx = tools.load_appearance_context(tmp)
    check("结构阶段能读回外观提示词", bool(ctx.get("prompts")) and prompt in str(ctx["prompts"][-1]))


def test_text_to_cad_without_script() -> None:
    print("[3] text_to_cad 不带脚本 → 交回外观约束")
    tmp = Path(tempfile.mkdtemp())
    (tmp / "10_appearance").mkdir(parents=True)
    (tmp / "10_appearance" / "prompts.json").write_text(
        '{"items":[{"kind":"render","prompt":"圆柱小夜灯 直径90 高120","file":"10_appearance/render-01.png"}]}',
        encoding="utf-8",
    )
    tools.bind_context(tmp, lambda e: None, {})
    out = tools.text_to_cad.invoke({})
    check("返回外观阶段提示词", "圆柱小夜灯" in out, out[:150])
    check("返回建模模板", "BuildPart" in out and "build123d" in out)

    # 模型会自己写一份 prompts.json（还带 structure_notes）把工具存档覆盖掉 —— 两种格式都要认
    tmp2 = Path(tempfile.mkdtemp())
    (tmp2 / "10_appearance").mkdir(parents=True)
    (tmp2 / "10_appearance" / "prompts.json").write_text(
        '{"product":"桌面小夜灯","prompts":[{"id":"prompt-01","kind":"render",'
        '"filename":"render-01.png","prompt":"圆柱形小夜灯 直径90mm 高120mm 哑光米白"}],'
        '"structure_notes":{"外形约束":"圆柱体，直径90mm，总高120mm","关键特征":["顶部圆形OLED"]}}',
        encoding="utf-8",
    )
    tools.bind_context(tmp2, lambda e: None, {})
    ctx2 = tools.load_appearance_context(tmp2)
    check("认模型手写的 prompts 格式", bool(ctx2.get("prompts")) and "直径90mm" in str(ctx2["prompts"][0]))
    check("取到 structure_notes 外形约束", "直径90mm" in str(ctx2.get("notes")))
    out2 = tools.text_to_cad.invoke({})
    check("无脚本时交回外形约束", "外形约束" in out2 or "直径90mm" in out2, out2[:200])


def test_text_to_cad_build() -> None:
    print("[4] text_to_cad 带脚本 → 建模 + 视图 + 一致性对照")
    tmp = Path(tempfile.mkdtemp())
    (tmp / "10_appearance").mkdir(parents=True)
    (tmp / "10_appearance" / "prompts.json").write_text(
        '{"items":[{"kind":"render","prompt":"圆柱小夜灯 直径90 高120","file":"10_appearance/render-01.png"}]}',
        encoding="utf-8",
    )
    events: list[dict] = []
    tools.bind_context(tmp, events.append, {})

    script = """
from build123d import *
W, D, H = 90.0, 90.0, 120.0
WALL, R = 2.0, 10.0
with BuildPart() as shell:
    Cylinder(radius=W/2, height=H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((0, 0, WALL)):
        Cylinder(radius=W/2-WALL, height=H-WALL, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
with BuildPart() as lid:
    Cylinder(radius=W/2, height=6, align=(Align.CENTER, Align.CENTER, Align.MIN))
parts = {"外壳": shell.part, "顶盖": lid.part.moved(Location((0, 0, H)))}
"""
    out = tools.text_to_cad.invoke({"script": script, "base_name": "lamp"})
    if "建模失败" in out:
        check("建模成功", False, out[:400])
        return
    check("建模成功", "建模成功" in out, out[:200])
    struct = tmp / "20_structure"
    check("导出 STEP", any(struct.glob("*.step")), str(list(struct.glob("*"))[:5]))
    check("导出 STL", any(struct.glob("*.stl")))
    check("脚本已存档", (struct / "model.py").exists())
    check("自动出了四视图", (tmp / "10_appearance" / "views-sheet.svg").exists())
    check("自动出了爆炸图", (tmp / "10_appearance" / "exploded.svg").exists())
    cons = struct / "consistency.md"
    check("写出一致性对照表", cons.exists())
    if cons.exists():
        text = cons.read_text(encoding="utf-8")
        check("对照表含外观提示词", "圆柱小夜灯" in text, text[:200])
        check("对照表含待核对项", "待核对项" in text)


def test_build_without_appearance() -> None:
    print("\n[5] 外观未出图就建模：必须被拦下并钉进产物")
    tmp = Path(tempfile.mkdtemp())
    tools.bind_context(tmp, lambda e: None, {})

    # 场景 A：不带脚本只问约束 —— 直接告知外观未就绪，不要给模板让模型硬建模
    out = tools.text_to_cad.invoke({})
    check("无外观时不给建模模板", "BuildPart" not in out, out[:120])
    check("说明外观未就绪", "外观阶段还没有产物" in out or "还没有产物" in out, out[:160])
    check(
        "给出可操作的处理步骤",
        "render_product_views" in out and "industrial-designer" in out,
        out[:300],
    )

    # 场景 B：带脚本硬建模 —— 放行，但一致性文件必须写明「未同源」
    (tmp / "20_structure").mkdir(parents=True, exist_ok=True)
    script = (
        "from build123d import *\n"
        "with BuildPart() as shell:\n"
        "    Box(90, 90, 120, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        "parts = {'外壳': shell.part}\n"
    )
    out2 = tools.text_to_cad.invoke({"script": script, "base_name": "nightlight"})
    if "建模成功" not in out2:
        print("    （CAD 不可用，跳过场景 B 的产物断言）", out2[:160])
        return
    cons = tmp / "20_structure" / "consistency.md"
    check("一致性文件已写", cons.exists())
    text = cons.read_text(encoding="utf-8") if cons.exists() else ""
    check("顶部钉了「未同源」告警", "未与外观同源" in text, text[:200])
    check("告警说明不能声称一致", "不能声称与外观图一致" in text)


def main() -> int:
    test_image_provider()
    test_prompt_archived()
    test_text_to_cad_without_script()
    test_text_to_cad_build()
    test_build_without_appearance()
    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
