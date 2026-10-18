"""子智能体虚拟路径：/home/user/... 必须折回工作区根，否则每轮白烧只读调用。

用法：python scripts/test_workspace_path.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import main_agent  # noqa: E402

OK = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


import tempfile  # noqa: E402

root = Path(tempfile.mkdtemp(prefix="wsroot_"))
(root / "AGENTS.md").write_text("# 记忆\n", encoding="utf-8")
(root / "00_prd").mkdir()
(root / "00_prd" / "prd.md").write_text("需求", encoding="utf-8")

be = main_agent.WorkspaceBackend(root_dir=str(root), virtual_mode=True)

print("\n[1] /home/user 前缀折回根目录")
check(
    "/home/user/AGENTS.md -> 根目录同名文件",
    be._resolve_path("/home/user/AGENTS.md") == be._resolve_path("/AGENTS.md"),
    str(be._resolve_path("/home/user/AGENTS.md")),
)
check(
    "/home/user/00_prd/prd.md -> /00_prd/prd.md",
    be._resolve_path("/home/user/00_prd/prd.md") == be._resolve_path("/00_prd/prd.md"),
)
check(
    "/home/user 本身 -> 根目录",
    be._resolve_path("/home/user") == be._resolve_path("/"),
)
check("普通根路径不受影响", be._resolve_path("/00_prd/prd.md").name == "prd.md")
check("相对路径不受影响", be._resolve_path("00_prd/prd.md").name == "prd.md")

print("\n[2] 实际能读到文件（不是只做字符串替换）")
try:
    txt = be.read("/home/user/AGENTS.md")
    got = "# 记忆" in str(txt)
except Exception as e:  # noqa: BLE001
    got = False
    print("    读取异常:", e)
check("read(/home/user/AGENTS.md) 能读到内容", got)

try:
    listing = be.ls("/home/user")
    # LsResult 是个结果对象，条目在 entries 里；不同版本字段可能叫 files / entries
    raw = getattr(listing, "entries", None) or getattr(listing, "files", None) or listing
    names = [str(getattr(e, "path", e)) for e in (raw or [])]
    has = any("00_prd" in n for n in names)
except Exception as e:  # noqa: BLE001
    names, has = [], False
    print("    列目录异常:", e)
check("ls(/home/user) 能看到 00_prd", has, str(names)[:160])

print("\n[3] 安全边界没有被顺手放宽")
escaped = False
for bad in ("../outside.md", "/../etc/passwd", "~/x.md"):
    try:
        be._resolve_path(bad)
    except Exception:  # noqa: BLE001  越界应被拒
        escaped = True
check("越界路径仍被拒绝", escaped)

print(f"\n结果：{OK} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
