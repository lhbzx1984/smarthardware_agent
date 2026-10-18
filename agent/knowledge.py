"""Cirkit Designer 文档知识库：零依赖 BM25 检索。

文档由 scripts/crawl_docs.py 抓取到 knowledge/raw/*.md（23 页）。
中英混排：英文按单词切，中文按 2-gram 切，避免引入分词依赖。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import config

WORD_RE = re.compile(r"[a-z0-9_+#.-]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

CHUNK_SIZE = 700
CHUNK_OVERLAP = 120

K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    text = text.lower()
    toks = WORD_RE.findall(text)
    for seg in CJK_RE.findall(text):
        toks.append(seg) if len(seg) == 1 else toks.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    return toks


@dataclass
class Chunk:
    doc: str
    title: str
    text: str
    tokens: list[str]


class KnowledgeBase:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or config.KNOWLEDGE_DIR)
        self.chunks: list[Chunk] = []
        self.df: dict[str, int] = {}
        self.avg_len = 1.0
        self._loaded = False

    def _split(self, text: str) -> list[str]:
        paras = [p.strip() for p in text.splitlines() if p.strip()]
        out, buf = [], ""
        for p in paras:
            if len(buf) + len(p) > CHUNK_SIZE and buf:
                out.append(buf)
                buf = buf[-CHUNK_OVERLAP:] + "\n" + p
            else:
                buf = (buf + "\n" + p).strip()
        if buf:
            out.append(buf)
        return out or [text]

    def load(self) -> "KnowledgeBase":
        if self._loaded or not self.root.exists():
            return self
        for md in sorted(self.root.glob("*.md")):
            raw = md.read_text(encoding="utf-8", errors="replace")
            title = next((l.lstrip("# ").strip() for l in raw.splitlines() if l.startswith("# ")), md.stem)
            for piece in self._split(raw):
                toks = tokenize(piece)
                if len(toks) < 5:
                    continue
                self.chunks.append(Chunk(md.stem, title, piece, toks))
        for c in self.chunks:
            for t in set(c.tokens):
                self.df[t] = self.df.get(t, 0) + 1
        self.avg_len = (sum(len(c.tokens) for c in self.chunks) / len(self.chunks)) if self.chunks else 1.0
        self._loaded = True
        return self

    def search(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        self.load()
        if not self.chunks:
            return []
        q = tokenize(query)
        n = len(self.chunks)
        scored: list[tuple[Chunk, float]] = []
        for c in self.chunks:
            tf: dict[str, int] = {}
            for t in c.tokens:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for t in set(q):
                f = tf.get(t, 0)
                if not f:
                    continue
                dfr = self.df.get(t, 0)
                idf = math.log(1 + (n - dfr + 0.5) / (dfr + 0.5))
                score += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * len(c.tokens) / self.avg_len))
            if score > 0:
                scored.append((c, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[: max(1, top_k)]

    def format(self, query: str, top_k: int = 5, max_chars: int = 3000) -> str:
        hits = self.search(query, top_k)
        if not hits:
            return f"未在 Cirkit Designer 文档库中命中「{query}」。可改用通用硬件设计知识，或提示用户在 app.cirkitdesigner.com 中确认元件名。"
        parts, used = [], 0
        for i, (c, s) in enumerate(hits, 1):
            block = f"[{i}] {c.title} (来源: {c.doc}, 相关度 {s:.2f})\n{c.text}"
            if used + len(block) > max_chars:
                block = block[: max_chars - used]
            parts.append(block)
            used += len(block)
            if used >= max_chars:
                break
        return "\n\n---\n\n".join(parts)


KB = KnowledgeBase()
