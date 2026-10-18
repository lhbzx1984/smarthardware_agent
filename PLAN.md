# 智能产品设计智能体 · 实现计划

> 输入一句产品需求 → 智能体自主规划流程 → 并行产出 **外观 / 结构 / 硬件 / 软件仿真** 四套方案 → 人工审核 → 导出交付包。
>
> 技术栈（已确认）：`deepagents`（Python 智能体内核） + `SiliconFlow` 模型 + `FastAPI/SSE` 运行时 + `Supabase`（数据·存储·实时） + `Next.js`（UI，部署 Vercel）

---

## 一、总体架构

```
┌─────────────── Vercel ───────────────┐
│  Next.js 15 App Router + Tailwind    │
│  需求输入 / 规划看板 / 四阶段产出     │
│  HITL 审批 / 交付包下载              │
└───────┬──────────────────────┬───────┘
        │ REST                 │ Realtime 订阅
┌───────▼──────────────┐  ┌────▼─────────────────┐
│ Supabase Postgres    │  │ Supabase Realtime    │
│ projects/todos/      │  │ agent_events 流式推送 │
│ artifacts/events     │  └────▲─────────────────┘
│ Storage: 产物文件     │       │ 写事件
└──────────────────────┘  ┌────┴─────────────────┐
                          │ Agent Worker (Python) │
                          │ FastAPI + deepagents  │
                          │ 本机 / ECS 长驻运行   │
                          └───────────────────────┘
```

**为什么要拆成 Python worker**：Vercel 是 serverless，跑不了十分钟级的智能体循环。智能体内核独立成 FastAPI 服务（本机开发、上线迁到你已有的阿里云 ECS），Next.js 只做交互与展示，两边通过 Supabase 解耦。

---

## 二、智能体内核设计（deepagents）

### 主智能体（编排层）
`create_deep_agent` 配置：

| 参数 | 取值 | 作用 |
|---|---|---|
| `model` | `ChatOpenAI(base_url=SiliconFlow, model=Qwen2.5-72B/DeepSeek-V3)` | 统一推理后端 |
| `middleware` | `TodoListMiddleware()` | 显式任务规划（`write_todos`），UI 直接渲染看板 |
| `subagents` | 6 个声明式子智能体 | 上下文隔离，各自只回一份最终报告 |
| `memory` | `["AGENTS.md"]` | 设计规范、输出契约、用户偏好持久化 |
| `backend` | `FilesystemBackend(root=workspace/<project>)` | 所有产物落盘，天然可版本化 |
| `interrupt_on` | `{"write_file": ...}` 关键路径 + 审批工具 | 人在回路 |
| `permissions` | 只允许 `/workspace/**`，拒绝 `.env`、凭据 | 声明式访问控制 |

### 六个子智能体

| 子智能体 | 职责 | 产出文件 |
|---|---|---|
| `requirements-analyst` | 需求澄清、约束抽取、验收标准 | `00_prd.md` |
| `industrial-designer` | 形态语义、CMF、人机交互、渲染提示词、三视图 SVG | `10_appearance/*.md|svg` |
| `mechanical-engineer` | 外壳分件、壁厚/拔模/装配序列、材料与工艺、DFM 检查 | `20_structure/*.md` |
| `hardware-engineer` | 主控选型、原理图连接表、电源树、BOM、Cirkit 接线清单 | `30_hardware/*.md|json|csv` |
| `firmware-engineer` | Arduino/C++ 固件、状态机、Cirkit 仿真步骤 | `40_firmware/*.ino|md` |
| `integration-reviewer` | 交叉一致性评审（尺寸↔PCB↔外壳↔引脚冲突） | `90_review.md` |

### 自定义工具（`tools=`）

- `search_cirkit_docs(query, top_k)` — 对 23 页 Cirkit 文档做 BM25 检索（零依赖，已落地抓取）
- `list_cirkit_components(keyword)` — 元件库查询（先查本地文档，缺则提示到 app 检索）
- `check_circuit(connections)` — 规则校验：电源/地短路、LED 缺限流电阻、引脚冲突、电压域不匹配、I2C 上拉缺失
- `build_bom(components)` — BOM 规范化 + 去重 + 估价 + 导出 CSV
- `check_arduino(code)` — 静态检查（括号配对、`setup/loop` 存在性、引脚与原理图一致性），有 `arduino-cli` 则真编译
- `emit_artifact(path, content)` — 落盘并同步 Supabase Storage + 发 Realtime 事件

### 技能（渐进式披露，`skills/`）
`product-design/SKILL.md`、`industrial-design/`、`mechanical/`、`hardware-cirkit/`、`firmware-arduino/` —— 主智能体只读 frontmatter，用到才加载正文，保持启动上下文紧凑。

---

## 三、目录结构

```
agent SDK/
├── PLAN.md                      本文件
├── scripts/
│   ├── crawl_docs.py            ✅ 已实现：Cirkit 文档抓取（23/23 页）
│   └── build_index.py           BM25 索引构建
├── knowledge/
│   ├── raw/                     ✅ 23 页 Markdown（约 110KB）
│   └── index/bm25.json
├── agent/                       Python 智能体 worker
│   ├── server.py                FastAPI + SSE
│   ├── main_agent.py            create_deep_agent 装配
│   ├── subagents.py             6 个子智能体定义
│   ├── tools/                   knowledge.py / circuit.py / bom.py / firmware.py
│   ├── skills/                  5 个 SKILL.md
│   ├── AGENTS.md                长期记忆与输出契约
│   └── workspace/<project>/     产物落盘
├── web/                         Next.js 应用
│   ├── app/                     App Router 页面
│   ├── lib/supabase/            client / server / types
│   └── components/              看板、阶段卡、审批、产物预览
└── supabase/
    └── migrations/0001_init.sql
```

---

## 四、Supabase 数据模型

| 表 | 关键字段 |
|---|---|
| `projects` | id, title, requirement, status, current_stage, created_at |
| `todos` | id, project_id, content, status(pending/in_progress/completed), owner_agent, seq |
| `artifacts` | id, project_id, stage, path, mime, size, storage_key, version |
| `agent_events` | id, project_id, type(message/tool/todo/artifact/interrupt), payload jsonb, ts |

- Realtime 开 `agent_events` + `todos` 两张表的 publication，前端订阅即可看到智能体实时进度
- Storage bucket `artifacts`，路径 `<project_id>/<stage>/<file>`
- RLS：开发期先放开 service-role 写、anon 读；上线前收窄到 user_id

---

## 五、里程碑

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **M0** ✅ | 环境 + 知识库 | venv 建好；Cirkit 文档 23/23 抓取成功；依赖装完可 import |
| **M1** | 智能体内核跑通（无 UI） | `python -m agent.main_agent "做一个温湿度监测小夜灯"` 产出四套方案文件 |
| **M2** | FastAPI + SSE | `/run` 启动任务，`/events/<id>` 流式吐 token/工具/todo |
| **M3** | Supabase 接入 | 表 + Storage + Realtime，worker 写事件，前端能订阅到 |
| **M4** | Next.js UI | 需求输入 → 规划看板 → 四阶段产出 → HITL 审批 → 导出 ZIP |
| **M5** | 真实闭环 | 用 Cirkit 账号抓示例项目补知识库；产物导入 Cirkit 可仿真 |

---

## 六、风险与对策

1. **SiliconFlow 模型指令跟随弱于 Claude** → 子智能体提示词写得极结构化（固定输出 schema + 自检清单）；关键步骤用工具硬校验而非靠模型自觉。
2. **长流程 token 成本** → 子智能体隔离上下文、技能渐进式加载、`write_todos` 控流程；主智能体只收最终报告。
3. **Vercel 不能跑长任务** → worker 独立部署；前端只通过 Supabase 读状态，不直接依赖 worker 长连接。
4. **凭据安全** → Cirkit 账号密码只进环境变量 / Supabase secrets，不写进任何文件与日志；`permissions` 拒绝智能体读取 `.env`。
5. **Cirkit app 抓取不稳** → M5 阶段做，失败则降级为「人工导出 JSON → 上传解析」。

---

## 七、需要你确认的最后一件事

- 智能体 worker 先跑**本机**（开发调试最快），M5 再迁 ECS —— 如果你希望直接按 ECS 部署来写，请说一声。
- Cirkit 账号密码我不会写进代码库，抓取时我会从环境变量读取，或由你在浏览器里登录好后交给我抓。
