"""问卷「固定骨架 + 按需求定制选项」的验证。

关注两件事：
1) 框架跨产品完全一致 —— 换个需求，分区、字段 key、题型、顺序、必填不许变；
2) 内容随需求变 —— 换产品，浮上来的选项要跟着换，且命中项要能当默认值。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import clarify  # noqa: E402

REQS = {
    "花盆": "做一个智能花盆，放在阳台上，能检测土壤湿度，土壤干了自动浇水，用太阳能供电，希望外壳是木质的",
    "夜灯": "桌面式温湿度监测小夜灯，OLED 显示温湿度，USB 供电，用 ESP32，暖光氛围灯",
    "工控": "车间设备运行状态监测节点，采集振动和温度，RS485 上报，金属外壳，壁挂安装，220V 市电",
}

ok = 0
fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  [ok] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


def shape(plan) -> list:
    """问卷的「框架指纹」：key + 题型 + 顺序。"""
    return [(q["key"], q["type"], q["required"], q["sticky"]) for q in plan["questions"]]


def main() -> int:
    global ok, fail

    plans = {k: clarify.skeleton_plan(v) for k, v in REQS.items()}

    print("1) 框架跨产品一致")
    base = shape(plans["花盆"])
    for k, p in plans.items():
        check(f"{k}: 字段指纹与花盆一致", shape(p) == base)
        check(f"{k}: 分区一致", [s["id"] for s in p["sections"]] == [s["id"] for s in plans["花盆"]["sections"]])
        check(f"{k}: framework 版本号", p.get("framework") == clarify.FRAMEWORK_ID)

    print("2) 骨架自洽（代码层）")
    keys_in_sections = [k for s in clarify.SECTIONS for k in s["keys"]]
    all_keys = list(clarify.FIELDS.keys())
    check("分区覆盖所有字段", sorted(keys_in_sections) == sorted(all_keys), f"{set(all_keys) ^ set(keys_in_sections)}")
    check("字段无重复", len(keys_in_sections) == len(set(keys_in_sections)))
    check("必填不超过 5 条", sum(1 for f in clarify.FIELDS.values() if f["required"]) <= 5)
    check("题量 12-18", 12 <= len(all_keys) <= 18, f"实际 {len(all_keys)}")

    print("3) 选项随需求变")
    opts_a = {q["key"]: q["options"] for q in plans["花盆"]["questions"]}
    opts_b = {q["key"]: q["options"] for q in plans["夜灯"]["questions"]}
    opts_c = {q["key"]: q["options"] for q in plans["工控"]["questions"]}
    diff_ab = sum(1 for k in opts_a if opts_a[k] != opts_b[k])
    diff_ac = sum(1 for k in opts_a if opts_a[k] != opts_c[k])
    check("花盆 vs 夜灯：至少 4 个字段选项不同", diff_ab >= 4, f"实际 {diff_ab}")
    check("花盆 vs 工控：至少 5 个字段选项不同", diff_ac >= 5, f"实际 {diff_ac}")

    def first_opt(plan, key):
        return next(q for q in plan["questions"] if q["key"] == key)["options"][0]

    check("花盆：场景首选阳台/庭院（需求写了阳台）", "阳台" in first_opt(plans["花盆"], "scenario"), first_opt(plans["花盆"], "scenario"))
    check("花盆：功能模块首选土壤湿度", "土壤" in first_opt(plans["花盆"], "modules"), first_opt(plans["花盆"], "modules"))
    check("花盆：供电首选太阳能", "太阳能" in first_opt(plans["花盆"], "power"), first_opt(plans["花盆"], "power"))
    check("花盆：材质首选木质", "木" in first_opt(plans["花盆"], "material"), first_opt(plans["花盆"], "material"))
    check("工控：场景首选工业/车间", "工业" in first_opt(plans["工控"], "scenario"), first_opt(plans["工控"], "scenario"))
    check("工控：材质首选金属类", ("铝" in first_opt(plans["工控"], "material") or "不锈钢" in first_opt(plans["工控"], "material")), first_opt(plans["工控"], "material"))
    check("工控：供电首选市电", ("市电" in first_opt(plans["工控"], "power") or "220" in first_opt(plans["工控"], "power")), first_opt(plans["工控"], "power"))

    print("4) 命中项直接当默认值")
    def dflt(plan, key):
        return next(q for q in plan["questions"] if q["key"] == key)["default"]

    check("夜灯：mcu 默认 ESP32", dflt(plans["夜灯"], "mcu") == "ESP32", dflt(plans["夜灯"], "mcu"))
    check("夜灯：供电默认 USB", "USB" in dflt(plans["夜灯"], "power"), dflt(plans["夜灯"], "power"))
    check("花盆：供电默认太阳能", "太阳能" in dflt(plans["花盆"], "power"), dflt(plans["花盆"], "power"))
    check("花盆：类目默认园艺/种植", "园艺" in dflt(plans["花盆"], "category"), dflt(plans["花盆"], "category"))
    check("花盆：风格默认自然有机（木质不该判成复古）", "自然" in dflt(plans["花盆"], "style"), dflt(plans["花盆"], "style"))
    check("名称猜得干净（去掉引导词和逗号后内容）", dflt(plans["夜灯"], "product_name") == "桌面式温湿度监测小夜灯", dflt(plans["夜灯"], "product_name"))
    check("花盆名称去掉「做一个」", dflt(plans["花盆"], "product_name") == "智能花盆", dflt(plans["花盆"], "product_name"))
    check("多选不预填（命中项只排前面）", dflt(plans["花盆"], "scenario") == "", repr(dflt(plans["花盆"], "scenario")))

    print("5) 兜底项与合法性")
    for name, p in plans.items():
        for q in p["questions"]:
            if q["type"] in ("select", "multiselect"):
                check(
                    f"{name}/{q['key']}: 选项非空且末项可自由补充",
                    len(q["options"]) >= 2 and clarify._is_other(q["options"][-1]),
                    str(q["options"]),
                )
                check(f"{name}/{q['key']}: 选项无重复", len(q["options"]) == len(set(q["options"])))
                if q["default"]:
                    check(
                        f"{name}/{q['key']}: 默认值在选项里",
                        q["default"] in q["options"] or clarify._is_other(q["default"]),
                        f"{q['default']} not in {q['options']}",
                    )
            else:
                check(f"{name}/{q['key']}: 文本题不带选项", q["options"] == [])

    print("6) 模板覆盖层：只能改文案/选项，改不动框架")
    tmp = Path(tempfile.mkdtemp()) / "clarify-template.json"
    tmp.write_text(
        json.dumps(
            {
                "assumptions": ["测试假设 A"],
                "overrides": {
                    "cost": {"options": ["≤10 元", "≤20 元", "自定义金额"]},
                    "mcu": {"label": "控制器"},
                    "不存在的字段": {"label": "不该生效"},
                },
            }
        ),
        encoding="utf-8",
    )
    orig = clarify.TEMPLATE_PATH
    clarify.TEMPLATE_PATH = tmp
    try:
        p = clarify.skeleton_plan(REQS["夜灯"])
        cost = next(q for q in p["questions"] if q["key"] == "cost")
        mcu = next(q for q in p["questions"] if q["key"] == "mcu")
        check("覆盖：选项被替换", cost["options"] == ["≤10 元", "≤20 元", "自定义金额"], str(cost["options"]))
        check("覆盖：题面被替换", mcu["label"] == "控制器", mcu["label"])
        check("覆盖：假设被替换", p["assumptions"] == ["测试假设 A"], str(p["assumptions"]))
        check("覆盖：陌生 key 被丢弃（框架不变）", shape(p) == base)
        tmp.write_text("{ 这不是合法 json", encoding="utf-8")
        p2 = clarify.skeleton_plan(REQS["夜灯"])
        check("模板写坏：仍返回完整骨架", shape(p2) == base and len(p2["questions"]) == len(base))
        check("模板写坏：假设回落内置", p2["assumptions"] == clarify._ASSUMPTIONS)
        # 旧格式（平铺 questions）也要能读
        tmp.write_text(
            json.dumps({"questions": [{"key": "scenario", "label": "使用场景（旧格式）"}], "assumptions": ["旧格式假设"]}),
            encoding="utf-8",
        )
        p3 = clarify.skeleton_plan(REQS["夜灯"])
        check("旧格式模板：label 覆盖生效", next(q for q in p3["questions"] if q["key"] == "scenario")["label"] == "使用场景（旧格式）")
        check("旧格式模板：框架不变", shape(p3) == base)
    finally:
        clarify.TEMPLATE_PATH = orig

    print("7) 模型定制层：只填选项，改不动框架")
    fake = {
        "ui": {"options": ["1.3 寸 OLED", " capacitive 触摸环", "其他（请补充）"], "default": "1.3 寸 OLED"},
        "power": {"options": ["USB 5V 直供", "其他（请补充）"]},
        "凭空造的题": {"options": ["不该出现"]},
    }
    pm = clarify.skeleton_plan(REQS["夜灯"], fake)
    ui = next(q for q in pm["questions"] if q["key"] == "ui")
    check("模型选项生效", ui["options"][0] == "1.3 寸 OLED", str(ui["options"]))
    check("模型 default 生效", ui["default"] == "1.3 寸 OLED")
    check("模型的陌生 key 被丢", all(q["key"] in clarify.FIELDS for q in pm["questions"]))
    check("模型层不动框架", shape(pm) == base)
    check("source 标记", pm["source"] == "skeleton+model", pm["source"])

    print("8) 简报按问卷顺序输出")
    answers = {"mcu": "ESP32", "product_name": "小夜灯", "scenario": "家居桌面/床头"}
    brief = clarify.build_brief(REQS["夜灯"], answers, plans["夜灯"])
    order = [brief.index(f"**{next(q['label'] for q in plans['夜灯']['questions'] if q['key'] == k)}**") for k in ("product_name", "scenario", "mcu")]
    check("简报字段顺序 = 问卷顺序", order == sorted(order), str(order))
    check("简报含已确认值", "ESP32" in brief and "小夜灯" in brief)

    print("9) 空需求不崩")
    p_empty = clarify.skeleton_plan("")
    check("空需求仍有完整骨架", shape(p_empty) == base)
    check("空需求必填项仍标记", sum(1 for q in p_empty["questions"] if q["required"]) >= 1)

    print(f"\n通过 {ok} 项，失败 {fail} 项")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
