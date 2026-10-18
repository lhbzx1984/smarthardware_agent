"use client";

import { useEffect, useRef, useState } from "react";

/**
 * 深度思考块：流式打字 + 思考中动效 + 折叠/展开 + 自动跟随滚动。
 * 运行中默认展开（看得到实时推理），结束后自动折叠为一行摘要，用户手动点过就不再自动折叠。
 */
export default function ThinkingBlock({
  agent,
  label,
  text,
  done,
}: {
  agent: string;
  label: string;
  text: string;
  done: boolean;
}) {
  const [open, setOpen] = useState(true);
  const touched = useRef(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // 短思考保持展开（一眼可见），长思考结束后自动折叠，避免刷屏；用户手动点过则尊重用户选择
  useEffect(() => {
    if (!done || touched.current) return;
    if (text.length > 420) setOpen(false);
  }, [done, text.length]);

  // 只有用户在底部时才自动跟随，避免打断向上翻阅
  useEffect(() => {
    const el = bodyRef.current;
    if (!el || !pinned.current || !open) return;
    el.scrollTop = el.scrollHeight;
  }, [text, open]);

  const onScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  const toggle = () => {
    touched.current = true;
    setOpen((v) => !v);
  };

  return (
    <div
      className="enter think-block overflow-hidden rounded-xl"
      style={{ background: "var(--think-bg)", border: "1px solid var(--think-border)" }}
    >
      <button
        onClick={toggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        style={{ color: "var(--think)" }}
      >
        <span className="think-spark shrink-0" aria-hidden>
          ◈
        </span>
        <span className="text-[10px] font-medium tracking-wide">深度思考</span>
        <span className="text-[10px]" style={{ color: "var(--text-faint)" }}>
          {label || agent}
        </span>
        {!done ? (
          <span className="flex items-center gap-1 text-[10px]" style={{ color: "var(--think)" }}>
            <span className="pulse-dot h-1.5 w-1.5 rounded-full" style={{ background: "var(--think)" }} />
            推理中
          </span>
        ) : (
          <span className="text-[10px]" style={{ color: "var(--text-faint)" }}>
            {text.length} 字
          </span>
        )}
        <span className="ml-auto text-[10px] transition-transform" style={{ color: "var(--text-faint)" }}>
          {open ? "收起 ▲" : "展开 ▼"}
        </span>
      </button>
      {open && (
        <div
          ref={bodyRef}
          onScroll={onScroll}
          className="scroll-thin max-h-[190px] overflow-y-auto px-3 pb-2.5"
        >
          <p
            className="whitespace-pre-wrap text-[12px] leading-[1.75]"
            style={{ color: "var(--think-text)", fontStyle: "normal" }}
          >
            {text}
            {!done && <span className="caret" />}
          </p>
        </div>
      )}
    </div>
  );
}
