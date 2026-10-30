"""EDA 并发首次导入回归：_DeadlockError: deadlock detected by _ModuleLock('eda.xxx')。

背景（2026-10-30 真机事故）：worker 里工具是在线程池并发跑的，而工具函数里写的是
「用到才 import」。CPython 的 import 锁在「线程 A 导包根 eda、线程 B 直接导子模块
eda.board / eda.kicad」并发时会互相等成环 —— B 先拿到子模块锁再去导父包，A 拿着父包锁
等子模块，直接 _DeadlockError，整次运行报废。

修法（agent/tools.py）：_mod() 把延迟导入串行化 + _eda_pkg() 固定包名 + 启动时单线程预热。
本脚本验的就是这三件事真的挡住了死锁。
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
sys.path.insert(0, str(AGENT))

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


# 子进程里干的事：import tools（触发预热）→ 多线程同时做旧代码那种「直接导子模块」
_CHILD = r'''
import sys, threading
sys.path.insert(0, r"{agent}")
import tools                      # 预热：单线程把 eda.* 全导入好

N = 12
b = threading.Barrier(N)
errs = []
PAT = [
    "import eda",
    "from eda.kicad import write",
    "from eda.board import load, normalize",
    "from eda.easyeda import write",
    "from eda.pcb import write",
    "from eda.netlist import annotate, normalize",
    "from eda import gerber",
    "from eda.preview import write_svg",
]

def run(i):
    b.wait()
    try:
        exec(PAT[i % len(PAT)], {{}})
    except BaseException as e:
        errs.append(f"[{{PAT[i % len(PAT)]}}] {{type(e).__name__}}: {{e}}")

ts = [threading.Thread(target=run, args=(i,)) for i in range(N)]
for t in ts: t.start()
for t in ts: t.join()
print("ERRORS", len(errs))
for e in errs: print("ERR", e)
'''


def race_rounds(rounds: int = 12) -> int:
    """跑 rounds 轮子进程，返回出错总次数（0 = 没撞上死锁）。"""
    bad = 0
    for _ in range(rounds):
        r = subprocess.run(
            [sys.executable, "-c", _CHILD.replace("{agent}", str(AGENT))],
            capture_output=True, text=True, timeout=120,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if "DeadlockError" in out or r.returncode != 0:
            bad += 1
            print("    ↑ 失败轮：", out.strip()[:300])
    return bad


print("=" * 60)
print("【1】预热：import tools 后 eda 子模块就该全在 sys.modules 里")
import tools  # noqa: E402

pkg = tools._eda_pkg()
print(f"  解析到的 eda 包名：{pkg}")
check("包名解析成功", pkg in ("eda", "agent.eda"), pkg)
need = ["board", "netlist", "kicad", "pcb", "easyeda", "preview", "gerber"]
missing = [m for m in need if f"{pkg}.{m}" not in sys.modules]
check("7 个子模块已预热", not missing, f"缺 {missing}")

print("\n【2】同一个 eda 只存在一份（不许 eda 与 agent.eda 各导一遍）")
both = ("eda" in sys.modules) and ("agent.eda" in sys.modules)
check("eda 未被导入两次", not both, "eda 与 agent.eda 同时在 sys.modules")

print("\n【3】访问器可取到各模块的入口函数")
for name, attr in (("kicad", "write"), ("pcb", "write"), ("netlist", "annotate"),
                   ("board", "load"), ("easyeda", "write"), ("preview", "write_svg")):
    try:
        fn = tools._eda(name, attr)
        check(f"_eda({name!r}, {attr!r})", callable(fn), repr(fn))
    except Exception as e:  # noqa: BLE001
        check(f"_eda({name!r}, {attr!r})", False, f"{type(e).__name__}: {e}")
check("gerber 模块可取", tools._mod(f"{pkg}.gerber") is not None)

print("\n【4】并发首次导入不再撞 import 死锁（子进程 × 12 轮 × 12 线程）")
bad = race_rounds(12)
check("12 轮并发导入全部无 DeadlockError", bad == 0, f"{bad} 轮失败")

print("\n【5】静态检查：tools.py 里不许再有裸的 eda 延迟导入（否则预热形同虚设）")
src = (AGENT / "tools.py").read_text(encoding="utf-8")
bare = [ln.strip() for ln in src.splitlines()
        if ln.strip().startswith(("from eda", "from agent.eda", "from .eda", "import eda"))]
check("无裸 eda import（全部走 _eda/_mod）", not bare, f"仍有 {bare}")
check("访问器存在", hasattr(tools, "_mod") and hasattr(tools, "_eda") and hasattr(tools, "_eda_pkg"), "")

print("\n" + "=" * 60)
print(f"通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
