"""工作区路径归一化。

模型（尤其是被上游错误信息误导时）会把 **宿主机绝对路径** 塞进 write_file /
save_artifact，例如 `C:\\Users\\...\\workspace\\<run_id>\\10_appearance\\orthographic.svg`。
在 Windows 上 `Path.resolve()` 会给它加上 `\\\\?\\` 长路径前缀，而 deepagents 的
`FilesystemBackend._resolve_path` 用 `full.relative_to(cwd)` 判定越界 ——
`\\\\?\\C:\\...` 与 `C:\\...` 字符串不等价，于是**正确的路径也被判成越界**：

    ValueError: Path:\\\\?\\C:\\...\\10_appearance\\orthographic.svg
                outside root directory: C:\\...\\workspace\\<run_id>

这里把各种形态统一折回工作区内的相对虚拟路径，模型无论传哪种都能落盘。
"""

from __future__ import annotations

import re
from pathlib import Path

_HOME_USER = "/home/user"
_DRIVE = re.compile(r"^[A-Za-z]:")


def strip_extended(key: str) -> str:
    """去掉 Windows 扩展长度前缀 `\\\\?\\` / `//?/` / `\\\\?\\UNC\\`。

    四种形态都要认：模型从错误信息里抄路径时，既可能原样带回反斜杠，也可能被
    JSON/Markdown 转义成正斜杠（`//?/C:/...`）——只认反斜杠那一种就会漏，漏掉之后
    `Path(cwd) / vpath` 把它当绝对路径直接覆盖工作区，越界判定必然失败。
    """
    s = str(key or "").strip().strip('"').strip("'")
    t = s.replace("\\", "/")
    low = t.lower()
    if low.startswith("//?/unc/"):
        # 还给反斜杠形态：normalize 靠 `\\` 开头识别 UNC，正斜杠会被当成"多写了一个斜杠"
        return "\\\\" + t[8:].replace("/", "\\")
    if low.startswith("//?/"):
        return t[4:].replace("/", "\\")
    return s


def normalize(key: str, root: Path | str = "") -> str:
    """归一成工作区内的相对虚拟路径（不带 `\\\\?\\`、用 `/` 分隔）。

    落在 root 下的绝对路径折成相对路径；**其余的保持原样**（盘符/UNC 不动），
    由 `is_foreign()` 判定后明确拒绝 —— 剥掉盘符反而会让它看起来像合法相对路径，
    然后被写进工作区里的 `Users/...` 这种垃圾目录。
    """
    s = strip_extended(key)
    if s.startswith("\\\\"):  # UNC（\\\\srv\\share）：保留双斜杠，交给 is_foreign 拒绝
        return "//" + s.replace("\\", "/").lstrip("/")
    # `//10_appearance//x.md` 这种只是多写了斜杠的虚拟路径，压成单斜杠正常落盘
    t = s.replace("\\", "/")
    if t.startswith("//"):
        t = "/" + t.lstrip("/")
    s = t
    if root:
        r = str(root).replace("\\", "/").rstrip("/")
        if r and s.lower().startswith(r.lower() + "/"):
            return s[len(r) + 1 :].lstrip("/")
    return s


def is_foreign(s: str) -> bool:
    """归一化后还带盘符 / 是 UNC = 工作区外的路径，必须拒绝。"""
    return bool(_DRIVE.match(s)) or s.startswith("//")


def under_root(s: str, root: str) -> bool:
    """归一化后的虚拟路径是否落在 root 下（大小写不敏感 + 斜杠统一）。"""
    r = str(root).replace("\\", "/").rstrip("/").lower()
    t = s.replace("\\", "/").lower()
    return t == r or t.startswith(r + "/")


def abs_under(key: str, root: str) -> str | None:
    """把任意形态的路径拼成 root 下的真实绝对路径。

    不走 `Path.resolve()`：Windows 上它会给结果加 `\\\\?\\`，而 root（已 resolve）
    不带这个前缀，`relative_to` 因此判成越界 —— 明明是工作区内的文件也被拒。
    这里用 `os.path.normpath` 纯字符串归一，越界则返回 None。
    """
    import os

    r = str(root).replace("\\", "/").rstrip("/")
    rel = normalize(key, r).lstrip("/")
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or strip_home_user(rel).startswith("~"):
        return None
    joined = os.path.normpath(os.path.join(r.replace("/", os.sep), *parts)) if parts else r
    return joined if under_root(joined, r) else None


def strip_home_user(s: str) -> str:
    """deepagents 内置提示词把工作目录说成 /home/user，而我们的虚拟根是 `/`。"""
    if s == _HOME_USER or s.startswith(_HOME_USER + "/"):
        return s[len(_HOME_USER) :] or "/"
    return s
