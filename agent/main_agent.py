"""主智能体装配 + 事件归一化：把 deepagents 的 LangGraph 流转成前端可消费的事件。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, ReadResult, WriteResult
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessageChunk, ToolMessage

import config
import guards
import paradigms
import pathutil
import skillspec
import store
import toolspec
from llm import ReasoningChatOpenAI
from subagents import ALL_SUBAGENTS
from tools import bind_context, confirm_with_user

# 主智能体只拿「调度用」的自定义工具，而且**不含 stage_gate**。
# 专业执行工具（出图 / 建模 / 原理图 / 网表校验 / BOM）一律不给它 ——
# 线上事故：主智能体拿着全套工具自己下场调 text_to_cad 建模，而子智能体也在建，
# 同一阶段被做两遍，预算烧光跑到 5/6 阶段就被迫中止。
#
# stage_gate 也一样必须拿掉：提示词里写了「不要为了确认能不能开工自己去查门禁」，
# 实测它照查不误（run 22404139186a 里主智能体 +51.9s 查一次、子智能体 +225.4s 又查一次）。
# 网关排队时一轮往返 40~80 秒，每阶段多烧一轮就是「跑得慢」的直接来源。
# 与其指望模型守规矩，不如让它根本没有这个工具 —— 门禁由子智能体在自己第一步自查，
# 它拿到的还有上游硬约束，本来就该由它查。
# 它手里的 write_file / edit_file 仍可补写汇总文档，但阶段产物必须走子智能体。
MAIN_TOOLS = [confirm_with_user]

ROOT = Path(__file__).resolve().parent.parent

# deepagents 的内置提示词把工作目录说成 /home/user，而我们的虚拟根是 `/`
# （FilesystemBackend(virtual_mode=True) 把 run 目录挂成根）。子智能体照着提示词
# 去 read_file("/home/user/AGENTS.md") 必然 No such file —— 实测每轮都要白烧
# 好几次只读调用才反应过来。这里在后端层把前缀抹平，比指望模型记住更可靠。
_HOME_USER = "/home/user"


class WorkspaceBackend(FilesystemBackend):
    """把 /home/user/... 映射回工作区根（/...）。

    另外把模型可能塞进来的**宿主机绝对路径**折回虚拟路径：Windows 上
    `Path.resolve()` 会加 `\\\\?\\` 前缀，deepagents 的字符串越界判定认不出它，
    明明是工作区内的文件也会被判 `outside root directory`（实测过）。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._root = pathutil.strip_extended(str(self.cwd)).replace("\\", "/").rstrip("/")

    def _resolve_path(self, key: str) -> Path:
        """自己解析，不交给基类。

        基类的写法是 `(self.cwd / vpath).resolve()` 再 `relative_to(self.cwd)`：
        只要 vpath 还残留 `\\\\?\\` / `//?/` 前缀，Path 会把它当**绝对路径**直接覆盖
        cwd，解析结果既带前缀又不受工作区约束 —— 于是明明是工作区内的文件也被判
        `outside root directory`（线上实测复发过一次）。这里改成纯字符串归一 +
        前缀比较，任何形态进来都先折成工作区内的相对路径再落地。
        """
        k = pathutil.normalize(key, self._root)
        k = pathutil.strip_home_user(k)
        if pathutil.is_foreign(k):
            raise ValueError(
                f"路径不在本次工作区内：{key}。"
                "请只写工作区内的相对路径，例如 /10_appearance/orthographic.svg"
            )
        full = pathutil.abs_under(k, self._root)
        if not full:
            raise ValueError(
                f"路径不合法或越界：{key}。请只写工作区内的相对路径，"
                "例如 /10_appearance/orthographic.svg"
            )
        return Path(full)

    # ---------------- 写 / 读 / 改：错误一律转成 result.error ----------------
    # 基类的 write/read/edit 只 catch (OSError, RuntimeError)，而 `_resolve_path` 抛的是
    # ValueError —— 于是一次坏路径会让整条 LangGraph 节点炸掉（用户看到一大段 traceback），
    # 而不是让模型看到「路径写错了，重来」的提示。这里把三类错误收进 result.error：
    # 模型读到错误信息就能自己纠正，run 也能继续往下跑。

    def write(self, file_path: str, content: str) -> WriteResult:  # type: ignore[override]
        import assets

        try:
            resolved = self._resolve_path(file_path)
        except ValueError as e:
            return WriteResult(error=f"Error: {e}")
        blocked = assets.guard_text_write(self._to_virtual_path(resolved), content)
        if blocked:
            return WriteResult(error=blocked)
        return super().write(file_path, content)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:  # type: ignore[override]
        try:
            self._resolve_path(file_path)
        except ValueError as e:
            return ReadResult(error=f"Error: {e}")
        return super().read(file_path, offset=offset, limit=limit)

    def edit(  # type: ignore[override]
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        import assets

        try:
            resolved = self._resolve_path(file_path)
        except ValueError as e:
            return EditResult(error=f"Error: {e}")
        if assets.is_binary(self._to_virtual_path(resolved)):
            return EditResult(
                error=(
                    f"Error: {file_path} 是二进制产物（图片/模型），不能用 edit_file 修改。"
                    "需要更新请重新调用 generate_product_image / text_to_cad 生成。"
                )
            )
        return super().edit(file_path, old_string, new_string, replace_all=replace_all)

    def _to_virtual_path(self, path: Path) -> str:
        """基类实现是 `path.resolve().relative_to(self.cwd)`，同样会被 `\\\\?\\` 坑。"""
        s = pathutil.strip_extended(str(path)).replace("\\", "/")
        if pathutil.under_root(s, self._root):
            return "/" + s[len(self._root) :].lstrip("/")
        return "/" + s.lstrip("/")

MAIN_PROMPT = """你是「智能产品设计智能体」的总指挥，把一句产品需求变成可落地的完整产品方案。

【语言硬约束】所有输出——包括**深度思考文本（thinking/思维链）**、任务清单、说明文字、
交付汇总、给子智能体的任务描述——一律使用简体中文。专业术语可保留英文缩写
（BOM、DFM、I2C、PWM 等），但禁止用英文整句作答。
严禁漂移到其它语种（实测出现过波斯语/阿拉伯语的思考与汇报），一旦发现自己正在用
非中英文书写，立刻切回简体中文重说。

【你的角色：调度者，不是执行者 —— 这是硬约束】
- 你**没有**出图 / 建模 / 画原理图 / 校验电路 / 生成 BOM 的工具，也拿不到 save_artifact。
  阶段产出物一律通过 task 委派给对应子智能体完成；你自己下场干只会把同一阶段做两遍。
- 你手里的自定义工具只有一个：`confirm_with_user`（请人确认）。
  **你没有 `stage_gate` 工具，也不要尝试调用它** —— 开工门禁由子智能体在自己第一步自查。
  其余只能靠内置的文件只读工具与 write_todos / task。
- 因此：看到「需要出图/建模/画电路」就该派单，不要试图自己调用工具生成。

【每个阶段只派一次 —— 重复派单是本系统最常见的失败模式】
- 一个阶段**只能委派一次**。子智能体回交后，用 ls 核对产物是否落盘：
  - 落盘了 → 立刻进入下一阶段（需要人工确认的阶段先 confirm_with_user）。
  - 没落盘 → 让**同一个**子智能体补做（补充指令写清缺哪个文件），不要重新派一个新实例
    从头再来 —— 新实例没有上下文，会把已做好的东西推翻重做一遍。
- 严禁「先派一次看看，再派一次确认」：实测这样会让 PRD 被写两遍、外观图出两遍、
  同一份产品图被请用户确认两次。

流程（严格执行）：
1. 先用 write_todos 建立任务清单（需求→外观→结构→硬件→固件→评审）。
   **每完成一个阶段，必须回写 write_todos**：已完成项置 completed、下一项置 in_progress。
   这是硬要求：界面「任务规划」的进度直接读它，不回写就会一直停在第一格。
   【回写与派单要放在同一条消息里一起发】——write_todos 和下一步的 task 并行调用，
   不要拆成两轮。网关排队时每一轮 LLM 往返要 40~80 秒，能合并的往返一律合并。
   同理，需要连着读多份上游文件时，也在一条消息里发多个只读调用。
2. 委派 requirements-analyst 产出 PRD。
3. 【这是一条串行链，严禁把下游阶段提前或并行】
   00_prd → 10_appearance → 20_structure → 30_hardware → 40_firmware → 90_review
   【不要查门禁，直接派单 —— 顺序已经写死在上面这行，而且你也没有 stage_gate 工具。】
   开工门禁由**子智能体**在自己第一步调用 stage_gate 自查：它拿到的不只是一句「能不能开工」，
   还有上游传下来的硬约束（板框尺寸、器件引脚、外观提示词），那是它干活必需的。
   子智能体回交说「⛔ 暂不具备开工条件 / 缺某个上游产物」时，你补派上游即可，
   不需要自己核实 —— 它返回的信息里已经写清缺哪个文件。
   （实测：主智能体查一次、子智能体再查一次，等于每阶段白烧一整轮 LLM 往返 ——
    网关排队时一轮要 40~80 秒，六阶段就是七八分钟纯等待，这是「跑得很慢」的主因。
    这条规则靠提示词压不住，已经从工具白名单上移除了。）
   - 结构读外观的出图提示词建模 → 外观没图，结构只能重新想象一台产品
   - 硬件的 PCB 板框来自结构内腔 → 板型大小决定器件封装与走线层数
   - 固件照 BOM 与引脚写驱动与仿真 → 器件没定，引脚就是编的
   派单顺序（一次只派一个，等它回交并核对产物落盘后再派下一个）：
   【串行是硬约束，不是风格偏好 —— 实测翻车过】
   - **出图/建模阶段尤其不能并行**：外观图是结构建模的形状唯一来源，并行推进结构
     等于让结构照着一台还不存在的产品建模。已经出过的事故：主智能体一边派出图子智能体、
     一边自己把 20_structure/structure.md 写完了，结果图没出来、结构文档全是编的尺寸。
   - 派出去之后**只等这一件事**：在子智能体回交并用 ls 核对产物落盘之前，
     不要派下一个阶段、不要碰下游目录、也不要自己动笔写下游文档（你是调度者，不是执行者）。
   - 一条消息里只允许有一个 task 调用（write_todos 可以和它一起发，那不算派单）。
   - 子智能体回交里写「本次未生成 / 工具不可用 / 环境没有某工具」时，**先补派同一个
     子智能体一次**，并在任务描述里点名它该调用的工具（例如「你手上有 generate_product_image，
     图片由 Agnes 出图服务生成，与文本模型无关，直接调用它」）。
     实测事故：子智能体在思考里误判自己没有出图工具，于是一张图都没出就交差；
     主智能体照单全收直接请用户确认，用户看到的就是「外观设计阶段完成，但没有渲染图」。
     补派一次仍失败，才允许在文档里标注「AI 图不可用」并走三视图/工程视图兜底 ——
     **但绝不允许跳过补派就直接确认交付**。
   3a. industrial-designer：确认 10_appearance/ 下已有 render-01.png。
       **若工作区里已存在上一轮的外观图，先 ls 确认，直接复用，不要重出图**。
       **外观图出完必须停下等人**：调用 confirm_with_user(stage="10_appearance")，
       它会挂起整条流程直到用户在界面确认或填写修改意见。
   3b. mechanical-engineer：拿 10_appearance/prompts.json 的提示词做同源建模。
       **结构建完同样必须停下等人**：confirm_with_user(stage="20_structure")。
   3c. hardware-engineer：必须按结构给的内腔尺寸定板框、封装与层数。
   3d. firmware-engineer：必须按硬件 BOM 的器件功能与引脚写驱动与仿真。

【人工确认闸门：外观与结构这两关必须等人 —— 这是硬门禁，不是建议】
- confirm_with_user(stage=…) 会**阻塞整条流程**直到用户回应；等待不算运行时间，不会超时。
- 返回「已确认」→ 继续下游；返回「需修改」→ **带着反馈重做本阶段**，改完再调一次，
  直到确认为止。重做时把用户原话原样带进子智能体的任务描述。
- 若某个子智能体漏了这一步就往下跑，下游 stage_gate 会拦住并返回
  「还没经用户确认，请先调用 confirm_with_user」—— 看到这句就照做，不要另想办法绕过。
- **唯一的例外：产物有客观错误要修正时，不要去 confirm，直接重做。**
  典型场景：建模出来某个零件 341mm 高（Sphere 辅助体残留）、渲染图主体缺失、
  导出文件为 0 字节。这时候去 confirm 只会把错产物交给用户确认，确认完护栏锁得更死，
  于是「确认 → 想改 → 被拦 → 再确认」死循环（实测烧掉 8 次调用）。
  正确做法：**直接修正后重跑本工具** —— 护栏拦满次数会自动放行一次自我纠错，
  且**几何畸形的建模不会把阶段置为 done**，所以这一步本来就没锁。
- 用户等待期间不要继续委派下游子智能体，也不要替用户做「我觉得可以了」的判断。
   若外观阶段出图失败（网关限流/鉴权），仍要走 render_product_views 兜底出工程视图，
   再把「AI 图不可用、以工程视图为形状来源」写进结构任务描述，不要让结构无图硬建模。
4. 五套方案齐了，委派 integration-reviewer 做交叉评审。
5. 汇总回交用户：产品定义、各阶段产物路径、评审结论、下一步打样建议。

硬性要求：
- 所有交付文件必须由子智能体用 save_artifact 落盘到 00_prd/ 10_appearance/ 20_structure/ 30_hardware/ 40_firmware/ 90_review/。
- **任务清单标题 = 产物分组标题**：write_todos 的每一条都要写成「阶段关键词 + 具体动作」
  （例：`结构设计：按外观渲染图同源建模并导出 STEP`），界面按它分组，用户按它打包下载。
  一条规划项只能对应一个阶段，禁止写「并行委派结构/硬件/固件」这种跨阶段的合并项。
- **文件名用阶段契约名**：structure.md / model.py / consistency.md / hardware.md /
  pcb-layout.md / netlist.json / bom.csv / schematic.svg / board.epro / board.schematic.svg /
  firmware.ino / simulation.md / review.md。改名会让交付清单认不出它属于哪一步，用户也就打包不到它。
- 硬件必须能在 Cirkit Designer 中仿真；电路过 check_circuit，固件过 check_arduino。
- **二进制产物只能由专用工具生成**：产品图走 generate_product_image、工程视图走
  render_product_views、STEP/STL 走 text_to_cad / build_structure、原理图走 render_schematic。
  严禁用 save_artifact / write_file / edit_file 去写 .png/.jpg/.step/.stl 这类文件，
  更不许覆盖**已经生成好的**图或模型（实测事故：有人把刚出好的 700 KB 渲染图覆盖成
  11 字节的 "placeholder"，用户看到的现象就是「外观图生成后被删掉了」）。
  要换视角/场景就再调一次 generate_product_image，它会自动顺延成 render-02.png。
  任何交付文件里都不要写 placeholder / TODO / 略 这类占位内容。
- 时间不够时**先保证阶段产物落盘**（先 save_artifact 再润色），超时会自动续期并可从
  下一阶段续跑；但产物没落盘就中止，等于这轮白跑。
- 委派时把前序结论带进任务描述，避免各子智能体自相矛盾。
- **派给子智能体的任务描述必须写中文**（task 的 description 参数）。写英文会带着子智能体
  一起用英文思考和汇报，用户看不懂；实测整条链出现过「主智能体中文、子智能体英文」的割裂。
- 子智能体的工具是**按角色裁剪**的（工业设计师有出图工具、硬件工程师有 PCB 工具，互不重叠）。
  所以「谁干哪件事」不能派错：别让结构工程师去导出 Gerber，也别让硬件工程师去出渲染图。
- **task 只有两个参数**：`subagent_type`（子智能体代号）+ `description`（中文任务描述）。
  多传任何别的字段（实测有人传 `description_en`）会被 schema 直接拒绝并报「参数错误」，
  白烧一整轮 LLM 往返。
- 全程中文输出，术语保留英文。
- 回交时用 Markdown 表格列出产物清单。
- 工作区的虚拟根目录是 `/`：文件一律写成 `/00_prd/prd.md`、`/20_structure/model.py` 这种形式。
  不存在 `/home/user` 这个目录，不要给它加前缀（后端会自动把 /home/user/... 折回根目录，
  但直接写根路径更省事）。
- 只读工具（ls / read_file / glob / grep）廉价且幂等，核对产物时该用就用；
  但同一份文件的内容若已在上文出现过，直接引用，不要为「确认一下」重复读取同一个路径。
  昂贵工具（generate_product_image / build_structure / render_product_views）反过来：
  同一组参数失败两次就换参数或走兜底，不要原样重试。

视觉与几何交付（本轮有真实工具可用，不要只在文档里描述）：
- **出图一律走 Agnes**：外观渲染图、场景图、爆炸图都由 Agnes 图像服务生成，
  与文本模型是哪一家无关（换 DeepSeek / SiliconFlow 当文本模型不影响这条线）。
- 外观阶段：industrial-designer 用 generate_product_image 出真实产品渲染图（PNG）。
  它写下的每条 prompt 会自动存档到 10_appearance/prompts.json。
- **同一产品的图必须串行同源**：第一张是文生图（基线），第二张起工具会自动以
  上一张为参考做图生图（reference="auto"），爆炸图则始终以第一张外观图为参考。
  你委派时要明确要求「一张一张出，不要并行、不要一次写完所有 prompt」，
  否则每张都是独立文生图，同一台产品会长出几个不同的外形。
- 结构阶段：mechanical-engineer **必须走 text_to_cad**（text-to-cad / cadgen 规范）：
  先不带 script 调用一次拿回外观提示词做形状约束，再带 build123d 脚本建模，
  导出 STEP/STL 并自动出四视图 + 爆炸图 + 20_structure/consistency.md。
  这样结构与外观是同一个产品，而不是各设计各的。
- 这两个工具可能因脚本语法错误或网关限流失败，失败时让子智能体重试，
  最多 3 次；仍失败则在文档里标注「本次未生成」并给出参数化尺寸表兜底，不要伪造成功。
- 最终汇总里用 Markdown 图片语法嵌入渲染图与爆炸图，让用户直接看到图，不要只给路径。
"""

# Plan 锚点：模型在长流程里会漂移（做到哪忘了自己该干啥，或者反复重做已完成的部分）。
# 硬约束是「每一步回交都必须带上 Step ID 与产物路径，下一步的任务描述必须引用它们」——
# 凭模型自觉做不到，必须写进提示词并让它显式复述。
PLAN_ANCHOR = """【Plan 锚点 · 防漂移硬约束】
1. 每委派一个子智能体，先在自己的回复里写一行锚点：
   `锚点 Step N：<子智能体名> → 目录 XX_xxx/`
2. 子智能体回交后，立刻核对它承诺的文件是否真的落盘（路径出现在产物列表里）。
   没落盘 = 这一步没完成，必须让它重做，不许当作完成进入下一步。
3. 下一步委派时，任务描述开头必须引用上一步的锚点与产物路径，例如：
   `基于 Step 2 已落盘的 10_appearance/appearance.md（外形尺寸 120×80×40mm），继续 Step 3…`
4. 禁止在没读上一步产物的情况下凭印象写约束 —— 尺寸、引脚、物料必须来自已落盘的文件。
"""


def get_model(settings: dict[str, Any] | None = None) -> ReasoningChatOpenAI | None:
    """构造模型。settings 来自前端（runtime 覆盖），缺失时回落 .env 默认值。

    拿不到任何可用 key 时返回 None，由调用方降级（不抛异常打断流程）。
    """
    import providers

    s = settings or {}
    model = (s.get("model") or "").strip() or config.MODEL
    base_url = (s.get("base_url") or "").strip() or config.DEFAULT_BASE_URL
    # 用户没填 key 时，按网关地址回落到 .env 里对应的那把
    api_key = (s.get("api_key") or "").strip() or config.fallback_key(base_url)
    if not api_key:
        return None
    try:
        temperature = float(s.get("temperature", 0.6))
    except (TypeError, ValueError):
        temperature = 0.6

    # 深度思考：前端显式给了开关就用它，否则回落 .env 的 AGENT_THINKING。
    # 实测这个开关之前根本没被消费：前端没传 thinking 时 `s.get("thinking", True)`
    # 恒为真，于是 .env 里配 AGENT_THINKING=0 也不生效 —— 想提速只能改代码。
    want_thinking = s.get("thinking")
    if want_thinking is None:
        want_thinking = config.THINKING
    body: dict[str, Any] = {}
    if want_thinking:
        raw = s.get("thinking_body")
        if isinstance(raw, dict) and raw:
            body = dict(raw)
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                body = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                body = providers.infer_thinking_body(model)
        else:
            body = providers.infer_thinking_body(model)
    else:
        # 【这里以前是个空 dict，等于什么都没发】—— 不发参数时网关按自己的默认走，
        # 实测照样吐思维链：一次完整 run 的 thinking 输出 89~106 万字符，占全部输出的
        # 75%~85%，直接把免费额度的 token 窗口打满，于是每一轮都撞 429。
        # 想关就必须**显式**告诉网关关掉，各家字段名不同，这里按模型名适配。
        body = providers.disabled_thinking_body(model)

    import httpx

    # connect / read 分开给：网关排大队时「连得上但要等很久」，
    # 一个统一的 300s 超时只会让整轮卡死五分钟才报错（见 config 里的实测注释）。
    timeout = httpx.Timeout(config.LLM_CONNECT_TIMEOUT, read=config.LLM_READ_TIMEOUT)
    kwargs: dict[str, Any] = dict(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_retries=config.LLM_MAX_RETRIES,
        timeout=timeout,
        max_tokens=8192,
    )
    if body:
        kwargs["extra_body"] = body
    # 默认走 certifi，不动；只有明确配了 CA 或显式关校验时才换 client
    if config.CA_BUNDLE or not config.VERIFY_SSL:
        kwargs["http_client"] = httpx.Client(verify=config.ssl_verify(), timeout=timeout)
    model_obj = ReasoningChatOpenAI(**kwargs)
    # 记账：按真实 usage 累计到滑动窗口，供请求前限速判断。
    # 子智能体也是走 get_model 建的模型，所以全局单例能覆盖整条链路。
    if config.QUOTA_GUARD:
        try:
            import quotaguard

            model_obj.callbacks = [quotaguard.get().handler()]
        except Exception:  # noqa: BLE001  记账挂不上不影响跑
            pass
    return model_obj


def build_agent(
    run_dir: Path,
    settings: dict[str, Any] | None = None,
    memory_hint: str = "",
    paradigm: "paradigms.Paradigm | None" = None,
    reflexion_hint: str = "",
):
    # memory 走虚拟文件系统路径：先把 AGENTS.md 放进本次 run 的工作区根
    # AGENTS.md 里附带工具目录 —— 子智能体通过记忆读到的是同一份，避免它们自造工具名
    src = ROOT / "AGENTS.md"
    body = src.read_text(encoding="utf-8") if src.exists() else ""
    try:
        (run_dir / "AGENTS.md").write_text(
            (body + "\n\n" + toolspec.agents_md_block()).strip() + "\n", encoding="utf-8"
        )
    except OSError:  # 写不进去只是少了记忆，不拖垮主流程
        pass

    # 提示词分层：主指令 → 情景记忆 → 反思规则 → 本轮范式档位 → 工具目录 → Plan 锚点
    blocks = [MAIN_PROMPT]
    if memory_hint:
        blocks.append(memory_hint)
    if reflexion_hint:
        blocks.append(reflexion_hint)
    if paradigm is not None:
        blocks.append(
            "【本轮决策范式】" + paradigm.label + "\n"
            f"设计哲学：{paradigm.philosophy}\n"
            f"适用边界：{paradigm.boundary}\n"
            f"预算：工具 {paradigm.tool_budget} 次 / {int(paradigm.max_seconds)} 秒。"
            "超预算会被强制中止，宁可少做一步也不要原地重试。\n"
            + paradigms.stage_plan(paradigm)
        )
    blocks.append(toolspec.prompt_block())
    blocks.append(skillspec.index_block())
    blocks.append(PLAN_ANCHOR)
    prompt = "\n\n".join(b for b in blocks if b)

    kwargs: dict[str, Any] = dict(
        model=get_model(settings),
        tools=MAIN_TOOLS,
        system_prompt=prompt,
        middleware=[TodoListMiddleware()],
        subagents=ALL_SUBAGENTS,
        backend=WorkspaceBackend(root_dir=str(run_dir), virtual_mode=True),
        name="product-design-agent",
    )
    try:
        return create_deep_agent(memory=["/AGENTS.md"], **kwargs)
    except Exception:  # noqa: BLE001  记忆不可用时降级为无记忆运行
        return create_deep_agent(**kwargs)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
        return "".join(parts)
    return ""


_REASON_KEYS = ("reasoning_content", "reasoning", "thinking")


def _reasoning_of(msg: Any) -> str:
    """取深度思考增量。兼容两种落点：
    1) additional_kwargs.reasoning_content（DeepSeek-R1 / V3.2 风格）
    2) content 里的 reasoning/thinking 内容块（部分网关与 Qwen3 风格）
    """
    ak = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(ak, dict):
        for k in _REASON_KEYS:
            v = ak.get(k)
            if v:
                return str(v)
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("thinking", "reasoning", "reasoning_content"):
                for k in _REASON_KEYS:
                    if b.get(k):
                        parts.append(str(b[k]))
                        break
                else:
                    parts.append(str(b.get("text", "")))
        return "".join(parts)
    return ""


def _args_ready(args: str) -> bool:
    """参数是否真的到齐了。

    三重判据，都是实跑踩出来的：
    1. 空串 —— 流式首发那一下 args 还没到。
    2. 半截 JSON —— 拼到一半，json.loads 必然失败（半截参数同样会毁掉去重签名）。
    3. 空容器 `{}` / `[]` —— 网关习惯先发一个空的 tool_calls 占位，真实参数随后
       才到；把 `{}` 当成「完整」就会发出一张空壳卡片，护栏还会把同一工具的所有
       调用塌缩成同一个签名。实测 read_file 因此被记成「重复 27 次」。
    网关若真下发非 JSON 参数，最多等 ARGS_WAIT_SECONDS 超时兜底。
    """
    a = (args or "").strip()
    if not a:
        return False
    try:
        v = json.loads(a)
    except Exception:  # noqa: BLE001
        return False
    if isinstance(v, (dict, list)) and not v:
        return False
    return True


def _is_prefix_overlap(buf: str, piece: str) -> bool:
    """部分网关每次全量重推 args（piece 含 buf 全部内容），此时不能拼接。"""
    return bool(buf) and bool(piece) and piece.startswith(buf)


def _label(ns: Any) -> str:
    if not ns:
        return "main"
    head = str(ns[0]) if isinstance(ns, (tuple, list)) else str(ns)
    return head.split(":")[0][:48] or "main"


def _emit_todos(sink: Callable[[dict], None], value: Any) -> None:
    if not isinstance(value, list):
        return
    items = []
    for t in value:
        if isinstance(t, dict):
            items.append(
                {
                    "content": str(t.get("content") or t.get("task") or ""),
                    "status": str(t.get("status", "pending")),
                }
            )
    if items:
        sink({"type": "todo", "items": items, "ts": time.time()})


def run_agent(
    requirement: str,
    run_dir: Path,
    sink: Callable[[dict], None],
    settings: dict[str, Any] | None = None,
    paradigm: "paradigms.Paradigm | None" = None,
    run: Any = None,
) -> str:
    """同步执行智能体；所有事件通过 sink 推送。返回最终文本。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    # run 对象传给工具层：confirm_with_user 要挂起整条流并把运行时钟往后拨，
    # 只给目录做不到（等待期间护栏必须放行，否则等人等成超时中止）。
    bind_context(run_dir, sink, settings, run)

    try:
        hint = store.memory_hint(requirement)
    except Exception:  # noqa: BLE001  记忆检索失败只是少了参考，不该拖垮主流程
        hint = ""
    if hint:
        sink({"type": "tool_note", "name": "memory", "detail": "已注入相似历史需求经验"})

    try:
        reflexion_hint = store.reflexion_hint()
    except Exception:  # noqa: BLE001  反思库读不到就当没踩过坑
        reflexion_hint = ""
    if reflexion_hint:
        sink(
            {
                "type": "tool_note",
                "name": "reflexion",
                "detail": f"已注入 {store.count_reflexions()} 条历史反思规则",
            }
        )

    agent = build_agent(run_dir, settings, hint, paradigm, reflexion_hint)
    final = ""

    def push(ev: dict) -> None:
        # 覆盖而非 setdefault：聚合事件（thinking / token）自带的是**首帧**时间，
        # 比它后面已经推走的事件还早 —— 实测出现 -4.4s 的负间隔，前端按 ts 排序
        # 会把思考内容插到上一条之前。统一以入队时刻为准，原值留在 ts_model。
        if "ts" in ev:
            ev["ts_model"] = ev["ts"]
        ev["ts"] = time.time()
        # 这里**不能**兜异常：guard 抛的 RunAborted（取消/超时/超预算）必须穿透出去
        # 才能打断 LangGraph 流；吞掉它 = 前端点了「停止」但后台继续空转。
        sink(ev)

    # cid -> 工具调用状态。流式下 args 是逐块拼出来的，必须按 tool_call_id 累积，
    # 否则第一个 chunk 只有 name、args 为空，前端就只剩一张空卡片。
    calls: dict[str, dict[str, Any]] = {}

    def ensure_call(cid: str, name: str, agent: str) -> dict[str, Any]:
        rec = calls.get(cid)
        if rec is None:
            rec = {"name": name or "tool", "agent": agent, "args": "", "started": False}
            calls[cid] = rec
        elif name and rec["name"] in ("", "tool"):
            rec["name"] = name
        return rec

    # args 是逐块拼出来的：第一个 chunk 往往只有工具名、args 还是空串。
    # 直接首发会让三处同时出问题 —— 前端卡片空白、落库事件没有参数、
    # 护栏把「同名工具」当成同一调用去重（读 5 个不同文件 = read_file 重复 5 次 → 误杀）。
    # 所以 args 没到齐就先挂起，等参数补齐（或超时）再真正发 tool_start。
    pending: dict[str, float] = {}

    def _fire_start(cid: str) -> None:
        rec = calls.get(cid)
        if rec is None or rec["started"]:
            return
        rec["started"] = True
        pending.pop(cid, None)
        push(
            {
                "type": "tool_start",
                "cid": cid,
                "agent": rec["agent"],
                "name": rec["name"],
                "args": rec["args"][:600],
            }
        )

    def flush_pending(force: bool = False) -> None:
        now = time.time()
        for cid in list(pending):
            rec = calls.get(cid)
            if rec is None:
                pending.pop(cid, None)
                continue
            if _args_ready(rec["args"]) or force or (now - pending[cid]) >= config.ARGS_WAIT_SECONDS:
                _fire_start(cid)

    def start_call(cid: str, name: str, agent: str) -> None:
        rec = ensure_call(cid, name, agent)
        if rec["started"]:
            return
        if not _args_ready(rec["args"]):
            pending.setdefault(cid, time.time())
            return
        _fire_start(cid)

    def close_call(cid: str, result: str, agent: str, name: str = "") -> None:
        rec = ensure_call(cid, name, agent)
        if rec.get("closed"):
            return
        # 结果比参数先到（网关不按序发 / 工具瞬间返回）：先把 start 补上，保证卡片配对
        if not rec.get("started"):
            _fire_start(cid)
        rec["closed"] = True
        result = result or ""
        push(
            {
                "type": "tool_end",
                "cid": cid,
                "agent": rec["agent"],
                "name": rec["name"],
                # args 在流式中后到，随结果一起回填，前端据此补齐卡片
                "args": rec["args"][:600],
                "result": result[:1500],
                "empty": not result.strip(),
            }
        )

    stream = agent.stream(
        {"messages": [{"role": "user", "content": requirement}]},
        stream_mode=["messages", "updates"],
        subgraphs=True,
        # 递归深度跟着档位走：ReAct 档本就不该深挖，ToT 档要留出分支的余量
        config={"recursion_limit": (paradigm.recursion_limit if paradigm else 120)},
    )
    for ns, mode, chunk in stream:
        agent_name = _label(ns)
        flush_pending()
        try:
            if mode == "messages":
                msg, _meta = chunk
                if isinstance(msg, ToolMessage):
                    cid = str(getattr(msg, "tool_call_id", "") or "")
                    close_call(
                        cid or f"anon-{len(calls)}",
                        _text_of(msg.content),
                        agent_name,
                        getattr(msg, "name", "") or "",
                    )
                    continue
                if isinstance(msg, AIMessageChunk):
                    for tc in getattr(msg, "tool_call_chunks", None) or []:
                        cid = str(tc.get("id") or "")
                        if not cid:
                            continue
                        rec = ensure_call(cid, tc.get("name") or "", agent_name)
                        # args 增量拼接（OpenAI 兼容网关逐块下发 JSON 片段）
                        piece = tc.get("args") or ""
                        if piece:
                            rec["args"] = (rec["args"] + piece) if not _is_prefix_overlap(rec["args"], piece) else piece
                        start_call(cid, tc.get("name") or "", agent_name)
                    for call in getattr(msg, "tool_calls", None) or []:
                        cid = str(call.get("id") or "")
                        if not cid:
                            continue
                        rec = ensure_call(cid, call.get("name") or "", agent_name)
                        args = call.get("args") or {}
                        s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                        # 只在「手上这份不完整、新这份完整」或「新这份更长」时覆盖：
                        # 网关先发占位 {}、后发真参数的顺序不能被先到的空壳锁死。
                        if _args_ready(s) and (
                            not _args_ready(rec["args"]) or len(s) > len(rec["args"])
                        ):
                            rec["args"] = s
                        start_call(cid, call.get("name") or "", agent_name)
                    reason = _reasoning_of(msg)
                    if reason:
                        push({"type": "thinking", "agent": agent_name, "text": reason})
                    txt = _text_of(msg.content)
                    if txt:
                        final += txt
                        push({"type": "token", "agent": agent_name, "text": txt})
            elif mode == "updates":
                if isinstance(chunk, dict):
                    for _node, update in chunk.items():
                        if isinstance(update, dict):
                            if "todos" in update:
                                _emit_todos(push, update["todos"])
                            files = update.get("files")
                            if isinstance(files, dict):
                                for p in files:
                                    push({"type": "artifact", "path": str(p), "size": 0, "preview": ""})
                            # 参数回填的第二条路：messages 流里网关给的是空壳 tool_calls，
                            # 但节点 update 里的 AIMessage 常常带着完整参数，且它到达得
                            # 比工具执行完更早 —— 用它补齐，tool_start 才有真实参数可用。
                            for m in update.get("messages") or []:
                                for call in getattr(m, "tool_calls", None) or []:
                                    cid = str(call.get("id") or "")
                                    rec = calls.get(cid)
                                    if rec is None:
                                        continue
                                    args = call.get("args") or {}
                                    s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                                    if _args_ready(s) and (
                                        not _args_ready(rec["args"]) or len(s) > len(rec["args"])
                                    ):
                                        rec["args"] = s
                                    if not rec.get("started") and _args_ready(rec["args"]):
                                        _fire_start(cid)
        except guards.RunAborted:
            # 护栏的中止信号（取消 / 超时 / 超预算 / 目标达成）必须穿透出去。
            # 这条 except 本来是给「单条事件解析失败」兜底的，写在整个循环体外面会
            # 把 push 抛出的 RunAborted 一并吞掉 —— 结果就是前端点了停止，后台继续跑。
            raise
        except Exception as e:  # noqa: BLE001  单条事件解析失败不能中断整条流
            push({"type": "warn", "message": f"事件解析跳过：{type(e).__name__}: {e}"})

    # 收尾：参数始终没等到的调用也要把卡片发出去（否则前端少一张、事件对不上）
    flush_pending(force=True)
    # 已发起但没收到结果的调用（工具抛错 / 流中断 / 网关漏发 ToolMessage）
    # 补一条收口事件，否则前端会永远挂着一张空卡片。
    for cid, rec in calls.items():
        if rec.get("started") and not rec.get("closed"):
            close_call(cid, "", rec["agent"])

    return final
