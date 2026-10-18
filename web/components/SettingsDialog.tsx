"use client";

import { useEffect, useMemo, useState } from "react";
import {
  DEFAULT_SETTINGS,
  IMAGE_PROVIDERS,
  loadSettings,
  saveSettings,
  type ModelSettings,
} from "@/lib/settings";

type Provider = {
  id: string;
  name: string;
  base_url: string;
  models: string[];
  key_hint: string;
  note: string;
};

const INPUT: React.CSSProperties = {
  background: "var(--bg-sunken)",
  border: "1px solid var(--border)",
  color: "var(--text)",
};

export default function SettingsDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (s: ModelSettings) => void;
}) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [draft, setDraft] = useState<ModelSettings>(DEFAULT_SETTINGS);
  const [showKey, setShowKey] = useState(false);
  const [remote, setRemote] = useState<string[]>([]);
  const [test, setTest] = useState<{ state: "idle" | "busy" | "ok" | "err"; msg: string }>({
    state: "idle",
    msg: "",
  });
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDraft(loadSettings());
    setTest({ state: "idle", msg: "" });
    setRemote([]);
  }, [open]);

  useEffect(() => {
    fetch("/api/agent/providers")
      .then((r) => r.json())
      .then((d) => setProviders(d.providers || []))
      .catch(() => setProviders([]));
  }, []);

  const current = useMemo(
    () => providers.find((p) => p.id === draft.provider),
    [providers, draft.provider],
  );

  const set = <K extends keyof ModelSettings>(k: K, v: ModelSettings[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const pickProvider = (id: string) => {
    const p = providers.find((x) => x.id === id);
    setDraft((d) => ({
      ...d,
      provider: id,
      baseUrl: p?.base_url ?? d.baseUrl,
      model: p?.models?.[0] ?? d.model,
    }));
  };

  // 用 base_url 反推当前选的是哪家（留空 = 跟随文本模型）。
  // 比较前先规范化：手写端点常带尾斜杠/大写，严格 === 会把 Agnes 误判成「跟随」，
  // 结果是密钥与模型输入框双双隐藏，用户以为设置丢了。
  const imageProvider = useMemo(() => {
    const norm = (s: string) => s.trim().replace(/\/+$/, "").toLowerCase();
    const b = norm(draft.imageBaseUrl);
    if (!b) return "follow";
    return IMAGE_PROVIDERS.find((p) => p.base_url && norm(p.base_url) === b)?.id || "follow";
  }, [draft.imageBaseUrl]);

  const imageProviderModel = useMemo(
    () => IMAGE_PROVIDERS.find((p) => p.id === imageProvider)?.model || "",
    [imageProvider],
  );

  const imageModelCandidates = useMemo(
    () => IMAGE_PROVIDERS.find((p) => p.id === imageProvider)?.models || [],
    [imageProvider],
  );

  const pickImageProvider = (id: string) => {
    if (id === "follow") {
      setDraft((d) => ({ ...d, imageBaseUrl: "", imageModel: "" }));
      return;
    }
    const p = IMAGE_PROVIDERS.find((x) => x.id === id);
    if (!p) return;
    setDraft((d) => {
      // 手填的模型 ID 一律保留：切服务商只该换端点，不该把手写的模型冲掉
      // （实测踩过：填了 agnes-image-2.5-flash，下来回切一次就变回内置的 2.1）
      const cur = d.imageModel.trim();
      const belongsOther = IMAGE_PROVIDERS.some(
        (x) => x.id !== id && x.model && x.model === cur,
      );
      const keep = cur && !belongsOther ? cur : "";
      return { ...d, imageBaseUrl: p.base_url, imageModel: keep || p.model };
    });
  };

  const runTest = async () => {
    setTest({ state: "busy", msg: "正在拉取模型列表…" });
    try {
      const r = await fetch("/api/agent/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_url: draft.baseUrl, api_key: draft.apiKey }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "连接失败");
      setRemote(d.models || []);
      // 光拉到模型列表不算通：有的网关 /models 200 却一律 402（余额为 0），
      // 这时报「连接成功」会让用户开跑一轮必失败的任务
      const p = d.probe;
      if (p?.state === "ok") {
        setTest({ state: "ok", msg: `连接成功，可用模型 ${d.count} 个；实测调用 ${p.model} 正常` });
      } else if (p?.state === "quota") {
        setTest({
          state: "err",
          msg: `能拉到 ${d.count} 个模型，但实测调用 ${p.model} 失败：${p.detail} —— 额度/鉴权问题，等待和重试都无效，需充值或换 key`,
        });
      } else if (p?.state === "unreachable") {
        setTest({ state: "err", msg: `模型列表能拉到，但调用不通：${p.detail}` });
      } else if (p?.state) {
        setTest({ state: "err", msg: `实测调用 ${p.model} 失败：${p.detail}` });
      } else {
        setTest({ state: "ok", msg: `连接成功，可用模型 ${d.count} 个` });
      }
    } catch (e) {
      setTest({ state: "err", msg: (e as Error).message });
    }
  };

  const save = () => {
    saveSettings(draft);
    onSaved(draft);
    onClose();
  };

  if (!open) return null;

  // 去重：网关 /models 偶尔会返回重复 id，且当前模型可能已在列表里，
  // 直接用模型名当 key 会撞 React 的「同 key 兄弟节点」告警
  const options = Array.from(
    new Set(
      (remote.length
        ? remote.slice(0, 200)
        : [...(current?.models || []), ...(draft.model ? [draft.model] : [])]
      )
        .map((m) => String(m || "").trim())
        .filter(Boolean),
    ),
  );

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      style={{ background: "rgba(0,0,0,0.45)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        className="scroll-thin enter max-h-[88vh] w-full max-w-[600px] overflow-y-auto rounded-2xl p-5"
        style={{ background: "var(--bg-elev)", border: "1px solid var(--border)", boxShadow: "var(--shadow)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-2">
          <h2 className="text-base font-semibold">大模型设置</h2>
          <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
            密钥仅保存在本机浏览器，worker 只在内存中使用，不落盘
          </span>
          <button onClick={onClose} className="ml-auto text-sm" style={{ color: "var(--text-faint)" }}>
            关闭
          </button>
        </div>

        <div className="flex flex-col gap-3.5">
          <Field label="服务商">
            <select
              value={draft.provider}
              onChange={(e) => pickProvider(e.target.value)}
              className="w-full rounded-xl px-3 py-2 text-sm outline-none"
              style={INPUT}
            >
              {providers.length === 0 && <option value={draft.provider}>加载中…</option>}
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            {current?.note && (
              <p className="mt-1 text-[11px]" style={{ color: "var(--text-faint)" }}>
                {current.note}
              </p>
            )}
          </Field>

          <Field label="Base URL">
            <input
              value={draft.baseUrl}
              onChange={(e) => set("baseUrl", e.target.value)}
              placeholder="https://api.siliconflow.cn/v1"
              className="w-full rounded-xl px-3 py-2 font-mono text-[12px] outline-none"
              style={INPUT}
            />
          </Field>

          <Field label="API Key">
            <div className="flex gap-2">
              <input
                type={showKey ? "text" : "password"}
                value={draft.apiKey}
                onChange={(e) => set("apiKey", e.target.value)}
                placeholder={current?.key_hint || "sk-…"}
                className="w-full rounded-xl px-3 py-2 font-mono text-[12px] outline-none"
                style={INPUT}
              />
              <button
                onClick={() => setShowKey((v) => !v)}
                className="magnetic shrink-0 rounded-xl px-3 py-2 text-[12px]"
                style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
              >
                {showKey ? "隐藏" : "显示"}
              </button>
            </div>
          </Field>

          <Field label="模型">
            <input
              list="pd-model-options"
              value={draft.model}
              onChange={(e) => set("model", e.target.value)}
              placeholder="输入或选择模型 ID"
              className="w-full rounded-xl px-3 py-2 font-mono text-[12px] outline-none"
              style={INPUT}
            />
            <datalist id="pd-model-options">
              {options.map((m, i) => (
                <option key={`${m}#${i}`} value={m} />
              ))}
            </datalist>
            <div className="mt-1.5 flex items-center gap-2">
              <button
                onClick={runTest}
                disabled={test.state === "busy" || !draft.apiKey.trim() || !draft.baseUrl.trim()}
                className="magnetic rounded-lg px-2.5 py-1 text-[11px]"
                style={{
                  background: "var(--accent-soft)",
                  color: "var(--accent)",
                  cursor: draft.apiKey.trim() ? "pointer" : "not-allowed",
                  opacity: draft.apiKey.trim() ? 1 : 0.5,
                }}
              >
                {test.state === "busy" ? "测试中…" : "测试连接 / 拉取模型"}
              </button>
              {test.msg && (
                <span
                  className="text-[11px]"
                  style={{
                    color:
                      test.state === "ok"
                        ? "var(--accent-2)"
                        : test.state === "err"
                          ? "var(--danger)"
                          : "var(--text-faint)",
                  }}
                >
                  {test.msg}
                </span>
              )}
            </div>
            {!draft.apiKey.trim() && (
              <p className="mt-1.5 text-[11px]" style={{ color: "var(--warn)" }}>
                未填 API Key 时，需求补全走内置模板、设计流程走演示模式。
              </p>
            )}
          </Field>

          <Field label={`温度 ${draft.temperature.toFixed(2)}`}>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={draft.temperature}
              onChange={(e) => set("temperature", Number(e.target.value))}
              className="w-full accent-[var(--accent)]"
            />
            <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-faint)" }}>
              越低越严谨稳定，越高越发散；工程设计建议 0.3–0.7
            </p>
          </Field>

          <div className="rounded-xl p-3" style={{ background: "var(--bg-sunken)" }}>
            <label className="flex cursor-pointer items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={draft.thinking}
                onChange={(e) => set("thinking", e.target.checked)}
                className="accent-[var(--accent)]"
              />
              开启深度思考（流式展示推理过程）
            </label>
            <button
              onClick={() => setAdvanced((v) => !v)}
              className="mt-1.5 text-[11px] underline"
              style={{ color: "var(--text-faint)" }}
            >
              {advanced ? "收起高级参数" : "高级：自定义思考参数"}
            </button>
            {advanced && (
              <div className="mt-2">
                <input
                  value={draft.thinkingBody}
                  onChange={(e) => set("thinkingBody", e.target.value)}
                  placeholder='留空自动推断，例如 {"enable_thinking": true}'
                  className="w-full rounded-lg px-2.5 py-1.5 font-mono text-[11px] outline-none"
                  style={INPUT}
                />
                <p className="mt-1 text-[11px]" style={{ color: "var(--text-faint)" }}>
                  Qwen3 → enable_thinking；GLM-4 → thinking.type；DeepSeek-R1 无需参数
                </p>
              </div>
            )}
          </div>
        </div>

        {/* 图像网关独立于文本模型：一家被墙/欠费时只切图像，不用连文本模型一起换 */}
        <div className="mt-4 rounded-xl p-3" style={{ background: "var(--bg-sunken)" }}>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-[13px] font-medium">图像生成（渲染图 / 爆炸图）</span>
            <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
              默认 Agnes，与文本模型解耦；出图的提示词会存档给结构阶段复用
            </span>
          </div>

          <div className="flex flex-col gap-2.5">
            <select
              value={imageProvider}
              onChange={(e) => pickImageProvider(e.target.value)}
              className="w-full rounded-lg px-2.5 py-1.5 text-[12px] outline-none"
              style={INPUT}
            >
              {IMAGE_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            {imageProvider !== "follow" && (
              <>
                <input
                  type={showKey ? "text" : "password"}
                  value={draft.imageApiKey}
                  onChange={(e) => set("imageApiKey", e.target.value)}
                  placeholder="图像服务 API Key（留空则用 .env 里同网关的那把）"
                  className="w-full rounded-lg px-2.5 py-1.5 font-mono text-[11px] outline-none"
                  style={INPUT}
                />
                <input
                  list="pd-image-model-options"
                  value={draft.imageModel}
                  onChange={(e) => set("imageModel", e.target.value)}
                  placeholder={imageProviderModel || "图像模型 ID（留空按网关默认）"}
                  className="w-full rounded-lg px-2.5 py-1.5 font-mono text-[11px] outline-none"
                  style={INPUT}
                />
                <datalist id="pd-image-model-options">
                  {imageModelCandidates.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
                {/* 明确回显「这次真会用哪个模型」：留空不等于不生效，而是走该网关注册的默认 */}
                <p className="text-[11px]" style={{ color: "var(--text-faint)" }}>
                  本次出图使用：{" "}
                  <span className="font-mono" style={{ color: "var(--text-dim)" }}>
                    {draft.imageModel.trim() || imageProviderModel || "网关默认"}
                  </span>
                  {!draft.imageModel.trim() && imageProviderModel ? "（内置默认）" : ""}
                </p>
              </>
            )}
            <p className="text-[11px]" style={{ color: "var(--text-faint)" }}>
              拿不到 AI 图时智能体会自动改用真实 3D 几何出工程视图与爆炸图（离线，必定成功），
              外观阶段不会留空。
            </p>
          </div>
        </div>

        <div className="mt-5 flex gap-2">
          <button
            onClick={save}
            className="magnetic flex-1 rounded-xl px-4 py-2.5 text-sm font-medium"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            保存设置
          </button>
          <button
            onClick={onClose}
            className="magnetic rounded-xl px-4 py-2.5 text-sm"
            style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-[12px] font-medium" style={{ color: "var(--text-dim)" }}>
        {label}
      </label>
      {children}
    </div>
  );
}
