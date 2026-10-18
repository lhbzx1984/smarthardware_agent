"""抓取 Cirkit Designer 文档全站 -> Markdown（零依赖，urllib + 正则）。"""
import html
import re
import sys
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
OUT = Path(__file__).resolve().parent.parent / "knowledge" / "raw"
OUT.mkdir(parents=True, exist_ok=True)

TAGS_STRIP = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def extract_article(page: str) -> str:
    m = re.search(r"<article[^>]*>(.*?)</article>", page, re.S | re.I)
    if not m:
        m = re.search(r"<main[^>]*>(.*?)</main>", page, re.S | re.I)
    return m.group(1) if m else page


def to_markdown(frag: str) -> str:
    frag = TAGS_STRIP.sub("", frag)
    out, lines = [], []
    # 块级标签 -> 换行语义
    frag = re.sub(r"</(p|div|li|tr|h[1-6]|pre|blockquote|section)>", "\n", frag, flags=re.I)
    frag = re.sub(r"<br\s*/?>", "\n", frag, flags=re.I)
    frag = re.sub(r"<li[^>]*>", "\n- ", frag, flags=re.I)
    frag = re.sub(r"<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", frag, flags=re.I)
    frag = re.sub(r"<td[^>]*>|<th[^>]*>", " | ", frag, flags=re.I)
    frag = re.sub(r"<a [^>]*href=\"([^\"]*)\"[^>]*>", r"[\1](\1) ", frag, flags=re.I)
    frag = re.sub(r"<[^>]+>", "", frag)
    frag = html.unescape(frag)
    for ln in frag.splitlines():
        ln = re.sub(r"[ \t]+", " ", ln).strip()
        if ln:
            lines.append(ln)
    # 折叠连续空行
    prev_blank = False
    for ln in lines:
        if not ln:
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out.append(ln)
    return "\n".join(out)


def slug(url: str) -> str:
    s = url.rstrip("/").split("/docs")[-1].strip("/")
    s = re.sub(r"[^0-9A-Za-z_-]+", "_", s)
    return s or "index"


DOCS_URLS = [
    "https://www.cirkitdesigner.com/docs/",
    "https://www.cirkitdesigner.com/docs/tutorials/creating-your-first-circuit",
    "https://www.cirkitdesigner.com/docs/tutorials/simulating-your-circuit",
    "https://www.cirkitdesigner.com/docs/tutorials/create-simulation-lcd",
    "https://www.cirkitdesigner.com/docs/tutorials/create-simulation-servo",
    "https://www.cirkitdesigner.com/docs/platforms/",
    "https://www.cirkitdesigner.com/docs/platforms/arduino-uno",
    "https://www.cirkitdesigner.com/docs/platforms/arduino-mega",
    "https://www.cirkitdesigner.com/docs/platforms/esp32-s3",
    "https://www.cirkitdesigner.com/docs/platforms/raspberry-pi-pico",
    "https://www.cirkitdesigner.com/docs/guides/simulation-support",
    "https://www.cirkitdesigner.com/docs/guides/wifi-networking",
    "https://www.cirkitdesigner.com/docs/custom-simulation-parts/getting-started",
    "https://www.cirkitdesigner.com/docs/custom-simulation-parts/component-examples",
    "https://www.cirkitdesigner.com/docs/custom-simulation-parts/guides/ai-component-builder-cheatsheet",
    "https://www.cirkitdesigner.com/docs/troubleshooting/",
    "https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/",
    "https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/agent-not-connecting",
    "https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/board-not-detected",
    "https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/browser-permissions",
    "https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/classroom-setup",
    "https://www.cirkitdesigner.com/docs/troubleshooting/cirkit-create-agent/universal-fix",
    "https://www.cirkitdesigner.com/docs/contributors/submission-guidelines",
]


def main() -> int:
    urls_file = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    urls = (
        [u.strip() for u in urls_file.read_text(encoding="utf-8").splitlines() if u.strip()]
        if urls_file
        else DOCS_URLS
    )
    ok = 0
    for u in urls:
        try:
            md = to_markdown(extract_article(fetch(u)))
            (OUT / f"{slug(u)}.md").write_text(f"<!-- source: {u} -->\n\n{md}\n", encoding="utf-8")
            ok += 1
            print(f"OK   {len(md):>6}  {slug(u)}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {u}: {e}")
    print(f"\n{ok}/{len(urls)} pages -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
