"""全局配置：从 .env 读取，不把任何凭据写进代码。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True：.env 是本项目唯一的显式配置入口，必须盖住外部环境变量。
# 曾踩坑：shell 里残留了一个同名 AGNES_API_KEY，把 .env 的密钥静默顶掉，
# 表现为「明明换了 key 还是 401」，排查成本极高。
load_dotenv(ROOT / ".env", override=True)

# ---------------------------------------------------------------- 网络层全局补丁：IPv4 优先
# 实测（2026-10-14，本机 → apihub.agnes-ai.com）：DNS 同时返回 IPv4/IPv6（Cloudflare 双栈），
# 本机 IPv6 出口高延迟且抖动（同一秒测：v4 0.3s 连上，v6 11.2s；丢包时段直接超时）。
# Python 的 socket 按 DNS 顺序串行尝试、IPv6 排前面 → 网关表现为「时通时连接超时」，
# 看日志就像网关间歇性下线。curl 能通是因为它有 happy eyeballs 并行尝试。
# 这里把解析结果**排序**为 IPv4 优先（不是过滤：IPv4 全挂时仍能回退 IPv6），
# 从根上消掉这类假「网关不可达」。进程级补丁，对 httpx / urllib / 各家 SDK 都生效。
# 设 AGENT_IPV4_FIRST=0 可关闭。
if os.getenv("AGENT_IPV4_FIRST", "1").strip().lower() not in ("0", "false", "no", "off"):
    import socket as _socket

    _orig_getaddrinfo = _socket.getaddrinfo

    def _ipv4_first_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        addrs = _orig_getaddrinfo(host, port, family, type, proto, flags)
        addrs.sort(key=lambda a: 0 if a[0] == _socket.AF_INET else 1)
        return addrs

    _socket.getaddrinfo = _ipv4_first_getaddrinfo

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")

# ---------------------------------------------------------------- Agnes（图像生成）
# 渲染图 / 爆炸图走 Agnes；key 优先取前端运行时传入，其次 .env
AGNES_API_KEY = os.getenv("AGNES_API_KEY", "")
AGNES_BASE_URL = os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
# 图像模型默认值。实测（2026-10-15）agnes 网关注册了 2.0 / 2.1 / 2.5 三代，
# 2.5-flash 可用且耗时相当（16.4s / 680KB vs 2.1 的 13.9s），故取最新一代为默认。
AGNES_IMAGE_MODEL = os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.5-flash")

# 默认模型。实测结论：
#   agnes-2.5-flash   —— 原生流式返回 reasoning_content，工具调用稳定（默认）
#   Qwen/Qwen3.5-*    —— 硅基流动上既能开 thinking 又能工具调用，但需账户有余额
#   deepseek-ai/DeepSeek-V3.2 —— 硅基流动不下发 reasoning_content，不作默认思考模型
DEFAULT_PROVIDER = os.getenv("AGENT_PROVIDER", "agnes" if os.getenv("AGNES_API_KEY") else "siliconflow")
DEFAULT_BASE_URL = os.getenv(
    "AGENT_BASE_URL",
    "https://apihub.agnes-ai.com/v1" if DEFAULT_PROVIDER == "agnes" else "https://api.siliconflow.cn/v1",
)
MODEL = os.getenv("AGENT_MODEL", "agnes-2.5-flash" if DEFAULT_PROVIDER == "agnes" else "Qwen/Qwen3.5-122B-A10B")
SUBAGENT_MODEL = os.getenv("AGENT_SUB_MODEL", MODEL)
FALLBACK_MODEL = os.getenv("AGENT_FALLBACK_MODEL", "deepseek-ai/DeepSeek-V3.2")

# 需求补全问卷是否让模型按产品类型现场出题。
# 默认 0 = 用固定模板 clarify-template.json：题目稳定、点开即出、不占一轮 LLM 往返
# （网关排队时一轮 40~80 秒，为一份问卷等这么久不划算，而且题目每次都不一样）。
# 设 1 = 回到「模型出题」的老行为。
CLARIFY_BY_MODEL = os.getenv("AGENT_CLARIFY_MODEL", "0") == "1"

# ---------------------------------------------------------------- 图像生成
# 不同网关的图像端点/默认模型不同，且**不能混用密钥**：
# 拿 A 家的 key 打 B 家的端点，网关只会回 401 Invalid token，看不出是配错了。
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# 实测连通性（2026-10-10 本机）：siliconflow / dashscope / deepseek 可达，
# apihub.agnes-ai.com 被网络层拦截（TLS 握手拿自签证书）。所以图像网关必须可切换，
# 只认一家等于把整条外观链路绑死在一条随时可能断的线路上。
IMAGE_PROVIDERS: dict[str, dict[str, str]] = {
    "agnes": {"base_url": AGNES_BASE_URL, "model": AGNES_IMAGE_MODEL},
    "siliconflow": {"base_url": SILICONFLOW_BASE_URL, "model": "black-forest-labs/FLUX.1-schnell"},
    "dashscope": {"base_url": DASHSCOPE_BASE_URL, "model": "wanx2.1-t2i-turbo"},
    "openai": {"base_url": OPENAI_BASE_URL, "model": "dall-e-3"},
}
# 自定义网关的默认图像模型（未知服务商时的兜底）
GENERIC_IMAGE_MODEL = os.getenv("AGENT_IMAGE_MODEL", "")
# 出图服务商与文本模型**解耦**：外观图 / 渲染图 / 爆炸图固定走 Agnes，
# 用户在前端换了 DeepSeek / SiliconFlow 当文本模型也不影响出图这条线。
IMAGE_PROVIDER = os.getenv("AGENT_IMAGE_PROVIDER", "agnes").strip().lower()
# 让网关直接返回 base64，不再去 CDN 二次下载。
# 实测 CDN 直链在本机链路上会被 TLS 拦截 / 连接重置搞挂（图生成好了却落不了盘），
# 而 Agnes 支持 response_format=b64_json —— 拿得到就绕开整条下载链路。
# 设 AGENT_IMAGE_B64=0 可退回「下载直链」模式。
IMAGE_B64 = os.getenv("AGENT_IMAGE_B64", "1").strip().lower() not in ("0", "false", "no", "off")

# ---------------------------------------------------------------- CAD 运行时
# 三选一：e2b（云端沙箱）/ local（本机 venv）/ cadgen（uvx 拉的官方运行时）。
# auto = 优先 e2b。理由实测：本机 8GB 内存，并发建模时 OpenBLAS 分配失败直接把子进程
# 打崩（退出码 1 且 stdout/stderr 全空），沙箱是独立内存，且冷启更快（建模 2.5s）。
E2B_API_KEY = os.getenv("E2B_API_KEY", "").strip()
# 沙箱保活时长（秒）：一个会话内多次建模复用同一个沙箱，省掉重复 pip install。
E2B_TIMEOUT = int(os.getenv("AGENT_E2B_TIMEOUT", "1800"))
# 自定义模板 id（预装 build123d 可再省 10s）；留空用官方默认镜像 + 首次 pip install。
E2B_TEMPLATE = os.getenv("AGENT_E2B_TEMPLATE", "").strip()
# 单次建模脚本在沙箱里的执行上限
E2B_RUN_TIMEOUT = int(os.getenv("AGENT_E2B_RUN_TIMEOUT", "300"))
CAD_RUNTIME = os.getenv("AGENT_CAD_RUNTIME", "auto").strip().lower()

_IMAGE_HINTS: list[tuple[str, str]] = [
    ("siliconflow", "siliconflow"),
    ("dashscope", "dashscope"),
    ("aliyuncs", "dashscope"),
    ("openai", "openai"),
    ("agnes", "agnes"),
]


def image_provider_of(base_url: str) -> str:
    b = (base_url or "").lower()
    for needle, key in _IMAGE_HINTS:
        if needle in b:
            return key
    return ""


def resolve_image_provider() -> tuple[str, str, str]:
    """出图用的是哪一家（端点, 模型, 指纹名）。与文本模型的网关无关。

    拿不到 IMAGE_PROVIDER 对应服务商的 key 时退到 any_image_key()，
    保证「换文本模型 ≠ 图就没了」。
    """
    key = IMAGE_PROVIDER if IMAGE_PROVIDER in IMAGE_PROVIDERS else "agnes"
    p = IMAGE_PROVIDERS[key]
    if not fallback_key(p["base_url"]):
        base, k = any_image_key()
        if base:
            return base, (GENERIC_IMAGE_MODEL or p["model"]), image_provider_of(base) or key
    return p["base_url"], p["model"], key


def resolve_image(base_url: str = "", model: str = "") -> tuple[str, str]:
    """按 base_url 判定服务商，返回 (图像端点, 模型)。

    认不出来的网关（自建 / Ollama / vLLM）就原样当端点用 —— 这些服务通常把
    /images/generations 也挂在同一 base_url 下，硬套预设反而会打错地方。
    """
    b = (base_url or "").strip()
    key = image_provider_of(b)
    if key:
        p = IMAGE_PROVIDERS[key]
        return p["base_url"], (model or p["model"])
    if b:
        return b, (model or GENERIC_IMAGE_MODEL or "flux")
    p = IMAGE_PROVIDERS["agnes"]
    return p["base_url"], (model or p["model"])


def mask(key: str) -> str:
    """密钥指纹：前 8 + 后 4。够定位「到底用的哪把 key」，又不泄露完整凭据。"""
    k = (key or "").strip()
    if not k:
        return "(未配置)"
    if len(k) <= 12:
        return k[:4] + "***"
    return f"{k[:8]}...{k[-4:]}"


# ---------------------------------------------------------------- 深度思考
# AGENT_THINKING=1 时给模型请求体附加 thinking 开关；具体参数因模型而异，
# 用 AGENT_THINKING_BODY 覆盖（JSON），例如：
#   Qwen3 / Qwen3.5 系列  -> {"enable_thinking": true}
#   deepseek-ai/DeepSeek-R1（原生就吐思维链，无需参数）
THINKING = os.getenv("AGENT_THINKING", "1") == "1"
THINKING_BODY: dict[str, Any] = {"enable_thinking": True}
_raw_body = os.getenv("AGENT_THINKING_BODY", "").strip()
if _raw_body:
    try:
        parsed = json.loads(_raw_body)
        if isinstance(parsed, dict):
            THINKING_BODY = parsed
    except json.JSONDecodeError:
        pass
if not THINKING:
    THINKING_BODY = {}

WORKSPACE = Path(os.getenv("AGENT_WORKSPACE", str(ROOT / "workspace")))
KNOWLEDGE_DIR = Path(os.getenv("AGENT_KNOWLEDGE", str(ROOT / "knowledge" / "raw")))
SKILLS_DIR = ROOT / "agent" / "skills"

PORT = int(os.getenv("AGENT_PORT", "8001"))
MOCK = os.getenv("AGENT_MOCK", "0") == "1"

MAX_TOOL_RESULT_CHARS = 4000

# ---------------------------------------------------------------- 运行护栏
# 同一个需求跑两次，事件数 8 万 vs 34 万、工具 72 次 vs 239 次、产物 10 个 vs 31 个 ——
# 根因不是随机，是「没有预算的自主循环」：模型可以连续思考十几分钟不落一个产物，
# 也能把同一个工具用同样的参数反复调上百次。下面这几个上限把不确定性收进可预期区间。
MAX_RUN_SECONDS = float(os.getenv("AGENT_MAX_SECONDS", "900"))  # 单次运行总时限（秒）
TOOL_BUDGET = int(os.getenv("AGENT_TOOL_BUDGET", "80"))         # 单次运行工具调用总上限
REPEAT_LIMIT = int(os.getenv("AGENT_REPEAT_LIMIT", "5"))        # 同一(工具,参数)重复调用上限

# ---------------------------------------------------------------- 超时续期（保证任务跑完）
# 档位时限原本是**硬墙**：到点就中止，哪怕模型正一张接一张出图、一个接一个落产物。
# 实测一次「六阶段」运行在 904 秒被砍掉，产物明明还在稳定增加 —— 中止的理由是
# 「时间到了」，而不是「卡住了」。时间本身不是失败信号，**停滞**才是。
# 所以改成两层：
#   1) 软续期：到点但**还在进展**（距上次实质进展 < STALL_SECONDS）就自动延长，
#      每次 EXTEND 秒，一直延到硬上限；并在接近时限时提前预警。
#   2) 硬上限 + 自动续跑：真到硬上限还未完成，按「阶段存盘点」自动再开一轮，
#      复用同一个工作区目录，从下一个未完成阶段接着做（最多 AUTO_RESUME_MAX 轮）。
TIMEOUT_EXTEND_SECONDS = float(os.getenv("AGENT_TIMEOUT_EXTEND", "600"))
# 0 = 按「档位时限 × 2」推导（standard 900 → 1800s）
RUN_HARD_SECONDS = float(os.getenv("AGENT_RUN_HARD_SECONDS", "0"))
TIMEOUT_WARN_RATIO = float(os.getenv("AGENT_TIMEOUT_WARN_RATIO", "0.8"))  # 用掉 80% 时限时预警
AUTO_RESUME_MAX = int(os.getenv("AGENT_AUTO_RESUME_MAX", "2"))

# ---------------------------------------------------------------- 人工确认闸门（human-in-the-loop）
# 设计不是「一条命令跑到底」：外观图出来长什么样、结构能不能装，只有人看了才算数。
# 这两个阶段做完必须**停下来等人**：确认就放行下游，有意见就带着反馈重做本阶段。
# 闸门是硬门禁 —— 下游阶段的 stage_gate 会检查确认记录，没确认就不许开工，
# 不能指望模型「记得先问一句」。
HUMAN_GATE = os.getenv("AGENT_HUMAN_GATE", "1").strip().lower() not in ("0", "false", "no", "off")
HUMAN_GATE_STAGES = tuple(
    s.strip()
    for s in os.getenv("AGENT_HUMAN_GATE_STAGES", "10_appearance,20_structure").split(",")
    if s.strip()
)
# 等人最久多久。超时按「自动放行」处理并写进记录 —— 让人干等会卡住整轮，
# 但也不能因为浏览器关了就把已做完的阶段丢掉。
HUMAN_WAIT_SECONDS = float(os.getenv("AGENT_HUMAN_WAIT", "1800"))
# 等待期间进度提示的间隔（秒）
HUMAN_BEAT_SECONDS = float(os.getenv("AGENT_HUMAN_BEAT", "60"))
# 确认记录的文件名（写在阶段目录里，重启/续跑后仍查得到）
HUMAN_REVIEW_FILE = "_human_review.md"

# ---------------------------------------------------------------- 重复调用的判据细化
# read_file 这种内置只读工具被判成「无效循环」是典型的误杀：它幂等、无副作用，
# 子智能体各自读一遍 AGENTS.md / 上一步产物本来就是正常协作。所以阈值按成本分三档。
READ_ONLY_TOOLS = frozenset(
    t.strip()
    for t in os.getenv(
        "AGENT_READ_ONLY_TOOLS", "read_file,ls,glob,grep,read,search_cirkit_docs"
    ).split(",")
    if t.strip()
)
REPEAT_LIMIT_READ = int(os.getenv("AGENT_REPEAT_LIMIT_READ", "24"))   # 只读工具
# 同一个文件被反复读（不同 offset/limit 算同一个文件）：这基本只有「想不起来自己写过什么」
# 一种解释。限额压得比普通只读更低，否则模型能把一次运行耗在反复回读自己刚写的报告上。
REPEAT_LIMIT_SAMEFILE = int(os.getenv("AGENT_REPEAT_LIMIT_SAMEFILE", "10"))
# args 抓不到时签名退化成「只有工具名」，此时证据不足，阈值要更保守
REPEAT_LIMIT_NOARG = int(os.getenv("AGENT_REPEAT_LIMIT_NOARG", "15"))
# 「停滞」判据：距上一次实质进展（新产物 / 写类工具成功）超过这么久，重复才算空转。
# 关键：**只读工具返回内容不算进展** —— 读文件不改变世界，把它的返回值当「还在往前走」，
# 就等于给「反复回读」发了免死金牌（实测评审子智能体连读 37 次自己的报告不肯收尾）。
STALL_SECONDS = float(os.getenv("AGENT_STALL_SECONDS", "120"))
# 预算按「代价」加权：只读廉价调用只算零点几次，昂贵调用（渲染/CAD/出图）才算 1 次
TOOL_WEIGHT_READ = float(os.getenv("AGENT_TOOL_WEIGHT_READ", "0.25"))
# 自定义工具里廉价的那几个：stage_gate 是纯磁盘判据（子智能体各调一次本就正常），
# 按 1.0 计费的话，光「开工前查门禁」这一件事就能吃掉六阶段预算的十分之一。
# 注意它**不进** READ_ONLY_TOOLS —— 那边还管着「算不算进展」，门禁查询不该算进展。
CHEAP_TOOLS = frozenset(
    t.strip() for t in os.getenv("AGENT_CHEAP_TOOLS", "stage_gate").split(",") if t.strip()
)
# 同一个 stage_gate 结果在这段时间内直接复用缓存：子智能体开工前查、主智能体核对时
# 又查，内容一模一样，重复算纯属浪费（实测一次运行里 stage_gate 被调了十几次）。
STAGE_GATE_CACHE_SECONDS = float(os.getenv("AGENT_STAGE_GATE_CACHE", "20"))
# tool_start 的 args 是流式逐块拼出来的，首发时常常是空的。等这么久再发卡片：
# 既保住前端卡片有参数，也让护栏拿到可用的去重签名。
ARGS_WAIT_SECONDS = float(os.getenv("AGENT_ARGS_WAIT", "0.8"))
# ---------------------------------------------------------------- 网关限流（429）
# 免费额度按时间窗口限流，langchain 自带的 max_retries 退避太短（秒级）救不回来，
# 一次抖动就把十几分钟的整轮判死。这里在编排层补一轮「长退避重跑」：
# 已落盘的产物会保留，重跑只是接着往下做，不是从头瞎跑。
RATE_LIMIT_ATTEMPTS = int(os.getenv("AGENT_RATE_LIMIT_ATTEMPTS", "4"))
RATE_LIMIT_BACKOFF = float(os.getenv("AGENT_RATE_LIMIT_BACKOFF", "25"))  # 25 / 50 / 75s
# 单次退避上限：限流窗口可能是分钟级，但不能让用户无限等
RATE_LIMIT_MAX_WAIT = float(os.getenv("AGENT_RATE_LIMIT_MAX_WAIT", "90"))

# ---------------------------------------------------------------- 配额守卫（主动限速）
# 免费额度是**滑动窗口内的 token 总量**，不是请求次数（2026-10-13 实测：
# 小请求连打 5 次全 200，单轮 16k 字符立刻 429）。与其撞了再退避 25~75 秒，
# 不如在请求前估一下、快超了就先等窗口滑过去。
QUOTA_WINDOW_SECONDS = float(os.getenv("AGENT_QUOTA_WINDOW", "60"))
# 窗口预算。免费额度没公开，按实测反推：完整 run 约 1040s 跑 60 轮左右，
# 折合 3~4 万 token/分钟。取 40000 —— 太紧会让每个请求前都白等半分钟，
# 太松就起不到预防作用。撞过 429 后会自动惩罚，等于动态收紧。
QUOTA_WINDOW_TOKENS = int(os.getenv("AGENT_QUOTA_WINDOW_TOKENS", "40000"))
# 撞到 429 后的惩罚静默期：比网关自己的窗口略长，避免刚恢复又打满
QUOTA_PENALTY_SECONDS = float(os.getenv("AGENT_QUOTA_PENALTY", "20"))
# 单次主动等待上限：宁可撞一次 429 也不能让界面看起来卡死
QUOTA_MAX_WAIT = float(os.getenv("AGENT_QUOTA_MAX_WAIT", "20"))
# 总开关：0 = 关闭主动限速（退回「撞了再退避」的老行为）
QUOTA_GUARD = os.getenv("AGENT_QUOTA_GUARD", "1").strip().lower() not in ("0", "false", "no")

# ---------------------------------------------------------------- 网关抖动（连接层）
# 实测（2026-10-11 本机，agnes 网关）：DNS/TCP/TLS/GET /models 全部 1~2s 正常，
# 但 /chat/completions 单次简单请求要 11~13s、流式首字节 17s，偶发超过 60s 直接断开
# → 表现为 langchain 的 OpenAIConnectionError / ReadTimeout，看起来像「网络挂了」，
#   其实是网关排队。**一个 300s 的读超时会让它卡死五分钟才报错**，护栏还会先判定停滞。
# 所以拆开 connect / read：连接给短超时（连不上就是连不上），读取给中等超时，
# 快速失败 → 快速重试 → 连续失败就换模型，比傻等一次强。
LLM_CONNECT_TIMEOUT = float(os.getenv("AGENT_LLM_CONNECT_TIMEOUT", "25"))
LLM_READ_TIMEOUT = float(os.getenv("AGENT_LLM_READ_TIMEOUT", "120"))
# 两层重试会相乘：langchain 层 4 次 × 编排层 4 次 = 最多 16 次请求。
# 网关一拥塞，这 16 次全是空转（每次还要等满连接超时），用户看到的就是「慢得离谱」。
# 编排层已经负责整体重跑，SDK 层留 1 次足够挡住单次抖动，多了纯属放大。
LLM_MAX_RETRIES = int(os.getenv("AGENT_LLM_MAX_RETRIES", "1"))   # langchain 层（SDK 内建退避）
NET_RETRY_MAX = int(os.getenv("AGENT_NET_RETRY_MAX", "3"))       # 编排层整体重跑次数
NET_RETRY_BACKOFF = float(os.getenv("AGENT_NET_RETRY_BACKOFF", "8"))  # 8 / 16 / 24s
# 连续这么多次网络类失败后，把模型切到 AGENT_FALLBACK_MODEL 再试。
# 网关常常是「某一个模型排队，另一个空闲」（实测 flash 12.8s / pro-alpha 1.4s），
# 换模型比原地重试有效得多 —— 所以阈值取 1：第一次失败就换，别在同一条队里排两次。
# 留空 = 不切换。
NET_FALLBACK_AFTER = int(os.getenv("AGENT_NET_FALLBACK_AFTER", "1"))
# 连续这么多次网络失败后，先探活网关再决定要不要继续重跑。
# 网关整体不可用时，剩下的退避与重跑全是白等 —— 直接报「网关不可达」让用户知道该等还是该换网关。
GATEWAY_DEAD_AFTER = int(os.getenv("AGENT_GATEWAY_DEAD_AFTER", "2"))
# 探活不通**不等于判死**：网关的短暂拥塞（几十秒到几分钟）远比「真的下线」常见，
# 一击判死会把「等一分钟就能自愈」的抖动变成用户可见的失败弹窗。
# 所以先进入等待-恢复窗口：这段时间里周期性探活，通了就自动续跑，真等到上限才报错。
GATEWAY_WAIT_MAX = float(os.getenv("AGENT_GATEWAY_WAIT_MAX", "600"))     # 等待恢复的总上限（秒）
GATEWAY_PROBE_INTERVAL = float(os.getenv("AGENT_GATEWAY_PROBE_INTERVAL", "15"))  # 两次探活的间隔（秒）

# ---------------------------------------------------------------- 开跑前的模型选路
# 同一个网关下不同模型的排队深度差一个数量级，而且**随时间变化**（昨天快的今天可能堵）。
# 写死模型名没意义，开跑前花几十秒并发探一次，后面二三十轮往返全走快的那个。
MODEL_RACE = os.getenv("AGENT_MODEL_RACE", "1").strip().lower() not in ("0", "false", "no", "off")
# 探测超时要能盖住「排队但能回」的情况：实测 pro-alpha 41s 才出首字节，
# 给 30s 会把它判死、只剩下真正堵住的那个模型可选。
MODEL_RACE_TIMEOUT = float(os.getenv("AGENT_MODEL_RACE_TIMEOUT", "60"))
# 候选模型：默认用「当前模型 + 备用模型」，可自行加（逗号分隔，必须是同一网关可用的名字）
MODEL_RACE_CANDIDATES: list[str] = [
    c.strip()
    for c in os.getenv("AGENT_MODEL_RACE_CANDIDATES", "").split(",")
    if c.strip()
]

# 被护栏中止时，已完成多少阶段就按「目标达成」而不是「失败」定性。
# 用户看到的差别很大：产物都在却显示「超出预算中止」，等于把一次交付说成事故。
GOAL_MIN_STAGES = int(os.getenv("AGENT_GOAL_MIN_STAGES", "4"))
# 写盘类工具：这些调用的成功结果才算「实质进展」。
# 判据的来源很关键 —— save_artifact 只是其中一种，子智能体更常用 write_file / edit_file
# 直接落盘，只认前者会让「六阶段产物齐全」的判据永远凑不齐。
WRITE_TOOLS = frozenset(
    t.strip()
    for t in os.getenv(
        "AGENT_WRITE_TOOLS",
        "save_artifact,write_file,edit_file,generate_product_image,text_to_cad,"
        "render_product_views,write_todos",
    ).split(",")
    if t.strip()
)
# 磁盘是产物的唯一权威：节流扫描 run 目录（秒），把不走 save_artifact 的落盘也算进目标判据
DISK_SCAN_SECONDS = float(os.getenv("AGENT_DISK_SCAN", "5"))
# 流式事件（thinking/token）聚合窗口：SSE 仍逐条推（保住打字机），只有落库被合并
AGG_SECONDS = float(os.getenv("AGENT_AGG_SECONDS", "0.5"))
AGG_CHARS = int(os.getenv("AGENT_AGG_CHARS", "1500"))
# 六阶段产物齐全后给模型多久做收尾汇总。判据靠磁盘产物而不是模型自觉回写 ——
# 模型常常「该收尾了」和「还想再改一版」之间反复横跳，宽限期一到就强制交付。
GOAL_GRACE_SECONDS = float(os.getenv("AGENT_GOAL_GRACE", "150"))

# ---------------------------------------------------------------- 决策范式
# AGENT_PARADIGM 留空 = 自动路由（按任务 DNA 三基因选型）；
# 填 lean / standard / review / explore 则强制指定档位（调试或用户明确要求时用）。
PARADIGM = os.getenv("AGENT_PARADIGM", "").strip().lower()
# 显式设置了 AGENT_MAX_SECONDS / AGENT_TOOL_BUDGET 时，它们作为**绝对硬上限**压过范式档位
_HARD_S = os.getenv("AGENT_MAX_SECONDS", "").strip()
HARD_MAX_SECONDS = float(_HARD_S) if _HARD_S else 0.0
_HARD_B = os.getenv("AGENT_TOOL_BUDGET", "").strip()
HARD_TOOL_BUDGET = int(_HARD_B) if _HARD_B else 0

# SSE 心跳：静默超过这个秒数就推一条 heartbeat（不落库、不占事件列表），
# 让前端能区分「还在跑」和「卡死了」—— 后端假死是最容易被误判成「还在运行中」的故障。
HEARTBEAT_SECONDS = float(os.getenv("AGENT_HEARTBEAT", "2.0"))
# 取消看门狗：护栏只能在「下一个事件到达时」中断流，而智能体可能正堵在模型的 HTTP
# 长连接上（几十秒不推一个事件）。取消后超过这个秒数还没停下来，就先给前端收口，
# 后台线程跑完仍会正常落盘 —— 宁可让 UI 先结束等待，也不能让它一直转圈。
CANCEL_GRACE_SECONDS = float(os.getenv("AGENT_CANCEL_GRACE", "20"))
# 反思规则库超过这个条数就自动聚类压缩（规则膨胀会拖慢检索，也会互相矛盾）
REFLEXION_CLUSTER_AT = int(os.getenv("AGENT_REFLEXION_CLUSTER", "60"))

# ---------------------------------------------------------------- 出网（代理 / 证书）
# 公司网、Clash/Mihomo TUN、某些校园网会对特定域名做 HTTPS 中间人，
# Python 的 certifi 不认那张根证书 → 表现为「网络中断」，其实是被拦。
# 排障顺序：先看 /health 的 image.host，再 curl 同域名，最后才动这两个开关。
VERIFY_SSL = os.getenv("AGENT_VERIFY_SSL", "1").strip().lower() not in ("0", "false", "no", "off")
CA_BUNDLE = os.getenv("AGENT_CA_BUNDLE", "").strip()
HTTP_TIMEOUT = float(os.getenv("AGENT_HTTP_TIMEOUT", "300"))


def ssl_verify() -> bool | str:
    """给 httpx / requests 用的 verify 参数。

    - 默认 True（certifi）
    - 给了 AGENT_CA_BUNDLE 就用那个 pem（自签根证书导出来丢进去，最安全的解法）
    - AGENT_VERIFY_SSL=0 才彻底关校验（会被中间人，仅限本地排障）
    """
    if CA_BUNDLE:
        return CA_BUNDLE
    return VERIFY_SSL


def require_key() -> str:
    if not SILICONFLOW_API_KEY:
        raise RuntimeError("缺少 SILICONFLOW_API_KEY，请在 .env 中配置")
    return SILICONFLOW_API_KEY


def fallback_key(base_url: str) -> str:
    """前端没传 key 时，按网关地址去 .env 里找对应的那把。

    这样用户打开页面什么都不填也能用（前提是 .env 里配了至少一把）。
    """
    key = image_provider_of(base_url)
    if key == "agnes":
        return AGNES_API_KEY
    if key == "siliconflow":
        return SILICONFLOW_API_KEY
    if key == "dashscope":
        return DASHSCOPE_API_KEY
    if key == "openai":
        return OPENAI_API_KEY
    return AGNES_API_KEY or SILICONFLOW_API_KEY or DASHSCOPE_API_KEY or OPENAI_API_KEY


def any_image_key() -> tuple[str, str]:
    """图像网关没单独配时，挑一把 .env 里有的（端点 + 密钥同源）。

    返回 ("", "") 表示一把都没有 —— 调用方应直接走离线兜底，别浪费时间重试。
    """
    for key in ("siliconflow", "dashscope", "agnes", "openai"):
        p = IMAGE_PROVIDERS[key]
        k = {"siliconflow": SILICONFLOW_API_KEY, "dashscope": DASHSCOPE_API_KEY,
             "agnes": AGNES_API_KEY, "openai": OPENAI_API_KEY}[key]
        if k:
            return p["base_url"], k
    return "", ""




def server_default() -> dict[str, str]:
    """服务端预置的模型配置（不含密钥），供前端首次打开时对齐。"""
    return {
        "provider": DEFAULT_PROVIDER,
        "base_url": DEFAULT_BASE_URL,
        "model": MODEL,
        "has_key": "1" if (AGNES_API_KEY or SILICONFLOW_API_KEY) else "0",
    }
