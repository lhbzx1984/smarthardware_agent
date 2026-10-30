"""「结构阶段死循环」这条线上事故的回归测试。

事故现场（用户实跑日志）：
- 建模脚本跑通了，但上盖用 `Sphere(半径 340)` 做微拱，布尔之后包围盒变成
  **100×100×341mm** —— 工具照样报「建模成功」并把 20_structure 置为 done。
- 模型发现尺寸离谱，想改 → 护栏「产物已落盘但未确认，请先 confirm_with_user」。
- 模型去 confirm → 用户确认 → 状态变 confirmed → 再想改 → 护栏更硬的一条
  「已经生成并经用户确认，不要再重做」。
- 于是 **confirm → 被拦 → confirm → 被拦**，8 次 text_to_cad / build_structure
  全部被拒，工具预算烧光，阶段还停在那个 341mm 的错模型上。

两处根因，各修一处：
1. **脚本能跑通 ≠ 模型是对的** —— 建模成功后要做几何自检，畸形就不置 done（不锁阶段）。
2. **护栏要有熔断** —— 拦满次数就放行一次自我纠错，否则模型只能撞墙。

本文件验证这两条。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "agent")]

import stagestate  # noqa: E402
import tools  # noqa: E402

FAILED: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(label)


# 用系统临时目录：workspace 下的批量删除会被宿主守卫拦（SAFE_DELETE_FAIL_CLOSED）
tmp = Path(tempfile.mkdtemp(prefix="deadlock_"))
tools.bind_context(tmp, lambda ev: None, {})

SCRIPT = "W, D = 90.0, 90.0\nH_TOTAL = 52.0\nR_OUTER = 10.0\n"

print("\n【1】几何自检 _cad_sanity：跑通不等于对")

good = [
    {"name": "主体外壳", "size_mm": [90.0, 90.0, 32.0], "volume_cm3": 49.34},
    {"name": "上盖", "size_mm": [90.0, 90.0, 12.0], "volume_cm3": 45.44},
    {"name": "底盖", "size_mm": [90.0, 90.0, 8.0], "volume_cm3": 59.367},
]
check("正常三个零件 → 无问题", tools._cad_sanity(good, SCRIPT) == [],
      str(tools._cad_sanity(good, SCRIPT)))

# 事故现场：上盖 341mm 高（Sphere 半径过大，布尔残留）
bad = [dict(good[0]), {"name": "上盖", "size_mm": [100.0, 100.0, 341.0], "volume_cm3": 45.44},
       dict(good[2])]
hits = tools._cad_sanity(bad, SCRIPT)
check("341mm 的上盖被抓出来", len(hits) == 1 and "上盖" in hits[0], str(hits))
check("只点名出问题的零件，不误伤好零件",
      all("上盖" in h for h in hits) and not any("主体" in h or "底盖" in h for h in hits),
      str(hits))

check("体积为 0（布尔把零件减没了）→ 抓出来",
      any("体积" in h for h in tools._cad_sanity(
          [{"name": "上盖", "size_mm": [90.0, 90.0, 12.0], "volume_cm3": 0.0}], SCRIPT)))
check("没有零件 → 抓出来", bool(tools._cad_sanity([], SCRIPT)))

# 脚本里没有尺寸常量时，只剩 500mm 绝对上限这一条
big = [{"name": "外壳", "size_mm": [1200.0, 800.0, 600.0], "volume_cm3": 1.0}]
check("无尺寸常量时靠 500mm 绝对上限兜住",
      bool(tools._cad_sanity(big, "parts = {'外壳': shell.part}")),
      str(tools._cad_sanity(big, "parts = {'外壳': shell.part}")))

print("\n【2】护栏熔断：拦到底就放行，不让模型一直撞墙")

sid = "20_structure"
stagestate.save(tmp, sid, status=stagestate.DONE, confirmed=False, blocked=0)
r1 = stagestate.guard(tmp, sid, "text_to_cad", ["20_structure/model.py"])
check("DONE 第一次 → 拒绝", r1 != "", r1[:40])
check("拒绝文案里给了出路（再调一次就放行）", "再调用一次" in r1, r1[:120])
r2 = stagestate.guard(tmp, sid, "text_to_cad", ["20_structure/model.py"])
check("DONE 第二次 → 放行（熔断）", r2 == "", r2[:60])
check("放行后状态退回 working，做完还能重新收尾",
      stagestate.load(tmp, sid)["status"] == stagestate.WORKING,
      str(stagestate.load(tmp, sid)["status"]))

# 已确认的阶段多拦一次
stagestate.save(tmp, sid, status=stagestate.CONFIRMED, confirmed=True, blocked=0)
c1 = stagestate.guard(tmp, sid, "text_to_cad", ["20_structure/model.py"])
c2 = stagestate.guard(tmp, sid, "text_to_cad", ["20_structure/model.py"])
c3 = stagestate.guard(tmp, sid, "text_to_cad", ["20_structure/model.py"])
check("CONFIRMED 前两次 → 都拒绝", c1 != "" and c2 != "", f"{c1[:30]} | {c2[:30]}")
check("CONFIRMED 第三次 → 放行", c3 == "", c3[:60])
check("放行后退回 done（会重新走一次人工确认）",
      stagestate.load(tmp, sid)["status"] == stagestate.DONE,
      str(stagestate.load(tmp, sid)["status"]))

# 新的一轮确认要把熔断计数清零，否则下一次会提前放行
stagestate.mark_confirmed(tmp, sid)
check("重新确认后熔断计数归零", int(stagestate.load(tmp, sid).get("blocked") or 0) == 0,
      str(stagestate.load(tmp, sid)))
check("归零后第一次调用又回到拒绝",
      stagestate.guard(tmp, sid, "text_to_cad", []) != "")

# 廉价工具永远放行（stage_gate / confirm_with_user 被挡才是另一种死锁）
check("confirm_with_user 不被护栏拦",
      stagestate.guard(tmp, sid, "confirm_with_user", []) == "")
check("stage_gate 不被护栏拦",
      stagestate.guard(tmp, sid, "stage_gate", []) == "")

print("\n" + "=" * 56)
if FAILED:
    print(f"❌ 失败 {len(FAILED)} 项：")
    for f in FAILED:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过")
