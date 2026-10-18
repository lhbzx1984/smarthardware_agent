"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    const cur = (document.documentElement.getAttribute("data-theme") as Theme) || "light";
    setTheme(cur);
  }, []);

  const apply = (t: Theme) => {
    setTheme(t);
    document.documentElement.setAttribute("data-theme", t);
    try {
      localStorage.setItem("pda-theme", t);
    } catch {
      /* 隐私模式下忽略 */
    }
  };

  return (
    <div
      className="magnetic flex items-center gap-1 rounded-full p-1"
      style={{ background: "var(--bg-sunken)", border: "1px solid var(--border)" }}
      role="group"
      aria-label="主题切换"
    >
      {(["light", "dark"] as Theme[]).map((t) => (
        <button
          key={t}
          onClick={() => apply(t)}
          aria-pressed={theme === t}
          className="rounded-full px-3 py-1 text-xs font-medium transition-all"
          style={{
            background: theme === t ? "var(--bg-elev)" : "transparent",
            color: theme === t ? "var(--text)" : "var(--text-faint)",
            boxShadow: theme === t ? "var(--shadow)" : "none",
          }}
        >
          {t === "light" ? "浅色" : "深色"}
        </button>
      ))}
    </div>
  );
}
