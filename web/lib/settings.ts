/** 模型设置：存浏览器 localStorage，随每次请求发给 worker，worker 不落盘。 */

const KEY = "pd-agent.model-settings.v1";

export type ModelSettings = {
  provider: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  temperature: number;
  thinking: boolean;
  /** 深度思考附加参数（JSON 字符串），留空则由后端按模型名自动推断 */
  thinkingBody: string;

  /**
   * 图像生成可独立于文本模型指定服务商 —— 产品图（渲染图 / 场景图 / 爆炸图）默认固定
   * 走 Agnes：同一台产品的几张图必须同画风，不该因为用户把文本模型换成别家就换一批画风。
   * 想换图床时在这里覆盖即可。
   */
  imageBaseUrl: string;
  imageApiKey: string;
  imageModel: string;
};

// 首次打开时的默认值。apiKey 留空 —— 后端会按 base_url 自动回落到服务端 .env 里的密钥，
// 所以用户什么都不填也能直接跑真实智能体。
export const DEFAULT_SETTINGS: ModelSettings = {
  provider: "agnes",
  baseUrl: "https://apihub.agnes-ai.com/v1",
  apiKey: "",
  model: "agnes-2.5-flash",
  temperature: 0.6,
  thinking: true,
  thinkingBody: "",
  // 出图默认 Agnes（与文本模型解耦），密钥留空由后端按端点回落到 .env
  imageBaseUrl: "https://apihub.agnes-ai.com/v1",
  imageApiKey: "",
  imageModel: "agnes-image-2.5-flash",
};

/** 可单独配图像的服务商（后端 config.IMAGE_PROVIDERS 的子集，只列支持文生图的）。
 *  models 是该网关「注册在册、实测可出图」的候选，供输入框补全用 —— 手填别的也行。 */
export const IMAGE_PROVIDERS = [
  { id: "follow", name: "跟随后端默认（Agnes）", base_url: "", model: "", models: [] as string[] },
  { id: "siliconflow", name: "硅基流动 SiliconFlow", base_url: "https://api.siliconflow.cn/v1", model: "black-forest-labs/FLUX.1-schnell", models: ["black-forest-labs/FLUX.1-schnell", "black-forest-labs/FLUX.1-dev"] },
  { id: "dashscope", name: "阿里百炼（通义万相）", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "wanx2.1-t2i-turbo", models: ["wanx2.1-t2i-turbo", "wanx2.1-t2i-plus"] },
  // 实测 2026-10-15：agnes 网关注册了 2.0 / 2.1 / 2.5 三代，2.5-flash 可用且耗时相当
  { id: "agnes", name: "Agnes AI", base_url: "https://apihub.agnes-ai.com/v1", model: "agnes-image-2.5-flash", models: ["agnes-image-2.5-flash", "agnes-image-2.1-flash", "agnes-image-2.0-flash"] },
  { id: "openai", name: "OpenAI (DALL·E)", base_url: "https://api.openai.com/v1", model: "dall-e-3", models: ["dall-e-3", "gpt-image-1"] },
];

/** 服务端 /health 下发的预置配置（不含密钥） */
export type ServerDefault = {
  provider?: string;
  base_url?: string;
  model?: string;
  has_key?: string;
};

/** 本地没保存过设置时，用服务端预置值对齐 */
export function withServerDefault(s: ModelSettings, d?: ServerDefault | null): ModelSettings {
  if (!d?.base_url) return s;
  if (typeof window !== "undefined" && window.localStorage.getItem(KEY)) return s;
  return { ...s, provider: d.provider || s.provider, baseUrl: d.base_url, model: d.model || s.model };
}

export function loadSettings(): ModelSettings {
  if (typeof window === "undefined") return { ...DEFAULT_SETTINGS };
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const p = JSON.parse(raw) as Partial<ModelSettings>;
    return {
      ...DEFAULT_SETTINGS,
      ...p,
      temperature: typeof p.temperature === "number" ? p.temperature : DEFAULT_SETTINGS.temperature,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(s: ModelSettings) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* 隐私模式下写入失败不影响使用 */
  }
}

/** 发给 worker 的载荷：thinkingBody 为空串时不发送，交给后端推断 */
export function toPayload(s: ModelSettings) {
  const body = s.thinkingBody.trim();
  return {
    provider: s.provider,
    base_url: s.baseUrl,
    api_key: s.apiKey,
    model: s.model,
    temperature: s.temperature,
    thinking: s.thinking,
    thinking_body: body || null,
    // 留空即跟随文本模型，后端会按 base_url 回落；不显式传 null，省得后端判空
    image_base_url: s.imageBaseUrl.trim(),
    image_api_key: s.imageApiKey.trim(),
    image_model: s.imageModel.trim(),
  };
}
