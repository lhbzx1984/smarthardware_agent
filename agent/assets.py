"""二进制产物的写入守卫与有效性校验。

真实故障（2026-10-11，run `71d4723e3fff`）：外观阶段**已经成功**调
`generate_product_image` 出了两张 700+ KB 的渲染图，随后同一个子智能体又调
`save_artifact("10_appearance/render-01.png", "placeholder")` 把两张真图**覆盖成
11 字节的文本**。前端于是显示两张打不开的图，用户看到的现象是
「外观图和渲染图生成后被删除/覆盖」。

两个教训：
1. **文本工具绝不能写二进制产物**（图片 / STEP / STL / PDF / zip）。这类文件只能由
   `generate_product_image` / `render_product_views` / `text_to_cad` 生成。
2. **「文件存在」不等于「产物有效」**。11 字节的 placeholder 也能让
   `10_appearance/*.png` 的路径判定通过，外观阶段被误判成已完成，
   结构阶段于是拿着假图往下建模 —— 门禁必须看文件**内容**。

本模块只做纯函数判断，不依赖 tools / server，供工具层与流水线层共用。
"""
from __future__ import annotations

from pathlib import Path

# 只能由专用工具生成、禁止文本写入的扩展名
BINARY_EXTS: frozenset[str] = frozenset(
    {
        # 图片
        "png", "jpg", "jpeg", "webp", "gif", "bmp", "ico", "tif", "tiff", "avif",
        # CAD / 3D
        "step", "stp", "stl", "obj", "3mf", "glb", "gltf", "iges", "igs", "dxf",
        # 文档 / 压缩
        "pdf", "zip", "7z", "rar", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
        # 音视频
        "mp4", "webm", "mov", "mp3", "wav",
    }
)
IMAGE_EXTS: frozenset[str] = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp", "avif"})

# 一张能看的渲染图该有的体量。小于这个值只能是占位符 / 截断文件 ——
# 实测的坏文件是 11 字节，正常 1024×1024 渲染图 700 KB 上下。
MIN_IMAGE_BYTES = 20 * 1024

# 明显的占位/空壳内容：任何路径下都不该被当成产物交付
PLACEHOLDER_WORDS = frozenset(
    {"placeholder", "todo", "tbd", "n/a", "na", "null", "none", "占位", "待补充", "待填写", "略"}
)

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)


def ext_of(rel: str) -> str:
    return Path(str(rel or "").replace("\\", "/")).suffix.lstrip(".").lower()


def is_binary(rel: str) -> bool:
    return ext_of(rel) in BINARY_EXTS


def is_image(rel: str) -> bool:
    return ext_of(rel) in IMAGE_EXTS


def looks_placeholder(content: str) -> bool:
    """内容是不是一眼假的占位符（整段就是 placeholder / TODO / 略）。"""
    s = (content or "").strip().strip("`*_#- \t\r\n\"'")
    if not s:
        return True
    return s.lower() in PLACEHOLDER_WORDS


def guard_text_write(rel: str, content: str) -> str:
    """文本工具（save_artifact / write_file）写盘前的守卫。

    返回 "" 表示放行，否则返回给模型看的中文拒绝原因。
    """
    if is_binary(rel):
        return (
            f"❌ 拒绝写入 {rel}：这是二进制产物，不能用文本工具生成（写了也打不开）。\n"
            "正确做法：\n"
            "  - 产品渲染图 / 场景图 / 爆炸图 → 调用 generate_product_image\n"
            "  - 工程视图（三视图 / 透视图）  → 调用 render_product_views\n"
            "  - STEP / STL 结构模型          → 调用 text_to_cad 或 build_structure\n"
            "  - 电路原理图                   → 调用 render_schematic\n"
            "已有的图片/模型文件请勿改写或删除，它们是后续阶段的唯一形状来源。"
        )
    if looks_placeholder(content):
        return (
            f"❌ 拒绝写入 {rel}：内容只有占位符（{content.strip()[:20]!r}），不是可交付的产物。\n"
            "请写真实内容；如果这一步暂时做不了，就如实说明卡在哪里，不要用占位文件冒充交付。"
        )
    return ""


def image_problem(path: Path) -> str:
    """图片文件是否真实可用。返回 "" 表示正常，否则返回问题描述。"""
    try:
        if not path.is_file():
            return "文件不存在"
        size = path.stat().st_size
        if size == 0:
            return "空文件"
        with path.open("rb") as f:
            head = f.read(16)
    except OSError as e:  # pragma: no cover - 磁盘异常
        return f"读取失败：{e}"
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            if size < MIN_IMAGE_BYTES:
                return f"{kind} 文件只有 {size} 字节，疑似占位/截断（正常渲染图 ≥ {MIN_IMAGE_BYTES // 1024} KB）"
            return ""
    # 没有魔数：多半是被文本覆盖过的伪图（实测就是 11 字节的 "placeholder"）
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:  # pragma: no cover
        text = ""
    if text and text.lower() in PLACEHOLDER_WORDS:
        return f"内容被写成了文本占位符 {text[:20]!r}，不是真实图片（{size} 字节）"
    return f"文件头不是已知图片格式（{size} 字节，开头 {head[:8]!r}）"


def image_files(root: Path, subdir: str = "10_appearance") -> list[Path]:
    d = root / subdir
    try:
        if not d.is_dir():
            return []
        return [p for p in sorted(d.rglob("*")) if p.is_file() and is_image(p.name)]
    except OSError:  # pragma: no cover
        return []


def bad_images(root: Path, subdir: str = "10_appearance") -> list[tuple[str, str]]:
    """该目录下所有不可用图片：(相对路径, 问题)。"""
    out: list[tuple[str, str]] = []
    for p in image_files(root, subdir):
        prob = image_problem(p)
        if prob:
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover
                rel = p.name
            out.append((rel, prob))
    return out


__all__ = [
    "BINARY_EXTS", "IMAGE_EXTS", "MIN_IMAGE_BYTES", "PLACEHOLDER_WORDS",
    "ext_of", "is_binary", "is_image", "looks_placeholder", "guard_text_write",
    "image_problem", "image_files", "bad_images",
]
