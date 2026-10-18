"""回归：工作区路径解析（Windows `\\\\?\\` / `//?/` 前缀复发专项）。

线上复发的一次报错：

    ValueError: Path:\\\\?\\C:\\Users\\...\\workspace\\<run>\\10_appearance\\render-prompts.md
                outside root directory: C:\\Users\\...\\workspace\\<run>

根因不是"模型传了宿主机路径"这么简单 —— 归一化漏掉 `//?/` 形态时，基类的
`(self.cwd / vpath).resolve()` 会把带前缀的 vpath 当**绝对路径**直接覆盖 cwd，
解析结果既带前缀又脱离工作区。这里把所有形态都钉死。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import main_agent  # noqa: E402
import pathutil  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


tmp = Path(tempfile.mkdtemp(prefix="ws_"))
run = tmp / "5b89343018b9"
(run / "10_appearance").mkdir(parents=True)
root_s = str(run).replace("\\", "/")

print("== 1. pathutil 前缀剥离（含正斜杠形态）==")
for label, raw in [
    ("反斜杠 \\\\?\\", "\\\\?\\" + root_s.replace("/", "\\") + "\\10_appearance\\a.md"),
    ("正斜杠 //?/", "//?/" + root_s + "/10_appearance/a.md"),
    ("前缀+正斜杠混写", "\\\\?\\" + root_s + "/10_appearance/a.md"),
    ("小写前缀", "\\\\?\\" + root_s.replace("/", "\\") + "\\10_appearance\\a.md"),
    ("带引号包裹", '"' + "\\\\?\\" + root_s.replace("/", "\\") + "\\10_appearance\\a.md" + '"'),
]:
    check(f"{label} → 折成相对路径", pathutil.normalize(raw, run) == "10_appearance/a.md",
          pathutil.normalize(raw, run))

check("UNC 形态仍判为工作区外", pathutil.is_foreign(pathutil.normalize("//?/UNC/srv/share/a.md", root_s)))
check("别处盘符仍判为工作区外", pathutil.is_foreign(pathutil.normalize("//?/D:/other/a.md", root_s)))
check("under_root：根自身", pathutil.under_root(root_s, root_s))
check("under_root：子目录", pathutil.under_root(root_s + "/10_appearance/a.md", root_s))
check("under_root：同级相似名不算", not pathutil.under_root(root_s + "_bak/a.md", root_s))
check("abs_under：.. 越界返回 None", pathutil.abs_under("../etc/passwd", root_s) is None)
check("abs_under：工作区外盘符返回 None", pathutil.abs_under("D:/x/a.md", root_s) is None)

print("== 2. WorkspaceBackend 全形态解析 ==")
be = main_agent.WorkspaceBackend(root_dir=str(run), virtual_mode=True)
cases = [
    ("相对路径", "10_appearance/render-prompts.md"),
    ("虚拟路径", "/10_appearance/render-prompts.md"),
    ("本 run 绝对路径", root_s + "/10_appearance/render-prompts.md"),
    ("反斜杠绝对路径", root_s.replace("/", "\\") + "\\10_appearance\\render-prompts.md"),
    ("\\\\?\\ 前缀", "\\\\?\\" + root_s.replace("/", "\\") + "\\10_appearance\\render-prompts.md"),
    ("//?/ 前缀", "//?/" + root_s + "/10_appearance/render-prompts.md"),
    ("\\\\?\\ + 正斜杠混写", "\\\\?\\" + root_s + "/10_appearance/render-prompts.md"),
    ("/home/user 前缀", "/home/user/10_appearance/render-prompts.md"),
    ("多余斜杠", "//10_appearance//render-prompts.md"),
]
for label, key in cases:
    try:
        p = be._resolve_path(key)
        s = str(p).replace("\\", "/")
        check(f"{label} → 落在工作区内",
              pathutil.under_root(s, root_s) and s.endswith("10_appearance/render-prompts.md"), s)
        check(f"{label} → 结果不带 \\\\?\\ 前缀", not s.startswith(("//?/", "\\\\?\\")), s)
    except Exception as e:  # noqa: BLE001
        check(f"{label} → 落在工作区内", False, f"{type(e).__name__}: {e}")

for label, key in [
    ("别的 run 目录", root_s + "_other/10_appearance/a.md"),
    ("盘符越界", "D:/Windows/a.md"),
    ("UNC 越界", "\\\\srv\\share\\a.md"),
    (".. 穿越", "/../outside/a.md"),
]:
    try:
        be._resolve_path(key)
        check(f"{label} → 被拒绝", False, "没抛异常")
    except ValueError as e:
        check(f"{label} → 被拒绝", True)
        check(f"{label} → 中文可读提示", "工作区" in str(e) or "越界" in str(e), str(e))

print("== 3. 真实读写落盘（走 backend.write / read）==")
try:
    res = be.write("/10_appearance/render-prompts.md", "# 提示词\n")
    check("write 成功", res is not None)
    check("文件真的落盘", (run / "10_appearance" / "render-prompts.md").exists())
    txt = be.read("/10_appearance/render-prompts.md")
    check("read 回读一致", "# 提示词" in str(txt), str(txt)[:60])
    p = be._resolve_path("\\\\?\\" + root_s.replace("/", "\\") + "\\10_appearance\\render-prompts.md")
    check("带前缀解析到同一文件", p.exists() and p.samefile(run / "10_appearance" / "render-prompts.md"), str(p))
    v = be._to_virtual_path(run / "10_appearance" / "render-prompts.md")
    check("_to_virtual_path 回虚拟路径", v == "/10_appearance/render-prompts.md", v)
except Exception as e:  # noqa: BLE001
    check("write 成功", False, f"{type(e).__name__}: {e}")

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
