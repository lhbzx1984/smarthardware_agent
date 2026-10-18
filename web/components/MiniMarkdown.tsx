"use client";

import { useMemo } from "react";

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 图片地址：外链原样用，产物相对路径走 worker 的二进制接口（带目录基准兜底） */
function imgSrc(raw: string, runId?: string, base?: string): string {
  const u = raw.trim();
  if (/^(https?:|data:|blob:)/i.test(u)) return u;
  const qs = new URLSearchParams({ path: u.replace(/^\.\//, "") });
  if (runId) qs.set("run_id", runId);
  if (base) qs.set("base", base);
  return `/api/agent/raw?${qs.toString()}`;
}

function inline(s: string, runId?: string, base?: string): string {
  let out = esc(s);
  // 图片先处理，避免其中的方括号被链接规则抢走
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, alt, src) => {
    const safe = esc(alt || "图片");
    return `<img src="${esc(imgSrc(src, runId, base))}" alt="${safe}" loading="lazy" />`;
  });
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  out = out.replace(
    /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer" style="color:var(--accent)">$1</a>',
  );
  return out;
}

/** 极简 Markdown -> HTML：先转义再解析，产物内容不会被当作 HTML 执行。 */
export function renderMarkdown(src: string, runId?: string, base?: string): string {
  if (!src) return "";
  const blocks: string[] = [];
  const stash: string[] = [];
  let text = src.replace(/\r\n/g, "\n");

  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, lang, code) => {
    stash.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000BLOCK${stash.length - 1}\u0000`;
  });

  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    const blockRef = /^\u0000BLOCK(\d+)\u0000$/.exec(line.trim());
    if (blockRef) {
      blocks.push(stash[Number(blockRef[1])]);
      i++;
      continue;
    }

    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const lvl = Math.min(h[1].length, 4);
      blocks.push(`<h${lvl}>${inline(h[2], runId, base)}</h${lvl}>`);
      i++;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push(`<blockquote>${inline(quote.join(" "), runId, base)}</blockquote>`);
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].trim().slice(1, -1).split("|").map((c) => c.trim()));
        i++;
      }
      blocks.push(
        `<table><thead><tr>${head.map((c) => `<th>${inline(c, runId, base)}</th>`).join("")}</tr></thead><tbody>${rows
          .map((r) => `<tr>${r.map((c) => `<td>${inline(c, runId, base)}</td>`).join("")}</tr>`)
          .join("")}</tbody></table>`
      );
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(inline(lines[i].replace(/^\s*[-*+]\s+/, ""), runId, base));
        i++;
      }
      blocks.push(`<ul>${items.map((t) => `<li>${t}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(inline(lines[i].replace(/^\s*\d+[.)]\s+/, ""), runId, base));
        i++;
      }
      blocks.push(`<ol>${items.map((t) => `<li>${t}</li>`).join("")}</ol>`);
      continue;
    }

    if (/^\s*-{3,}\s*$/.test(line)) {
      blocks.push("<hr />");
      i++;
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\u0000BLOCK\d+\u0000$/.test(lines[i].trim())
    ) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(`<p>${inline(para.join(" "), runId, base)}</p>`);
  }

  return blocks.join("\n");
}

export default function MiniMarkdown({
  content,
  runId,
  base,
}: {
  content: string;
  runId?: string;
  /** 图片相对路径的查找基准（通常是当前文档所在目录） */
  base?: string;
}) {
  const html = useMemo(() => renderMarkdown(content, runId, base), [content, runId, base]);
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}
