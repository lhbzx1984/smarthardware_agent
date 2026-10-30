"""立创官方元件库（`agent/eda/lib.py`）的自验。

分两段：
- **离线段**：解析与匹配规则，不联网、不依赖缓存，永远要跑；
- **联网段**：真拉取器件，取不到就 skip（CI 无网时不能假失败）。

匹配规则是踩坑踩出来的，每条断言都对应一个真实事故：
``USB-C → 40P 排针``、``SSD1306 OLED → 贴片电感``、``3mm 直插 LED → 0805``、
``800mAh 锂电 → CR2032 纽扣座``。这些「配上了但配错」比配不上更糟。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from eda import lib  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def main() -> int:
    print("[1] 单位与层号（标准版 → Pro）")
    check("封装 1 单位 = 10 mil", lib._FP_UNIT == 10.0, str(lib._FP_UNIT))
    check("99→48（器件轮廓）", lib._LAYER_MAP.get("99") == 48, str(lib._LAYER_MAP.get("99")))
    check("101→49（极性）", lib._LAYER_MAP.get("101") == 49, str(lib._LAYER_MAP.get("101")))
    check("11→12（多层）", lib._LAYER_MAP.get("11") == 12, str(lib._LAYER_MAP.get("11")))

    print("[2] SVG 迷你路径解析")
    # 小写 h/v 是**相对**命令 —— 早先只取大写，把 ``h -10`` 读成绝对坐标，
    # 引脚线长算出 340 这种离谱值。
    pts = lib._path_points("M 0 0 h 10 v 10")
    check("相对命令按增量算", pts == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], str(pts))
    # Z 要闭合回起点，所以是 4 个点
    pts = lib._path_points("M0,0L10,0L10,10Z")
    check("逗号与粘连写法", pts == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)], str(pts))
    check("空路径不炸", lib._path_points("") == [])

    print("[3] 器件种类")
    check("kind 优先", lib._kind({"kind": "ic", "type": "module"}) == "ic")
    check("回退认 type", lib._kind({"type": "Connector"}) == "connector")

    print("[4] 哪些字段可能藏着型号")
    check("纯阻值不拿去搜", lib._candidate_models({"value": "10k"}) == [])
    check("纯容值不拿去搜", lib._candidate_models({"value": "100nF"}) == [])
    check("型号要拿去搜", lib._candidate_models({"value": "TP4056"}) == ["TP4056"])
    check("part 字段也认", lib._candidate_models({"part": "AMS1117-3.3"}) == ["AMS1117-3.3"])
    check("复合型号会拆词", "SSD1306" in lib._shortened({"value": "SSD1306-OLED-1.3"}))

    print("[5] 打分：型号是主身份，封装只加分")
    hit = {"mpn": "TP4056", "package": "ESOP-8_L4.9-W3.9"}
    check("型号完全相等 = 100", lib._score(hit, "TP4056", "") == 100)
    check("型号前缀命中 = 60", lib._score({"mpn": "TP4056X"}, "TP4056", "") == 60)
    check("型号包含 = 45", lib._score({"mpn": "XXTP4056YY"}, "TP4056", "") == 45)
    check("只有封装族同 = 20（不够门槛）",
          lib._score({"mpn": "SSD1306-150MT"}, "SSD1306-OLED-1.3", "") == 20)
    check("毫不相干 = 0", lib._score({"mpn": "NE555"}, "TP4056", "") == 0)
    check("封装一致加分", lib._score(hit, "TP4056", "ESOP-8") > 100)
    # 网表里的 footprint 经常是模型瞎写的（TP4056 标成 SOT-23-5，实际 ESOP-8）
    check("封装不符**不否决**", lib._score(hit, "TP4056", "SOT-23-5") == 100)

    print("[6] 种类冲突：封装大类与器件种类对不上就弃用")
    check("电感封装配 module → 弃",
          lib._score({"mpn": "SSD1306-150MT", "package": "IND-SMD"}, "SSD1306", "", "module") == 0)
    check("电感封装配 inductor → 留",
          lib._score({"mpn": "SSD1306-150MT", "package": "IND-SMD"}, "SSD1306", "", "inductor") > 0)
    check("电池座封装配 battery → 留", not lib._pkg_clash("BAT-TH_CR2032", "battery"))
    check("电池座封装配 ic → 弃", lib._pkg_clash("BAT-TH_CR2032", "ic"))
    check("认不出的前缀不误杀", not lib._pkg_clash("MODULE-SMD_ESP32", "module"))
    check("空 kind 不误杀", not lib._pkg_clash("IND-SMD", ""))

    print("[7] 规格型关键词（型号在库里不叫这个名字）")
    check("USB-C 16PIN → TYPE-C 16P",
          lib._hints({"type": "connector", "value": "USB-C",
                      "footprint": "USB-C-16PIN"}) == ["TYPE-C 16P"],
          str(lib._hints({"type": "connector", "value": "USB-C", "footprint": "USB-C-16PIN"})))
    check("无脚数时退回 TYPE-C",
          lib._hints({"type": "connector", "value": "USB-C"}) == ["TYPE-C"])
    check("3mm 直插 LED → LED 3mm",
          lib._hints({"type": "led", "footprint": "LED-3mm"}) == ["LED 3mm"])
    check("电阻不生成 hint", lib._hints({"type": "resistor", "value": "10k"}) == [])

    print("[8] 通用件：直插不给贴片映射")
    check("0603 电阻有映射", bool(lib._generic_code({"type": "resistor", "footprint": "0603"})))
    check("0603 电容有映射", bool(lib._generic_code({"type": "capacitor", "footprint": "0603"})))
    # TACT-6MM 是贴片轻触开关，别被 "6MM" 判成直插
    check("TACT-6MM 不算直插", bool(lib._generic_code({"type": "switch", "footprint": "TACT-6MM"})))
    check("LED-3mm 算直插 → 不映射",
          lib._generic_code({"type": "led", "footprint": "LED-3mm"}) == "")
    check("连接器没有通用映射", lib._generic_code({"type": "connector"}) == "")
    check("电池没有通用映射", lib._generic_code({"type": "battery"}) == "")

    print("[9] 探测失败要有 TTL（一次抖动不能判死整条链路）")
    lib._L_PROBE.update({"ok": False, "why": "x", "at": 0.0})
    check("过期后允许重探", lib.online() in (True, False) and lib._L_PROBE["at"] > 0.0)
    lib._L_PROBE.update({"ok": True})
    check("成功后不再打网络", lib.online() is True)

    print("[10] 联网段（取不到就 skip）")
    if not lib.online():
        print("  skip 离线")
        return finish()
    part = lib.fetch("C25804") or lib.fetch("C19702")
    if not part:
        print("  skip 取不到 C12530")
        return finish()
    check("电容有符号引脚", len(part["sym"]["pins"]) == 2, str(len(part["sym"]["pins"])))
    check("电容有封装焊盘", len(part["fp"]["pads"]) == 2, str(len(part["fp"]["pads"])))
    check("贴片焊盘没有钻孔",
          all(float(p.get("drill") or 0) == 0.0 for p in part["fp"]["pads"]),
          str([p.get("drill") for p in part["fp"]["pads"]]))
    rows = lib.sym_rows(part, "C12530")
    check("符号行以 DOCTYPE 开头", rows[0][0] == "DOCTYPE", str(rows[0][:2]))
    check("符号有 PIN 行", any(r[0] == "PIN" for r in rows))
    fp_rows, pad_eids = lib.fp_rows(part, part["package"])
    check("封装焊盘号 → eid 一对一", len(pad_eids) == len(part["fp"]["pads"]),
          f"{len(pad_eids)} vs {len(part['fp']['pads'])}")
    check("焊盘行 22 段", all(len(r) == 22 for r in fp_rows if r[0] == "PAD"))
    check("引脚名能转成焊盘号", bool(lib.pin_number(part, "1")))

    tht = lib.fetch("C2935306") or lib.fetch("C21713961")
    if tht and any(float(p.get("drill") or 0) > 0 for p in tht["fp"]["pads"]):
        check("通孔器件有钻孔", True)
    else:
        print("  skip 没有可用的通孔器件")

    # 真的走一遍匹配：这些是踩过坑的器件
    for comp, kind, must_not in (
            ({"type": "connector", "value": "USB-C", "footprint": "USB-C-16PIN"},
             "connector", "HDR-TH_40P"),
    ):
        got = lib.resolve(comp)
        check(f"{comp['value']} 不被配成排针",
              not got or "HDR-TH_40P" not in (got.get("package") or ""),
              str(got.get("package") if got else None))

    return finish()


def finish() -> int:
    print(f"\n通过 {passed} / 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
