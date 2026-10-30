"""Gerber / 钻孔 / 坐标文件导出 —— 打样要交给板厂的那包东西。

走本机 kicad-cli（无头），不自己写 Gerber 语法：RS-274X 的坑不值得踩一遍，
而且 KiCad 导出的就是板厂认的格式。没装 KiCad 时**明确告知**，不静默失败 ——
用户拿到"导出成功"却发现是空的，比直接说做不到更糟。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

# kicad-cli 装在哪：本机实测在 E:/KiCad（不在 Program Files，也不在 PATH 里）
_CLI_CANDIDATES = (
    "E:/KiCad/10.0/bin/kicad-cli.exe",
    "E:/KiCad/9.0/bin/kicad-cli.exe",
    "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe",
    "C:/Program Files/KiCad/9.0/bin/kicad-cli.exe",
    "C:/Program Files/KiCad/8.0/bin/kicad-cli.exe",
)

# 打样真正需要的层（其余是参考层，带上无害但板厂一般只看这些）
# 铜层文件名 KiCad 8/9 叫 -F_Cu.gtl，KiCad 10 改成了 -top_cu.gtl —— 两种都要认，
# 否则"必需层"清单会把最重要的线路层漏掉。
ESSENTIAL_SUFFIXES = (
    "-F_Cu.gtl", "-B_Cu.gbl", "-top_cu.gtl", "-bottom_cu.gbl",
    "-F_Mask.gts", "-B_Mask.gbs", "-F_Silkscreen.gto", "-B_Silkscreen.gbo",
    "-F_Paste.gtp", "-B_Paste.gbp",
    "-Edge_Cuts.gm1", ".drl",
)


def find_cli() -> str | None:
    """定位 kicad-cli。返回绝对路径，找不到返回 None。"""
    env = os.environ.get("KICAD_CLI", "").strip()
    if env:
        return env if Path(env).exists() else None
    which = shutil.which("kicad-cli")
    if which:
        return which
    for c in _CLI_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def _run(cli: str, args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run([cli, *args], capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"
    # kicad-cli 在 Windows 上输出 GBK，用 utf-8 解会得到乱码甚至抛异常
    out = (p.stdout + p.stderr).decode("gbk", "replace")
    return p.returncode, out.strip()


def export(pcb: Path, out_dir: Path) -> dict[str, object]:
    """导出 Gerber + 钻孔 + 坐标文件到 out_dir。不抛异常，结果写在返回里。"""
    cli = find_cli()
    if not cli:
        return {"ok": False, "error": "本机没找到 kicad-cli（没装 KiCad 或装在非标准位置）",
                "files": [], "hint": _manual_hint()}

    pcb = Path(pcb)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: list[tuple[str, list[str]]] = [
        ("gerber", ["pcb", "export", "gerbers", "--output", str(out_dir), str(pcb)]),
        ("drill", ["pcb", "export", "drill", "--output", str(out_dir), str(pcb)]),
        ("pos", ["pcb", "export", "pos", "--output", str(out_dir / "positions.csv"), str(pcb)]),
    ]
    done: list[str] = []
    failed: list[str] = []
    for name, args in steps:
        rc, out = _run(cli, args)
        (done if rc == 0 else failed).append(name if rc == 0 else f"{name}(rc={rc}) {out[:120]}")

    files = sorted(p for p in out_dir.iterdir() if p.is_file())
    if not files:
        return {"ok": False, "error": "kicad-cli 跑完了但没产出文件", "files": [],
                "done": done, "failed": failed, "hint": _manual_hint()}
    return {
        "ok": not failed,
        "cli": cli,
        "files": [str(p) for p in files],
        "done": done,
        "failed": failed,
    }


def pack(files: list[str], zip_path: Path, extra: dict[str, str] | None = None) -> dict[str, object]:
    """把散落的文件打成 zip（板厂要的就是一个压缩包）。"""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                p = Path(f)
                if p.is_file():
                    z.write(p, p.name)
                    n += 1
            for name, body in (extra or {}).items():
                z.writestr(name, body)
                n += 1
    except (OSError, zipfile.BadZipFile) as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "count": n}
    return {"ok": True, "path": str(zip_path), "count": n,
            "size": zip_path.stat().st_size if zip_path.exists() else 0}


def essential(files: list[str]) -> list[str]:
    """打样必需的层（用于给用户一句"里面有啥"）。"""
    return [f for f in files if any(f.endswith(s) for s in ESSENTIAL_SUFFIXES)]


def _manual_hint() -> str:
    return ("没有 KiCad 也能出：用 KiCad 打开 .kicad_pcb → 文件 → 制造输出 → "
            "Gerber（.gbr）+ 钻孔文件（.drl），连同 BOM 与坐标文件一起发给板厂。")
