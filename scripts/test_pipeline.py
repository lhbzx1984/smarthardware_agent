"""阶段流水线回归：串行门禁 / 跨阶段约束传递 / 交付清单 / 打包下载。

跑法：
    python scripts/test_pipeline.py

重点是「依赖链真的拦得住」：上游没产物时下游必须被挡住，
补齐后才放行 —— 智能硬件各阶段的耦合（PCB 板型 ← 结构内腔 ← 外观外形）
靠的就是这道门禁。
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import pipeline  # noqa: E402
import stages  # noqa: E402

PASS = FAIL = 0


def ok(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}")


def w(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pipeline-"))
    try:
        # ------------------------------------------------ 1. 链与元数据
        print("\n[1] 阶段链与依赖声明")
        ok(stages.CHAIN == ["00_prd", "10_appearance", "20_structure",
                            "30_hardware", "40_firmware", "90_review"], "串行链顺序正确")
        ok(stages.depends_of("20_structure") == ["10_appearance"], "结构依赖外观")
        ok(stages.depends_of("30_hardware") == ["20_structure"], "硬件依赖结构")
        ok(stages.depends_of("40_firmware") == ["30_hardware"], "固件依赖硬件")
        ok(all(stages.deliverables_of(s) for s in stages.CHAIN), "每阶段都有交付契约")

        # ------------------------------------------------ 2. 空目录门禁
        print("\n[2] 空目录：下游阶段一律不许开工")
        g20 = pipeline.gate(tmp, "20_structure")
        ok(not g20["ready"], "结构（无外观图）被拦住")
        g30 = pipeline.gate(tmp, "30_hardware")
        ok(not g30["ready"], "硬件（无结构尺寸）被拦住")
        g40 = pipeline.gate(tmp, "40_firmware")
        ok(not g40["ready"], "固件（无网表/BOM）被拦住")
        ok(pipeline.gate(tmp, "00_prd")["ready"], "PRD 无依赖，可直接开工")
        snap = pipeline.snapshot(tmp)
        ok(snap["next"] == "00_prd", f"下一个阶段 = {snap['next']}")

        # ------------------------------------------------ 3. 逐步补齐 → 逐段放行
        print("\n[3] 补齐上游后逐段放行")
        w(tmp, "00_prd/prd.md", "# PRD\n尺寸 ≤120×80×40mm\n")
        ok(pipeline.gate(tmp, "10_appearance")["ready"], "有 PRD → 外观可开工")
        w(tmp, "10_appearance/appearance.md", "# 外观\n")
        w(tmp, "10_appearance/prompts.json", json.dumps({"items": [{"prompt": "立方体小夜灯 120×80×40mm"}]}))
        # 人工确认闸门：产物齐了还不够，必须用户在界面上点过确认
        ok(not pipeline.gate(tmp, "20_structure")["ready"], "产物齐但未经用户确认 → 结构仍被拦住")
        import hitl  # noqa: E402
        hitl.save_record(tmp, "10_appearance", hitl.APPROVE, "外观确认通过")
        ok(pipeline.gate(tmp, "20_structure")["ready"], "用户确认 + 有外观图/提示词 → 结构可开工")
        ok(pipeline.gate(tmp, "20_structure")["context"]["appearance"]["has_prompts"], "门禁带出提示词存档")

        w(tmp, "20_structure/structure.md", "# 结构\n内腔净尺寸 96×76×30mm\n安装柱 4×M2\n")
        w(tmp, "20_structure/consistency.md", "整体包围盒：**100.0×80.0×40.0 mm**\n")
        w(tmp, "20_structure/model.step", "ISO-10303")
        hitl.save_record(tmp, "20_structure", hitl.APPROVE, "结构确认通过")
        g30 = pipeline.gate(tmp, "30_hardware")
        ok(g30["ready"], "有结构尺寸 → 硬件可开工")
        pcb = g30["context"]["pcb"]
        ok(pcb["ok"] and pcb["board_mm"] == [78.0, 98.0], f"板框推导 {pcb.get('board_mm')}（内腔留 1mm 单边余量）")
        ok(pcb["suggested_layers"] == 2 and "面积" in pcb["reason"], f"层数决策 {pcb['suggested_layers']} 层：{pcb['reason'][:24]}…")
        ok("内腔" in "".join(g30["context"]["structure"]["lines"]), "结构内腔原文行传给硬件")

        w(tmp, "30_hardware/netlist.json", json.dumps({
            "components": [{"id": "U1", "type": "esp32", "value": "ESP32-S3", "footprint": "QFN"}],
            "connections": [{"from": "U1.D13", "to": "D1.anode"}],
        }))
        w(tmp, "30_hardware/bom.csv", "id,type,value\nU1,MCU,ESP32-S3\nD1,LED,Red\n")
        g40 = pipeline.gate(tmp, "40_firmware")
        ok(g40["ready"], "有网表与 BOM → 固件可开工")
        ok(g40["context"]["hardware"]["n_components"] == 1, "器件数传给固件")
        ok(g40["context"]["hardware"]["bom_rows"] == 2, "BOM 行数传给固件")
        ok("U1.D13" in g40["context"]["hardware"]["connections"], "引脚端点传给固件")

        w(tmp, "40_firmware/firmware.ino", "void setup(){}")
        w(tmp, "40_firmware/simulation.md", "# 仿真\n")
        ok(pipeline.gate(tmp, "90_review")["ready"], "固件落盘 → 评审可开工")
        ok(not pipeline.snapshot(tmp)["progress"]["finished"], "评审未落盘时不算全部完成")
        w(tmp, "90_review/review.md", "# 评审\n")
        snap = pipeline.snapshot(tmp)
        ok(snap["progress"]["finished"], "六阶段判定齐全")
        ok(snap["next"] == "", "全部完成后没有下一阶段")

        # ------------------------------------------------ 4. 交付清单（标题 ↔ 规划）
        print("\n[4] 交付清单：产物标题对应规划标题")
        todos = [
            {"content": "需求分析与规格定义", "status": "completed"},
            {"content": "外观设计：出产品渲染图", "status": "completed"},
            {"content": "结构设计：按外观同源建模并导出 STEP", "status": "completed"},
            {"content": "硬件设计：按结构内腔定 PCB 板型与层数", "status": "in_progress"},
            {"content": "固件与仿真：按器件功能写驱动", "status": "pending"},
            {"content": "交叉评审", "status": "pending"},
        ]
        man = pipeline.manifest(tmp, todos)
        by = {g["id"]: g for g in man["stages"]}
        ok(by["20_structure"]["plan_title"].startswith("结构设计"), f"结构组标题取自规划：{by['20_structure']['plan_title']}")
        ok(by["30_hardware"]["plan_title"].startswith("硬件设计"), f"硬件组标题取自规划：{by['30_hardware']['plan_title']}")
        ok(by["00_prd"]["plan_title"] == "需求分析与规格定义", "PRD 组标题取自规划")
        ok(by["90_review"]["plan_title"] == "交叉评审", "未匹配时用阶段名兜底")
        files30 = {f["path"]: f["title"] for f in by["30_hardware"]["files"]}
        ok(files30.get("30_hardware/netlist.json") == "电路网表", "契约文件用中文标题")
        ok(files30.get("30_hardware/bom.csv") == "物料清单（BOM）", "BOM 标题正确")
        ok(man["total_files"] == sum(g["n_files"] for g in man["stages"]), "文件总数对得上")
        w(tmp, "conversation_history/session-1.md", "# 会话")
        man2 = pipeline.manifest(tmp, todos)
        other = [g for g in man2["stages"] if g["id"] == ""]
        ok(len(other) == 1 and other[0]["n_files"] == 1, "阶段外的文件归入「其它」，不丢")
        ok(man2["total_files"] == man["total_files"] + 1, "其它文件也计入总数")

        # ------------------------------------------------ 5. 打包
        print("\n[5] 打包下载")
        data, name = pipeline.zip_stage(tmp, "30_hardware")
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        ok("30_hardware/netlist.json" in names and "30_hardware/bom.csv" in names, "阶段包只含该阶段文件")
        ok(not any(n.startswith("20_structure/") for n in names), "阶段包不含上游文件")
        ok(name.startswith("30_hardware"), f"阶段包文件名 {name}")

        data, name = pipeline.zip_all(tmp, "abc123", todos)
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        ok("abc123/交付清单.md" in names, "全量包内含交付清单")
        ok("abc123/40_firmware/firmware.ino" in names, "全量包保留阶段目录")
        md = zf.read("abc123/交付清单.md").decode("utf-8")
        ok("结构设计：按外观同源建模并导出 STEP" in md, "清单里写的是规划标题")
        ok("电路网表" in md, "清单里写的是产物中文标题")

        # ------------------------------------------------ 6. 面积 → 封装/层数判据
        print("\n[6] 板面积 → 封装与层数")
        small = pipeline.pcb_envelope({"overall_mm": [30, 30, 12]})
        ok(small["suggested_layers"] == 4, f"小板 {small['board_area_mm2']}mm² → 4 层（BGA 扇出走不通 2 层）")
        big = pipeline.pcb_envelope({"overall_mm": [120, 90, 40]})
        ok(big["suggested_layers"] == 2, f"大板 {big['board_area_mm2']}mm² → 2 层")
        mid = pipeline.pcb_envelope({"overall_mm": [50, 40, 18]})
        ok(mid["suggested_layers"] == 2 and "0603" in mid["suggested_package"], "中等板 → 2 层 + 0603 可布")
        none = pipeline.pcb_envelope({"overall_mm": None})
        ok(not none["ok"] and "结构" in none["hint"], "无结构尺寸时给出可操作提示")

        # ------------------------------------------------ 7. 工具层输出
        print("\n[7] stage_gate 工具")
        import tools

        tools.bind_context(tmp, None, {})
        out = tools.stage_gate.func("30_hardware")
        ok("可以开工" in out, "放行时给出明确结论")
        ok("板框建议" in out and "PCB 约束" in out, "输出带 PCB 板型约束")
        out40 = tools.stage_gate.func("40_firmware")
        ok("器件" in out40 and "引脚" in out40, "固件门禁带器件与引脚")
        out20 = tools.stage_gate.func("结构设计")
        ok("20_structure 结构设计" in out20, "中文阶段名也能解析")

        # 空目录阻断
        tmp2 = Path(tempfile.mkdtemp(prefix="pipeline-empty-"))
        tools.bind_context(tmp2, None, {})
        blocked = tools.stage_gate.func("硬件设计")
        ok("暂不具备开工条件" in blocked and "缺失" in blocked, "缺上游时明确阻断并说明原因")
        shutil.rmtree(tmp2, ignore_errors=True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n=== pipeline: {PASS} 通过 / {FAIL} 失败 ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
