# easyeda-copilot MCP 接入评估（2026-10-30）

调研对象：<https://github.com/biosshot/easyeda-copilot>（MIT，`easyeda-copilot-mcp` v1.3.2）

---

## 一、它是什么

一个 **MCP server + EasyEDA 桌面端扩展** 的组合：AI Agent 通过 MCP 协议驱动**本机正在运行的 EasyEDA Pro Desktop**，在**真实原理图 / PCB 文档**上做设计，产物是可继续手工编辑的原生 EasyEDA 对象。

```
Codex / Claude Code / 我们的智能体
            │  stdio（MCP JSON-RPC）
            ▼
   easyeda-copilot-mcp（npm 包，Node ≥20.19）
            │  WebSocket 127.0.0.1:8787（本机 named pipe / unix socket）
            ▼
   EasyEDA Copilot 扩展（.eext，需开启 External Interactions）
            │
            ▼
   EasyEDA Pro Desktop 中已打开的原理图 / PCB 文档
```

## 二、硬前置（缺一不可）

| 依赖 | 是否必需 | 本机现状 | 说明 |
|---|---|---|---|
| EasyEDA Pro Desktop **V3.2.149** | ✅ 必需 | ❌ 未安装 | 必须启动并**打开目标文档**，无 headless 模式 |
| `.eext` 扩展 + 开启 External Interactions | ✅ 必需 | ❌ | 从 Releases 下载后手动导入 |
| Node.js ≥ 20.19 | ✅ 必需 | ✅ v22.22.2 | 已具备 |
| 联网 | 部分 | ✅ | `npx` 拉包、LCSC/EasyEDA 元件解析、首次布线会下载 KRT 与私有 Python 运行时 |
| API key | ❌ 不需要 | — | 走 public EasyEDA API，无自备密钥 |

**决定性结论**：所有工具（含元件搜索）都要经 bridge 拿 `EasyEdaInstance`，没有桌面端时统一报
`EasyEDA MCP interface is not connected.`。**它不是一个能无人值守跑的库，而是"人机协同"通道。**

## 三、能力清单（30 个工具）

| 分区 | 工具 | 对我们有用吗 |
|---|---|---|
| 工程控制 | `get_all_projects` `get_current_project_info` `create_doc` `open_document` `sync_current_document` `list_easyeda_instances` | ✅ 建工程、开文档、原理图→PCB 同步 |
| 原理图 | `get_schematic` `get_full_schematic` `get_schematic_groups` `get_full_schematic_groups` `annotate_designators`（preserve / resequence） | ✅ **读回真实网表**；位号标注 |
| 元件 | `search_query`（MPN / UUID 解析，LCSC 库） | ✅ **真实封装、尺寸、价格**，反哺 BOM 与结构限高 |
| PCB 布局 | `make_pcb_layout`（约束 DSL：功能块/引脚邻近/边缘放置/禁布/安装孔/散热焊盘/preserve） `get_current_pcb` `get_pcb_component_sizes` `get_pcb_stack_layers` | ✅ **我们现在完全没有的能力** |
| PCB 布线 | `run_pcb_router_dsl`（网络类/电源网络/铜皮/差分对/等长/扇出/阻抗/过孔缝合） `validate_pcb_dsl` | ✅ 铺铜、地平面、缝合过孔 |
| 检查预览 | `preview_pcb`（分层预览） `inspect_net` `inspect_component` `preview_component` `preview_image_path/url` | ✅ 落盘 PCB 预览图 |
| 验证 | `get_pcb_drc_rules` **原生 EasyEDA DRC** `preview_error` `preview_recommended` `list_checkpoints` | ✅ 真 DRC（我们目前只有自写的 ERC 规则） |
| 长任务 | `wait_operation` `list_operations` `cancel_operation` `apply_operation` | ⚠️  mutation 先等 50s 返回 operation ID，之后轮询；**断连不重试 mutation** |
| 其它 | `search_reused_block` `wait_ms` | 复用块检索 |

**不支持**：Gerber / NC Drill 导出（README 全文未提，制造文件仍走 EasyEDA 自身导出）。

## 四、我们现在的硬件链路 vs 接入后

| 环节 | 现状 | 接入后 |
|---|---|---|
| 原理图 | 模型写 `netlist.json` → 自研 `schematic.py` 渲 SVG（示意级，非 EDA 网表） | EasyEDA 生成**真原理图**（可编辑、可导网表、可进 PCB）+ 回读网表覆盖 `netlist.json` |
| 电气检查 | `check_circuit` 自写 ERC（6 条规则） | EasyEDA **原生 DRC** + ERC 并存 |
| 封装/尺寸 | 模型估（有编造风险） | `get_pcb_component_sizes` / 元件解析给**真实封装尺寸**，反哺结构限高校验 |
| BOM | `build_bom` 文本规范化 | LCSC 解析补真实型号、封装、单价 |
| PCB | ❌ 无（只有 `pcb-layout.md` 文字约束） | 板框 + 约束布局 + 布线 + 铺铜 + DRC + 预览图 |
| 仿真接线 | `cirkit-steps.md`（Cirkit Designer 复现步骤） | 保留（EasyEDA 不覆盖仿真） |

## 五、结论：**不能"替换"，只能"优先 + 回落"**

原因很简单——智能体跑在后台 worker 里，而 EasyEDA 桌面端是 GUI 应用且必须有人打开文档。
无条件替换会让"没开 EasyEDA"变成整条流水线失败。正确姿势：

```
hardware-engineer 开工
   └─ 探测 EasyEDA 是否在线（list_easyeda_instances）
        ├─ 在线 → EasyEDA 真链路（建/开文档 → 解析元件 → 生成原理图 → 同步 → 布局 → 布线 → DRC → 预览落盘）
        │          并把回读的 netlist / 封装尺寸 / DRC 报告写回 30_hardware + 50_pcb
        └─ 离线 → 现有链路（netlist.json + schematic.py SVG + cirkit-steps.md），产物契约不变
```

下游（评审、前端产物面板、cirkit-steps、BOM）**契约零变更**，只是上游数据源从"模型写的 JSON"升级为"真实 EDA 回读"。

## 六、接入路径选择

| 方案 | 做法 | 评价 |
|---|---|---|
| **A. CLI daemon（推荐）** | `easyeda-copilot-cli start` 拿 4 字符 ID，之后 `<id> call <tool> --input file.json`，结果 JSON 到 stdout | 最省事：不用自己实现 MCP stdio 客户端，daemon 复用避免每次握手；Python 侧 `subprocess` 即可 |
| B. stdio MCP 客户端 | 自己写 JSON-RPC over stdin/stdout（~80 行） | 更可控、无 daemon 生命周期管理，但要处理初始化握手与流控 |
| C. 直接用 Python SDK | 仓库 `mcp/python` 只有 `units.py`，不是完整 SDK | ❌ 不可行 |

长任务统一走 `wait_operation` 轮询，且**mutation 失败不自动重试**（官方明确警告），只回退到离线链路并告知用户。

## 七、分阶段路线

**阶段 A（无需 EasyEDA，可立即落地）**
1. 新增 `agent/easyeda.py`：探测 + CLI/MCP 调用 + 工具封装（约 8 个面向智能体的 tool）
2. 新增 `50_pcb` 阶段（或挂在 `30_hardware/pcb/`）：产物契约 `board.json` / `dsl.json` / `drc.md` / `preview.png`
3. `hardware-engineer` prompt 增加"探测→分流"分支；离线路径完全不变
4. 单元测试（mock bridge），回归现有 51 项 pipeline 测试

**阶段 B（你装好 EasyEDA Pro 后联调）**
5. 真链路打通：建工程 → 原理图 → 同步 → 布局 → 布线 → DRC
6. 回读网表与封装尺寸，验证与结构限高的一致性校验

## 八、风险与注意

- **无人值守跑不了**：每次 PCB 设计都需要你先打开 EasyEDA 与目标文档（这是设计定位，不是缺陷）
- **长任务**：布局/布线可能数分钟，需轮询 + 用户等待；`stop` 不回滚
- **mutation 不重试**：断连即失败，必须显式回退
- **版本绑定**：EasyEDA Pro V3.2.149；更新客户端可能失效

---

## 九、补充（2026-10-30）："只想生成文件、不想打开 GUI"怎么办？

### 9.1 结论先行

| 问题 | 答案 |
|---|---|
| 用 **easyeda-copilot** 这条链路，能不能不装 EasyEDA 客户端？ | ❌ **不能**。它就是个遥控器 |
| 只生成 EDA 工程文件（双击即可打开），能不能不装？ | ✅ **能**，但要换路线：按公开格式自己写文件，不走 MCP |

**证据**：`mcp/src/` 全目录内 **0 个 http/https 调用**（`grep https?://` 无命中），所有能力（含元件搜索）都经
`mcp/src/bridge/index.ts` 的 WebSocket 打到本地 EasyEDA 客户端；README Requirements 第一条就是
`EasyEDA Desktop V3.2.149` + 扩展开启 `External Interactions`，Usage 里明确要求"打开 EasyEDA Desktop → 打开目标文档"。
MCP 侧**不写任何文件**，它只在已打开的文档上增删改对象。

### 9.2 绕开 MCP 的依据：EasyEDA Pro 工程是**公开格式**

- 官方文档：<https://prodocs.easyeda.com/cn/format/>（V2.2 与 V3 两份规范；V3 版 `2025.10.21`，MD 约 3146 行）
- 容器：`*.epro` / `*.epro2` = **ZIP 归档**
- 内容：`project.json`（工程清单） + `*.esch`（原理图页） + `*.epcb`（PCB） + `*.esym`（符号） + `*.efoo`（封装） + `*.eblob`
- 行格式：每行两个 JSON 对象，用 `||` 分隔 —— 外层 `{type,id,ticket}`（一致性框架）+ 内层图元数据
- 图元：V3 统一用 `DOCHEAD` / `HEAD` / `CANVAS` / `LAYER` / `PRIMITIVE` / `COMPONENT` / `ATTR` / `WIRE`
- 单位 0.01 inch、逆时针角度制、颜色 `#RRGGBB`
- 第三方整理版：<https://github.com/HalfSweet/EasyEDA-Pro-V3-format>（官方 PDF 转 Markdown）
- 旁证：KiCad 官方 `dev-docs.kicad.org/en/import-formats/easyeda/` 完整写明了 EasyEDA Pro 的 ZIP 结构与行格式 —— 说明该格式**可解析、可生成**

**所以：只要实现"网表 → 图元 → 行 → zip"，就能在无 GUI、无 EasyEDA 的情况下产出 `.epro`，用户双击打开即可。**

### 9.3 器件符号/封装从哪来（实测结果）

| 来源 | 实测 | 结论 |
|---|---|---|
| `https://easyeda.com/api/products/{uuid}`（开放库 API） | 403 / 404 | ❌ 已不可直连，不能依赖 |
| `https://wwwapi.lcsc.com/...` | SSL EOF（反爬/地域） | ❌ 不稳定 |
| 自建常用器件库（符号引脚 + 封装焊盘） | 可控 | ✅ 推荐地基 |
| KiCad 官方库仓库（`kicad-symbols` / `kicad-footprints`，纯文本、CC 授权，git 可拉，无需安装 KiCad） | 纯文件 | ✅ 现成海量 |

### 9.4 三条可选路线

| 路线 | 产物 | 工作量 | 风险 | 与嘉立创生态 |
|---|---|---|---|---|
| **A. 直写 EasyEDA Pro `.epro`** | 双击即开的原生工程 | 大（格式实现 + 自建符号/封装库） | 中高：客户端校验严格度未知（uuid/ticket/一致性框架），**本机无法自验**，需你打开确认 | ⭐⭐⭐ 最贴合 |
| **B. 直写 KiCad 文本工程**（`.kicad_pro/.kicad_sch/.kicad_pcb`） | KiCad 直接打开；EasyEDA Pro **支持导入 KiCad 工程** | 中（s-expression，格式公开稳定、容错高；器件库现成） | 低：纯文本可 diff，社区参考实现多 | ⭐⭐ 需一次导入 |
| **C. 维持现状**（`netlist.json` + 自绘 SVG + 文字约束） | 示意级 | 无 | 无 | ⭐ |

补充：两条新路线的**布线**都不建议自己写死 —— 生成"板框 + 布局 + 电源/地预铺铜"，其余交给 EDA 自带布线器或
Freerouting（可无头跑 DSN/SES）。**Gerber / NC Drill 制造文件依旧出不了**（需 kicad-cli 或 EDA 内导出）。

### 9.5 建议

1. **先做共用地基**：`netlist` 结构化 + 常用器件库（引脚定义 / 封装焊盘尺寸 / LCSC 编号 / 限高）。A、B 都能用。
2. **格式先 B 后 A**：KiCad 文本容错高、器件库现成、能出 Gerber、还能被 EasyEDA Pro 导入，验证成本最低；
   `.epro` 作为第二阶段（你装了客户端能自验时）再补。
3. **easyeda-copilot 保留为可选增强**：你本机开着 EasyEDA 时走真链路（真 DRC、真布线、真封装尺寸），否则自动回落到文件生成链路。
4. **验证方式**：生成的 KiCad 工程可由 KiCad 打开/EDA 导入校验；`.epro` 可由 KiCad 的"EasyEDA Pro 导入"做交叉校验（KiCad 8+ 支持 `.epro`）。

---

## 十、产品视角：哪条路径用户体验最好？

### 10.1 先定评判标准（对本项目而言）

这个产品的用户不是 EDA 工程师，他要的是"我说一句话，拿到**能看懂、能改、能打样**的硬件方案"。
所以按四条打分：

| 标准 | 为什么重要 |
|---|---|
| **① 每次运行必交付** | 智能体后台跑，路径不能依赖"用户此刻开着某个 GUI"，否则失败率不可控 |
| **② 打开成本** | 用户要装什么、点几下才能看到东西 |
| **③ 打样闭环** | 能否一路走到 Gerber + BOM + 坐标（嘉立创一键下单） |
| **④ 界面观感** | 网页产物面板上呈现成什么样（这才是用户第一眼看到的） |

### 10.2 四条路径打分

| 路径 | ① 必交付 | ② 打开成本 | ③ 打样闭环 | ④ 界面观感 | 工程成本 |
|---|---|---|---|---|---|
| easyeda-copilot（遥控 GUI） | ❌ 没开客户端就整体失败 | 高：装客户端 + 开文档 + 守着 | 中：真 DRC/真布线，但 Gerber 仍要手动导 | 好（自带预览图） | 低（工具现成） |
| **直写 KiCad 文本** | ✅ 纯代码，必成 | 低：免费 KiCad 打开；网页里可直接看渲染 | ✅ `kicad-cli` 无头出 Gerber/BOM/坐标，JLCPCB 生态成熟 | ✅ 自研渲染器完全可控 | 中 |
| 直写 EasyEDA `.epro` | ✅ 纯代码，必成 | 中：必须装 EasyEDA 才打得开 | ❌ Gerber 只能在客户端导出 | ✅ 同样可自渲染 | 高（格式细节多） |
| 现状（netlist + 自绘 SVG） | ✅ | 无（只有示意 SVG） | ❌ 没有真实工程文件 | 中（示意级） | 无 |

### 10.3 推荐：**结构化网表做唯一事实源，KiCad 为主、`.epro` 为辅，easyeda-copilot 降级为可选增强**

理由：

1. **网表结构化是四条路径共同的地基**，而且它本身就提升现有质量 —— ERC 校验、BOM、限高校验都能从"模型编的字符串"升级为"可校验的结构化数据"。先做它，稳赚。
2. **KiCad 文本是主输出**：容错高、可 diff、器件库现成（官方库纯文本可拉取）、`kicad-cli` 能无头出制造文件、**EasyEDA Pro 也能导入 KiCad 工程**，等于花一份工拿到两个生态。
3. **`.epro` 是副输出**：同一个网表再写一个 writer 而已，给习惯嘉立创原生工程的用户。放到格式验证通过之后。
4. **easyeda-copilot 不进主链路**，只在"探测到 EasyEDA 在线"时启用，用来补真 DRC / 真布线 / 真实封装尺寸，并把结果回流到网表。它从"依赖"变成"增益"，失败也无感。
5. **界面层决定体验上限**：用户在网页里看到的应该是可缩放/可点选器件的原理图与 PCB 预览 + BOM 表 + 一键下载 zip（KiCad 工程 + `.epro` + BOM csv + Gerber），而不是一堆文件名。自研 s-expression / JSON Lines → SVG 渲染器，无外部依赖、可控。

### 10.4 落地节奏（建议）

| 阶段 | 内容 | 用户可见的收益 |
|---|---|---|
| **P0** | 网表 schema + 常用器件库（30~60 个：ESP32-C3/S3 模块、LDO、USB-C、0402/0603 阻容、LED、按键、排针、PH2.0、MOS、传感器接口）+ KiCad 原理图 writer + 自研 SVG 渲染 | 硬件阶段从"示意 SVG"升级为**真原理图文件**，网页里可交互查看 |
| **P1** | PCB writer（板框 + 自动布局 + 电源/地铺铜）+ PCB 预览渲染 + BOM/坐标导出；布线先不吹全自动 | 第一次出现**真实 PCB 布局**，能量化板子尺寸，反哺结构阶段 |
| **P2** | `.epro` writer + Gerber 出包（kicad-cli 可选） | 嘉立创原生工程 + 制造文件，接近"能下单" |
| **P3** | easyeda-copilot 可选增强通道（探测式） | 开着客户端时拿到真 DRC / 真布线 |

### 10.5 两个诚实提醒

- **PCB 自动布线质量是这个领域公认的难点**。建议 P1 只承诺"板框 + 布局 + 电源/地预铺铜"，并在产物里明说"信号线需在 EDA 内自动布线"。把预期管住，比交付一张走不通的板子体验好得多。
- **器件库要长期维护**。建议库条目带 LCSC 编号与封装尺寸，和现有 BOM 复用同一份数据，避免两处漂移。
- **平台**：支持 Windows x64；Linux ARM64 / musl 不支持完整栈
- **许可**：MIT，可商用；但产物留在 EasyEDA 生态内（这点与"Cirkit Designer 可仿真"的现有约束是同构的）
