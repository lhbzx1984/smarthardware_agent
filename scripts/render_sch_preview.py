"""CLI：把 .epro 的原理图渲染成 SVG。

    python scripts/render_sch_preview.py workspace/<run>/30_hardware/board.epro [out.svg]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.eda.preview import write_svg            # noqa: E402

if __name__ == "__main__":
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".sch.svg")
    p = write_svg(src, out, src.stem)
    print(f"写出 {p}（{p.stat().st_size} 字节）")
