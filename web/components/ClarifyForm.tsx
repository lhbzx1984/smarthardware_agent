"use client";

export type Question = {
  key: string;
  label: string;
  type: "text" | "textarea" | "select" | "multiselect" | "number";
  options: string[];
  placeholder: string;
  default: string;
  required: boolean;
  why: string;
  sticky?: boolean;
  section?: string;
};

// 问卷框架（分区）由后端骨架给出，跨产品固定：换个产品，分区和字段顺序不变，
// 变的是每个字段里的选项。传了 sections 就按分区渲染，没传就平铺（兼容旧数据）。
export type Section = {
  id: string;
  title: string;
  hint?: string;
  keys: string[];
};

const INPUT: React.CSSProperties = {
  background: "var(--bg-sunken)",
  border: "1px solid var(--border)",
  color: "var(--text)",
};

// 「其他（请补充）」「自定义金额」这类选项 = 兜底项：选中后展开自由输入框，
// 能点选就点选，选不了就写。提交值只存用户实际输入的文字，不存兜底项本身。
const isCustomOpt = (o: string) => o.startsWith("其他") || o.startsWith("自定义");

function Field({
  q,
  answers,
  onChange,
}: {
  q: Question;
  answers: Record<string, string>;
  onChange: (k: string, v: string) => void;
}) {
  return (
    <div>
      <label className="mb-1 flex items-center gap-1 text-[12px] font-medium" style={{ color: "var(--text-dim)" }}>
        {q.required && <span style={{ color: "var(--danger)" }}>*</span>}
        {q.label}
        {q.sticky && (
          <span
            title="这项属于稳定偏好，会自动沿用你上次填写的值；改一次，下次就记住新的"
            className="rounded px-1 py-[1px] text-[9px] font-normal"
            style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
          >
            记忆
          </span>
        )}
      </label>

      {(q.type === "text" || q.type === "number") && (
        <input
          type={q.type === "number" ? "number" : "text"}
          value={answers[q.key] ?? q.default ?? ""}
          onChange={(e) => onChange(q.key, e.target.value)}
          placeholder={q.placeholder}
          className="w-full rounded-xl px-3 py-2 text-[12px] outline-none"
          style={INPUT}
        />
      )}

      {q.type === "textarea" && (
        <textarea
          rows={2}
          value={answers[q.key] ?? q.default ?? ""}
          onChange={(e) => onChange(q.key, e.target.value)}
          placeholder={q.placeholder}
          className="w-full resize-none rounded-xl px-3 py-2 text-[12px] outline-none"
          style={INPUT}
        />
      )}

      {q.type === "select" &&
        (() => {
          const customOpt = q.options.find(isCustomOpt);
          const knownOpts = q.options.filter((o) => !isCustomOpt(o));
          const stored = answers[q.key] ?? q.default ?? "";
          const touched = q.key in answers;
          const isKnown = knownOpts.includes(stored);
          // 自定义文字 / 选了兜底项还没填 → select 停在兜底项上
          const selectVal = isKnown ? stored : customOpt && (touched || stored) ? customOpt : stored;
          const customText = stored && !q.options.includes(stored) ? stored : "";
          return (
            <>
              <select
                value={selectVal}
                onChange={(e) => {
                  const v = e.target.value;
                  // 选兜底项：清空存储值等输入（必填题的空值会被提交校验拦住）
                  onChange(q.key, customOpt && v === customOpt ? "" : v);
                }}
                className="w-full rounded-xl px-3 py-2 text-[12px] outline-none"
                style={INPUT}
              >
                <option value="">请选择…</option>
                {q.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
              {customOpt && selectVal === customOpt && (
                <input
                  value={customText}
                  onChange={(e) => onChange(q.key, e.target.value)}
                  placeholder={q.placeholder || "请补充说明"}
                  autoFocus
                  className="mt-1.5 w-full rounded-xl px-3 py-2 text-[12px] outline-none"
                  style={INPUT}
                />
              )}
            </>
          );
        })()}

      {q.type === "multiselect" &&
        (() => {
          const customOpt = q.options.find(isCustomOpt);
          const knownOpts = q.options.filter((o) => !isCustomOpt(o));
          const parts = (answers[q.key] || "").split("、").filter(Boolean);
          const knownPicked = parts.filter((p) => knownOpts.includes(p));
          const customText = parts.filter((p) => !q.options.includes(p)).join("、");
          const customOn = !!customOpt && (parts.includes(customOpt) || customText !== "");
          const commit = (known: string[], custom: string, on: boolean) => {
            const segs = [...known];
            if (customOpt && on) segs.push(custom || customOpt);
            onChange(q.key, segs.join("、"));
          };
          const chip = (o: string, picked: boolean, toggle: () => void) => (
            <button
              key={o}
              onClick={toggle}
              className="magnetic rounded-lg px-2 py-1 text-[11px]"
              style={{
                background: picked ? "var(--accent-soft)" : "var(--bg-sunken)",
                color: picked ? "var(--accent)" : "var(--text-dim)",
                border: `1px solid ${picked ? "var(--accent)" : "var(--border)"}`,
              }}
            >
              {o}
            </button>
          );
          return (
            <>
              <div className="flex flex-wrap gap-1.5">
                {knownOpts.map((o) =>
                  chip(o, knownPicked.includes(o), () => {
                    const next = knownPicked.includes(o)
                      ? knownPicked.filter((x) => x !== o)
                      : [...knownPicked, o];
                    commit(next, customText, customOn);
                  }),
                )}
                {customOpt &&
                  chip(customOpt, customOn, () => commit(knownPicked, customOn ? "" : customText, !customOn))}
              </div>
              {customOpt && customOn && (
                <input
                  value={customText}
                  onChange={(e) => commit(knownPicked, e.target.value, true)}
                  placeholder={q.placeholder || "请补充说明"}
                  autoFocus
                  className="mt-1.5 w-full rounded-xl px-3 py-2 text-[12px] outline-none"
                  style={INPUT}
                />
              )}
            </>
          );
        })()}

      {q.why && (
        <p className="mt-1 text-[10px] leading-snug" style={{ color: "var(--text-faint)" }}>
          {q.why}
        </p>
      )}
    </div>
  );
}

export default function ClarifyForm({
  questions,
  answers,
  onChange,
  sections,
}: {
  questions: Question[];
  answers: Record<string, string>;
  onChange: (k: string, v: string) => void;
  sections?: Section[];
}) {
  if (questions.length === 0) return null;

  const byKey = new Map(questions.map((q) => [q.key, q]));
  // 按骨架分区归类；分区里没排到的题（旧数据 / 后端加了新字段）兜底挂到最后一组
  let groups: { id: string; title: string; hint?: string; items: Question[] }[] = [];
  if (sections && sections.length) {
    const taken = new Set<string>();
    for (const s of sections) {
      const items = s.keys.map((k) => byKey.get(k)).filter((q): q is Question => !!q);
      for (const q of items) taken.add(q.key);
      if (items.length) groups.push({ id: s.id, title: s.title, hint: s.hint, items });
    }
    const rest = questions.filter((q) => !taken.has(q.key));
    if (rest.length) groups.push({ id: "_rest", title: "补充信息", items: rest });
  } else {
    groups = [{ id: "_all", title: "", items: questions }];
  }

  return (
    <div className="flex flex-col gap-3">
      {groups.map((g) => {
        const filled = g.items.filter((q) => String(answers[q.key] || "").trim()).length;
        return (
          <div
            key={g.id}
            className="rounded-xl px-3 py-2.5"
            style={{ background: "var(--bg-sunken)", border: "1px solid var(--border)" }}
          >
            {g.title && (
              <div className="mb-2 flex items-baseline gap-2">
                <span className="text-[12px] font-semibold" style={{ color: "var(--text)" }}>
                  {g.title}
                </span>
                <span className="text-[10px]" style={{ color: "var(--text-faint)" }}>
                  {filled}/{g.items.length}
                </span>
                {g.hint && (
                  <span className="ml-auto text-[10px]" style={{ color: "var(--text-faint)" }}>
                    {g.hint}
                  </span>
                )}
              </div>
            )}
            <div className="flex flex-col gap-3">
              {g.items.map((q) => (
                <Field key={q.key} q={q} answers={answers} onChange={onChange} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
