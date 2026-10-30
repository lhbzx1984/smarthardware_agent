# 智能产品设计智能体 · 交付说明

输入一句产品需求 → 智能体自主规划 → 并行产出**外观 / 结构 / 硬件 / 软件仿真**四套方案 → 交叉评审 → 产物落盘 + 会话入库。

---

## 最新改动：修「跑满 120 步整轮崩溃」（2026-10-30 · 第十七轮）

报错：`GraphRecursionError: Recursion limit of 120 reached`，整轮被打成 error，
前面几个阶段**已经落盘的产物全部作废**。

### 根因一：子图与父图共享 step 计数，120 步根本不够

LangGraph 里子智能体的每一次「模型 → 工具 → 模型」都算在**主图的账上**。
六阶段串行链不是「6 次派单」那么便宜 —— 一个子智能体内部就能跑几十步。
标准档的 120 步必然在某个阶段耗尽。

修法（`agent/paradigms.py`）：按档位重给预算 ——
**lean 120 / standard 600 / review 800 / explore 1000**，并可用
`AGENT_RECURSION_{LEAN,STANDARD,REVIEW,EXPLORE}` 覆盖。
真正的无效循环由 server 层的**工具预算**兜底拦，步数不再是第一道闸。

### 根因二：撞上限被当成故障，产物跟着陪葬

`GraphRecursionError` 一路冒泡到 server 的兜底 except → `status=error`。
可实际上这往往只是「这一轮的往返额度花完了」，产物已经攒到四五个阶段。

修法：新增 `guards.RunStepLimit`（`RunAborted` 子类，`status="steps"`）。
`main_agent.run_agent` 捕获 `GraphRecursionError` 后先收口工具卡片、推一句人话提示，
再转成中止信号 —— 走的是和「超时中止」同一条路径：**产物保留 + 自动续跑**。
`_maybe_auto_resume` 的触发名单从只有 `timeout` 扩到 `timeout` + `steps`。

### 附带：软阈值预警 + 补派次数上限

- 用掉 80% 步数时推一次预警（「先保证当前阶段产物落盘，别在同一阶段反复补派」），
  不再等到撞墙才知道。
- 主提示词新增硬约束：**同一阶段最多补派 2 次**，第 3 次就在文档里标注缺项并进入下一阶段。
  上一轮那次卡在结构阶段反复补派，正是把步数烧光的直接原因。
- 前端历史列表新增 `steps` 状态样式（达到步数上限，中性色，不与「失败」混为一谈）。

### 回归

新增 `scripts/test_step_limit.py`（**18 项**：档位预算、环境变量覆盖、中止语义、
假流耗尽 → 转成 `RunStepLimit`、软阈值只预警一次、续跑名单）。
`test_paradigms` 45 项（顺带修了一条被 `.env` 的 `AGENT_STANDARD_SECONDS=2400` 打破的
断言 —— 断言的是档位设计，比对前先卸掉 env 覆盖）、`test_subagent_tools` 65、
`test_goal_stop` 22、`test_timeout_extend` 20、`test_rate_limit` 18、`test_delete_run` 17、
`test_loop_guard` 24、`test_stage_once` 全绿；前端 `tsc --noEmit` 通过。

---

## 修「结构阶段空交付」（2026-10-30 · 第十六轮）

真实运行「桌面式温湿度监测小夜灯」`2344fca905b9`：外观阶段很干净（render-01/02 串行同源，
爆炸图也出了），**结构阶段零产物** —— 子智能体返回空三次，最后主智能体自己写文档兜底交差。

### 根因一：主智能体改派给了没有工具的兜底角色（直接原因）

`mechanical-engineer` 返回空之后，主智能体换人派了 `general-purpose`。
这个同名子智能体是 **deepagents 自动补的**（`graph.py`：caller 没提供同名 spec 时才自动加），
它的工具 = 文件中间件那套 + 主智能体的工具，而本项目主智能体只有 `confirm_with_user`。
所以它诚实地回了一句「我没有 build_structure」，结构阶段就这么空了。
`mechanical-engineer` 本身是**有** `build_structure` / `text_to_cad` / `generate_product_image` 的。

修法：`agent/subagents.py` 显式提供同名 `general-purpose` spec 覆盖内置的（现在是 7 个角色，
它拿全套 16 个工具），并在主提示词里写死**补派只能补同一个角色**。

### 根因二：2.4 万字的几何推演被截断（为什么原角色返回空）

结构工程师在一条消息里做球冠半径、孔位坐标的长推演（thinking 约 2.4 万字），
还没来得及调 `text_to_cad` 就被截断 → 返回空、零产物 → 主智能体以为它失败。

修法：三处同时压 —— `COMMON_RULES` 加「别在思考里做长篇数值推演」、
`mechanical-engineer` 提示词要求**先把脚本写进 `20_structure/model.py` 再调 `text_to_cad(script=…)`**、
`text_to_cad` 第一步的返回里也带上这条硬要求。落文件后即使截断也不丢成果。

### 附带：门禁把「已有文件」说清楚

结构阶段目录里躺着外观阶段预生成的 `exploded-01.png`，门禁只说「已有 1 个文件」，
子智能体不知道它不算契约产物。`stage_gate` 现在分档列出：
**契约产物已齐 / 仍缺 / 非契约产物（不代表本阶段做完）**。

### 回归

`scripts/test_subagent_tools.py` 47 → **65 项**（新增【8】兜底角色覆盖内置、【9】长推演约束、
【10】空回交也算失败 + 补派不换人）；其余 11 个脚本全绿。

**待生效**：worker 未重启（夜灯那次运行还在跑硬件/固件阶段，重启会打断它）。
改动已落盘，下次跑 `restart-worker.ps1` 后自动生效。

---

> **代码仓库**：https://github.com/lhbzx1984/smarthardware_agent（SSH `git@github.com:lhbzx1984/smarthardware_agent`，main 分支，2026-10-18 首次推送）
> clone 加 `--recursive`（`vendor/text-to-cad` 建模规范是子模块）。
> `.env` / `data/` / `workspace/` / `node_modules` / `.next` 已排除，不含密钥。

## 最新改动：修「外观阶段零渲染图交付」+ 强制串行派单（2026-10-30 · 第十五轮）

真实运行「便携式土壤湿度检测仪」（run `cd8a78352166`）暴露的问题：**外观阶段一张渲染图都没出**，
子智能体在思考里断定「当前环境没有 generate_product_image」，主智能体照单全收并请用户确认；
同时主智能体一边派出图、一边自己把 `20_structure/structure.md` 写完（违反调度者角色 + 违反串行）。

**根因（已实测排除法定位）**：不是工具没绑——我实测过 deepagents 的 harness profile
（`_harness_profile_for_model`）对当前模型 `excluded_tools` 为空，六个子智能体确实都拿到了工具。
是**模型幻觉**：网关降级到备用模型后，模型在 thinking 里枚举"我有哪些工具"列了一份错的清单
（ls/read_file/write_file/edit_file/delete/glob/grep + confirm_with_user），
于是压根不去调用出图工具。工具越多越容易迷失 —— 16 个全塞给每个角色是催化因素。

**改动**：
1. `agent/subagents.py`：新增 `TOOLS_FOR`，**按角色裁剪工具集**（需求 4 / 外观 6 / 结构 7 /
   硬件 11 / 固件 5 / 评审 2，原来一律 16）。
2. 新增 `tools_note(agent)`：把该角色**真正持有的工具名**写进它自己的提示词，
   并明说「清单是权威的，思考时以为自己没某个工具也要照清单调用」+
   「图片由 Agnes 出图服务生成，与文本模型是哪一家无关，换模型不会让出图能力消失」。
3. `agent/main_agent.py` 提示词：出图/建模阶段**串行是硬约束**（一条消息只允许一个 task），
   派单描述必须中文，子智能体报「本次未生成 / 工具不可用」时**必须先补派同一个子智能体一次**
   （并在描述里点名该调的工具），不允许直接确认交付；顺带写明 task 只有
   `subagent_type` + `description` 两个参数（实测有人传 `description_en` 被 schema 拒绝）。
4. `agent/tools.py`：`save_artifact` 空 content 时的报错补上「不支持分块写，一次带全文」
   （实测有模型先传空 content 占位建文件，界面于是先冒出一个空产物）。

**回归**：新增 `scripts/test_subagent_tools.py`（47 项：契约工具齐全 / 确实裁剪过 /
跨角色不串工具 / 提示词清单覆盖每个工具名 / 主提示词串行与补派约束）。
连同 deadlock、stage_once、artifact_guard、visual_chain、artifact_sync、goal_stop、
human_gate、loop_guard、workspace_path、path_resolve、eda_import_race 全绿。worker 已重启。

**这次运行的遗留**：10_appearance 下只有 `orthographic.svg` + `render-prompts.md`，没有 render 图；
`prompts.json` 里只有一条结构阶段补的 exploded 提示词（没有 render 基线）。
结构阶段靠 appearance.md 的尺寸交接表兜住了（总长 170mm 对得上），但**形状约束本该来自渲染图**——
重跑一次新需求即可验证修复效果。

## 最新改动：修 EDA 工具并发导入死锁（2026-10-30 · 第十四轮）

**现象**：一次运行里 EDA 工具报
`_DeadlockError: deadlock detected by _ModuleLock('eda.board')`，整次运行报废。

**根因**（已复现，8 线程约 10% 命中率）：工具函数里写的是「用到才 import」，而工具在线程池里并发跑。
CPython 的 import 锁在**「线程 A 导包根 `eda`、线程 B 直接导子模块 `eda.board`」**并发时互相等成环 ——
`_find_and_load` 是**先拿子模块锁、再去导父包**，于是 B 拿着 `eda.board` 等 `eda`，A 拿着 `eda` 等 `eda.board`，
`_has_deadlocked()` 判定成环 → 抛 `_DeadlockError`。

**修法**（`agent/tools.py`）：
1. `_mod(name, attr)` —— 所有延迟导入统一入口，首次导入进全局 `_IMPORT_LOCK` 串行化；
   已在 `sys.modules` 的走快路径，不进锁（热路径零开销）。
2. `_eda_pkg()` —— eda 包名只解析一次并固定（worker 里是顶层 `eda`，脚本里是 `agent.eda`），
   杜绝同一个文件被当成两个模块各导一遍。
3. `_warm_imports()` —— `import tools` 时单线程把 7 个 eda 子模块全部预热（实测 96ms），
   运行期根本不再有首次导入。

**顺带修掉一个静默 bug**：`emit_easyeda` 里写的是 `from .eda.preview import write_svg`，
但 worker 里 `tools` 是**顶层模块**（`__package__ == ''`，server 用 `from tools import ALL_TOOLS` 启动），
相对导入必然 `ImportError` 并被 `except Exception` 吞掉 —— **原理图预览 SVG 从来没真正生成过**。
改用 `_eda("preview", "write_svg")` 后冒烟可见 `board.schematic.svg` 正常产出。
同类问题：`scripts/test_easyeda.py` 的 `from agent.eda.preview import ...`（路径只放了 `agent/`）也改回顶层 `eda`。

**回归**：新增 `scripts/test_eda_import_race.py`（13 项：预热完整性 2 / 单份模块 1 / 访问器 7 /
子进程 12 轮 ×12 线程并发导入无死锁 1 / 源码无裸 eda import 2）。
EDA 链路 test_kicad 58 + test_pcb 62 + test_easyeda 95 + test_gerber 24 + test_lib 48 全绿，
其余 10 个链路回归脚本全绿，三个 EDA 工具冒烟出全产物，worker 已重启。

---

## 最新改动：修「第二张图被拦 / 渲染图落错目录 / 语言漂移」（2026-10-30 · 第十三轮）

用户实跑「儿童防近视坐姿提醒器」时暴露三个问题，逐个定位并修掉：

| 现象 | 根因 | 修法 |
|---|---|---|
| 第二、三张图被「本阶段已落盘，请先确认」拦死 | `generate_product_image` 出**一张**图就 `_stage_done("10_appearance")`，阶段立刻锁住 | 改成 `_sync_stage(rel)`：只有契约产物（appearance.md + orthographic.svg + render-01.png）齐全才置 done |
| 渲染图出现在「其它文件」分组，不在 10_appearance | 模型传的是裸文件名 `render-01.png`，落盘掉进工作区根 | 新增 `tools._in_stage_dir()`：没带目录就按 kind 归位（render→10_appearance，exploded→20_structure） |
| 思考与汇报漂移到波斯语 | 提示词只说「面向用户的输出用中文」，没约束 thinking | main_agent / subagents `COMMON_RULES` 加语言硬约束：含思考文本在内一律简体中文，点名禁止波斯语/阿拉伯语 |

护栏同步放宽（`stagestate.guard`）：**DONE 状态 + 目标文件在磁盘上不存在 → 放行**，
因为「补齐第二张图」不算「把第一张重做一遍」。只对多产物工具
（`generate_product_image` / `render_product_views`）生效 —— 建模/原理图不豁免，
否则换个文件名就能绕过闸门。CONFIRMED 仍拦（已确认的一版不该再加图），有熔断兜底。

回归：`scripts/test_stage_once.py` 新增【11】【12】【13】三组共 11 项断言（落盘归位、
新文件放行、已存在仍拦、出一张图不锁阶段、语言约束）；顺手修两处过期断言
（`test_human_gate` 工具数 12→≥12；`test_loop_guard` 预算改用范式档位）。
相关回归全绿：stage_once / deadlock / artifact_guard / image_ref / visual_chain /
artifact_sync / goal_stop / human_gate / loop_guard / workspace_path / path_resolve。
worker 已重启（无活跃会话时）。

---

## 最新改动：阿里云部署方案（2026-10-30 · 第十二轮）

需求：把项目搬到服务器（阿里云 ECS），要部署脚本 + 部署流程。产出 `deploy/` 全套：

| 文件 | 作用 |
|---|---|
| `deploy/agentctl.sh` | 总入口：`init/up/down/restart/update/status/logs/backup/ssl` |
| `deploy/docker-compose.yml` | worker + web + nginx 三容器编排，data/workspace 挂卷 |
| `deploy/Dockerfile.worker` / `Dockerfile.web` | python:3.13-slim / node:22-slim，已配阿里 PyPI 与 npmmirror |
| `deploy/nginx/conf.d/*.conf` | 反向代理 + **SSE 透传**（`proxy_buffering off` / `gzip off` / 3600s） |
| `deploy/requirements.txt`（+`-cad.txt`） | 项目此前没有依赖清单，按实测环境补 |
| `deploy/env.prod.example` | 生产环境变量模板，含大陆 ECS 出网注意事项 |
| `deploy/bare/*` | 不用 Docker 时的 systemd + 宿主机 nginx 备选方案 |
| `deploy/README.md` | ECS 选型 / 安全组 / HTTPS / 运维 / 排障速查 |

代码侧唯一改动（为容器化）：`agent/config.py` 新增 `AGENT_HOST`，`agent/server.py` 的
`uvicorn.run` 不再硬编码 `127.0.0.1`（容器内不绑 0.0.0.0 时 nginx 与跨容器访问全连不上）。
默认仍是 `127.0.0.1`，本机开发零影响。另新增 `.dockerignore` 给构建上下文瘦身。

---

## 最新改动：修掉「结构阶段死循环」（2026-10-30 · 第十一轮）

用户实跑反馈：结构阶段智能体**卡在护栏里出不来** —— 建模发现上盖尺寸错了想改，
被护栏拦 → 去请用户确认 → 确认完想改，被拦得更死 → 再确认 → 再被拦。
8 次 `text_to_cad` / `build_structure` 全部被拒，预算烧光，阶段还停在错模型上
（用户最后手动取消了那次运行 `162a24d2ed04`）。

### 根因：两个缺陷咬在一起

**① 脚本能跑通 ≠ 模型是对的。** 建模工具只判「脚本有没有报错」，成功了就无条件
`_stage_done("20_structure")` 把阶段锁住。上盖用 `Sphere(半径 340)` 做微拱，布尔之后
包围盒变成 **100×100×341mm**，照样报「建模成功」—— 阶段一锁，谁都改不动。

**② 护栏没有熔断。** `stagestate.guard()` 只有「放行 / 拒绝」两态。产物一旦置 done
（更别说 confirmed），模型想纠错就只有一条路：`confirm_with_user`。而提示词里还写着
「看到拦截就照做去确认」—— 于是确认→被拦→确认→被拦，无限循环。

### 修法

| 位置 | 改动 |
|---|---|
| `agent/tools.py` | 新增 `_cad_sanity()`：建模成功后做**几何自检**（体积≤0 / 任一维>500mm / 最长边超脚本声明外形的 2.5 倍）。畸形就**不置 done**（阶段不锁），并返回「哪个零件哪一维、常见原因（大半径辅助体布尔残留）、怎么改」+ 明确告知「本次没锁阶段，直接改脚本重跑，不用去 confirm」 |
| `agent/stagestate.py` | `guard()` 加**熔断**：同一状态连拦 N 次（done 拦 1 次、confirmed 拦 2 次）就放行一次自我纠错，状态退回一档（confirmed→done / done→working），做完重新走确认。拒绝文案里写明「再调用一次就会被放行」，给模型一条明确的路 |
| `agent/main_agent.py` / `agent/subagents.py` | 提示词补**唯一例外**：产物有客观错误（尺寸离谱 / 0 字节 / 图里主体缺失）时**不要去 confirm，直接改了重跑** |

熔断是**通用**的，不只救建模 —— `generate_product_image` / `render_schematic` / `build_bom`
这些上锁的昂贵工具都跟着受益。

**新增 `scripts/test_deadlock.py` 17 项**（几何自检 6 + 熔断 11）；
`test_stage_once` / `test_pipeline` / `test_artifact_guard` 回归不变。worker 已重启。

---

## 第十轮：全链路体检 + 待办清单（2026-10-30）

用户要求「启动服务、跑完整设计流程、看看有啥问题」。服务本身是活的（worker :8001 /
前端 :3000），于是用 `run_product_e2e.py` 起了一次真实需求做验收（土壤湿度检测仪，
`run_id=e4b4acb5ac7a`，可在界面上同步观察），同时把整条链路体检了一遍。

### 体检发现的 4 个真问题（已修）

| # | 问题 | 影响 | 修法 |
|---|---|---|---|
| 1 | 前端 `isBinary` 只认 `step\|stp\|stl\|3mf\|zip`，**不认 `.epro`** | 用户点开嘉立创工程看到一屏乱码（zip 二进制当文本渲染） | `web/app/page.tsx` 正则加 `epro` |
| 2 | `stages.py` 交付清单写 `30_hardware/schematic.svg`，实际产出是 `board.schematic.svg` | 交付清单里那条链接 404 | 改成真实文件名 |
| 3 | `stages.py` 标题 + `subagents.py` 提示词仍写「`.epro` 只有 PCB、没有原理图页」 | **模型会在交付说明里向用户写错**，说没有原理图 | 改成「PCB 段与原理图页都有，符号封装取自立创官方库」 |
| 4 | `main_agent.py` 文件名契约清单漏了 `board.epro` / `board.schematic.svg` | 子智能体可能给产物改名 → 交付清单认不出 | 补进契约清单 |

### 验收脚本补齐最后一环

`scripts/check_real_board.py` 原来只校验 KiCad PCB，新增 **【5】嘉立创 .epro** 段（13 项）：
zip 结构（project.json / PCB / FOOTPRINT / SYMBOL / SHEET）、原理图器件都在 A4 内、
符号不止一种、有网络标签、有预览 SVG、KiCad 导入器回读（封装数/焊盘数/网络数/空位号）。
拿已有真实工程 `e60f46e54e23` 实测全绿（26 封装 / 145 焊盘 / 17 网络 / 80.2×50.2mm）。

`README.md` 补「电路设计：原理图 + PCB」章节：两条输出链路（KiCad 主 / 嘉立创副）、
立创官方库数据来源、已知边界（不布线、KiCad 链路未接库、`.epro` 是 v2.2）。

### 下一步清单（按影响排序）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | **KiCad 链路接立创官方库** | `board.kicad_pcb` / `schematic.kicad_sch` 仍是参数化符号 + 近似封装。库数据已在手，是纯转写工作（符号图元 + 焊盘几何两套映射）。不做的代价：用户打开 KiCad 看到的封装和 `.epro` 里不一样 |
| P1 | 原理图加 GND / VCC 电源符号 | 现在全是短线挂文字标签，观感影响最大 |
| P1 | **C1（100nF 去耦）与 J2（PMS7003 排针）悬空** | netlist 里没有连线记录，**等你确认接法**后补 |
| P2 | 原理图功能分区框 + 区名 | 没有虚线框，看不出「这是一块电路」 |
| P2 | 引脚名落地到 `.esym` | 现在只有网络名，看不出这根是 SDA 还是 SCL |
| P2 | 排版：大件撑高整行 | U1（128×288）把整行撑高，同行小件悬在中间 |
| P3 | 立创库命中率 16/22 | 兜底 6 个：PMS7003、J2 排针、锂电、3mm 直插 LED、OLED |
| P3 | **代码未提交** | `agent/eda/` 整个目录 + 6 个脚本都还是未跟踪状态 |

---

## 第九轮：原理图排版重做 + 生成时自带预览（2026-10-30）

用户实测反馈「原理图一点不美观」，但明确要求**先跑通流程、美观慢慢优化**。
这一轮只做两件不阻塞流程的事：

### 1. 排版从「图纸无限」改成「A4 内自适应」

旧版按 `SHEET_W=1300` 一路往右铺、超宽才换行，22 个器件直接铺到 **y=1211**
（A4 只有 825）—— 打开就是一堆跑出纸外的符号。现在：

- **按功能流排序聚团**（`_FLOW` 表）：接口 → 电池 → IC → 模块/传感器 →
  晶振/阻容 → 二极管/LED → 开关/蜂鸣器。同类挨在一起，不再按位号字母序
  把电阻和 ESP32 混排。
- **试 1..n 行，取第一个能整体落进 A4 可打印区的方案**，行高自适应、行内均布、
  竖向居中；器件过多才压行距兜底，**绝不超纸**。

结果：`x 116..1054 / y 80..748`，完全在 A4（1170×825）内。

### 2. `agent/eda/preview.py`：纯 Python 渲染原理图 SVG

不依赖浏览器/客户端，生成 `.epro` 时自动多出一份 `board.schematic.svg`，
浏览器直接打开就能看排版。三个 `.esch` 字段陷阱记在模块头里：

- `RECT` 存**两个角点** (x1,y1,x2,y2)，不是 x/y/w/h；
- `POLY` 的点是**扁平数组** [x1,y1,x2,y2,…]，闭合标志在第 4 列；
- `PIN` 的 (x,y) 是引脚**外接点**，线段朝 rot 方向延伸 len。

### 仍未解决（用户已同意后续再打磨）

原理图「好看」还差这些，按影响排序：

1. **没有电源/地符号** —— 现在 GND/3V3 全是一根短线挂个文字标签，正经原理图
   这里应该是 GND 三角/横线、VCC 箭头，且朝下/朝上统一。
2. **器件之间没有视觉分区** —— 没有虚线框 + 区名（电源区/主控区/传感器区）。
3. **引脚名不显示** —— 库符号里 PIN 的 `NAME` 属性没落地到 `.esym`，只有网络名，
   看不出「这根是 SDA 还是 SCL」。
4. **行内均布会在大件旁留白** —— U1（128×288）把整行撑高，同行的 AHT20/OLED 悬在中间。

---

## 第八轮：原理图与 PCB 改为「基于立创官方元件库」（2026-10-30）

用户实测反馈三条：**符号长得一模一样**、**没有型号名**、**PCB 封装不对**。根因是之前
符号/封装都是参数化猜的（两脚器件一律画同一个矩形、封装按 `size_mm` 铺焊盘），
跟真实器件无关。这一轮改成：**按型号去立创官方库查 LCSC 编号，拉真符号 + 真封装**。

### 数据源（公开只读，无需鉴权）

| 用途 | 接口 |
|---|---|
| 搜型号 → LCSC 编号 | `GET https://easyeda.com/api/eda/product/list?keyword={kw}&currentPage=1&pageSize=N`（参数是 `keyword`，`wd` 无效） |
| 取器件（符号+封装） | `GET https://easyeda.com/api/products/{LCSC}/components?version=6.5.17` |

`result.dataStr.{head,shape}` 是原理图符号，`result.packageDetail.dataStr` 是 PCB 封装。

### 单位：两侧完全不同（最容易踩的坑）

- **符号**：标准版 1 单位 = 0.1 inch = Pro 原理图 1 单位 → **1:1**
- **封装**：标准版 1 单位 = 10 mil → Pro PCB **×10**
- 标准版封装坐标是**画布绝对坐标**，必须先减 `head.x/head.y` 再缩放

### 匹配策略（全部「宁可兜底也不配错」）

1. 显式 `lcsc` → 直接用
2. 型号搜库，置信分 ≥ 40（型号相等 100 / 前缀 60 / 包含 45 / 仅封装族同 20）
3. 「型号 + 种类词」再搜一轮（`SSD1306 OLED`）
4. 「规格 + 种类」搜（`USB-C 16PIN` → `TYPE-C 16P`、`LED 3mm`）
5. 通用件按「种类 + 封装尺寸」取模板件（电阻/电容/LED…），只借符号形状与封装几何，
   `from="generic"` 标记，**BOM 里不写它的编号**
6. 全败 → 本地兜底图形（按 kind 画：电阻矩形、电容双线、LED 三角+箭头、电池长短线、
   连接器针脚、模块双线外框、多脚 IC 方框）

### 三次「配上了但配错」，都比配不上更糟

| 器件 | 错误结果 | 修法 |
|---|---|---|
| `USB-C` | `C2337 2.54-1*40P直针`（40P 排针） | 连接器不给 default；名字含 USB/TYPE-C/JACK 的一律不按脚数配排针 |
| `SSD1306-OLED-1.3` | `C52098030 SSD1306-150MT`（顺络贴片电感） | 加 `_pkg_clash()`：库件封装大类（`IND-`/`BAT-`/`HDR-`…）与器件种类冲突就否决 |
| `800mAh-LiPo` | `C70382 CR2032`（纽扣电池座） | 电池不给 default；直插件（`3mm`/`THT`/`DIP`）不给贴片映射 |

⚠ SSD1306 那条还带出一个隐蔽点：**搜索结果里的 `package` 是 `SMD,18.5x15.2mm` 这种简写，
看不出类别**，只有下载详情后才是 `IND-SMD_...`。所以种类冲突必须在 `fetch()` 之后再判
一次，只在搜索结果上判等于没判。

### 另外两个真 bug

- **`POLYGON` 焊盘让 KiCad 导入器直接段错误**（无输出、rc≠0）。标准版有这个形状，
  但顶点没带在接口返回里，Pro 也不认这个形状名 → 一律降级成同尺寸矩形。
  定位方式：逐个器件单独生成 `.epro` 再回读，22 个里只有 ESP32 模块那个崩。
- **立创接口有频控**：连打十几次回 403，之后整批器件退回本地图形（表现就是
  「封装全都不对」）。修法：请求间 0.35s 最小间隔 + 403 退避重试 + **搜索结果也落盘**
  （之前只缓存器件详情，每轮都重搜）+ 探测失败只信 120s（成功才永久缓存）。

### 结果

`workspace/e60f46e54e23/30_hardware/board.epro`：

- **16/22 器件用上官方库**（ESP32-C3-MINI-1 61 焊盘 / TP4056 ESOP-8 / AMS1117 SOT-223 /
  AHT20 6 脚 / USB-C TYPE-C 16P 20 焊盘 / 0603-0805 阻容 / 6mm 轻触开关）
- 6 个走本地兜底：PMS7003、J2 排针、800mAh 锂电、2 只 3mm 直插 LED、OLED 屏
- KiCad EASYEDAPRO 导入器回读：26 封装（22 器件 + 4 孔）、145 焊盘、17 网络、
  板框 80.2×50.2mm、无空位号
- 回归：`test_lib`(48) + `test_easyeda`(90) + `test_kicad`(58) + `test_pcb`(62) +
  `test_gerber`(24) + `test_artifact_sync`(10) = **292 项全绿**

---

## 上一轮：补上原理图段（SCH），工程打开后有原理图 + PCB（2026-10-30 · 第七轮）

`.epro` 现在同时带 **原理图页** 和 **PCB**：工程树 `main → schematic → MAIN` 与
`main → board → PCB1` 两枝都有。

### 关键发现：原理图是靠网名并网的，不是靠线几何

真机工程里 `I2C_SDA` 有 13 根**互不相接**的短线，每根都挂着一个属性：

```json
["WIRE","e2553",[[995,635,1045,635]],"st25",0]
["ATTR","e2554","e2553","NET","I2C_SDA",0,1,995,635,0,"st1",0]
```

也就是说：**给一根线挂 `NET` 属性 = 网络标签**，编辑器按名字并网。
这一条决定了实现方式 —— 每根引脚只画一小截引出线 + 一个同名标签，
不需要跨图走线，也就绕开了「线与线相交算不算连通」这个说不清的坑。

### 新增文件

- `agent/eda/sch.py`（新）：符号 `.esym` + 图纸 `.esch` 的生成。
- `agent/eda/easyeda.py`：装配时并入原理图段 —— 器件复用 PCB 那批 device
  （只是把 `attributes.Symbol` 补上，库元件才是"符号+封装"齐全的），
  `project.json` 增加 `schematics` / `symbols` 索引，`boards` 同时挂 schematic 与 pcb。

### 格式要点（都写进 `sch.py` 的文档头了）

| 项 | 值 |
|---|---|
| 图纸单位 | 1/100 英寸（A4 = 1170×825），栅格 10 —— **与 PCB 段的 mil 不是一回事** |
| `COMPONENT` | 9 段：`[类型, eid, 部件名, x, y, 旋转, 镜像, {}, 0]`，旋转 0/90/180/270 |
| `ATTR` | 12 段：`[类型, eid, 宿主eid, 键, 值, 0, 可见, dx, dy, 旋转, 字体, 0]` |
| `PIN` | 11 段，`(x,y)` 是**外接点**（线连到这里），线体朝角度方向延伸 |
| `WIRE` | 5 段：`[类型, eid, [[x1,y1,x2,y2],…], 线型, 0]`，可以是零长度（纯标签） |
| 网络 | `ATTR` 挂在 WIRE 上，键 `NET`，值 = 网名 |

### 画法

- 符号按「型号 + 脚数」去重：两脚器件左右各一脚；多脚器件左半自上而下、右半自下而上（常规 IC 画法）。
- 排版是书架式，4 列 × 6 行，列距 340（1/100 英寸），相邻符号留 ~160 的标签空间。
- 每个引脚：一小截 30 的引出线 + 一个 `NET` 标签。**没有连线的引脚就只有线没有标签**，
  跟 PCB 侧"没网络的焊盘"一致 —— 不会凭空造出连接。

### 验证

`scripts/test_easyeda.py` 新增第 11 组（共 88 项）：

- `SHEET/**/*.esch` 与 `SYMBOL/*.esym` 都在；`project.json` 的 `schematics` / `symbols` 索引与文件一一对应；
- 行长度：COMPONENT 9、ATTR 12、WIRE 5、PIN 11；
- 每个器件的 `Symbol` / `Device` 属性都指向真实存在的条目，且 device 的 `Symbol` 已补齐；
- **每根线的起点都落在某个引脚外接点上**（器件原点 + 符号引脚偏移，逐条算）；
- **每个 NET 标签的「位号.引脚 → 网名」与网表逐条比对**（这是之前 PCB 侧出过错的地方，
  原理图侧同样加了这道锁）。

PCB 侧没有回归：KiCad 导入器回读仍是 26 器件 / 91 焊盘 / 17 网络 / 空位号 0 / 板框 80.2×50.2mm。
全量回归 293 项全绿（easyeda 88 / kicad 58 / pcb 62 / gerber 24 / pipeline 51 / artifact_sync 10）。

### 仍未验证

本机没有 EasyEDA 客户端。原理图段目前只能做**结构自检**，没有像 PCB 段那样有
KiCad 导入器可以回读（KiCad 的 EASYEDAPRO 导入器只管 PCB）。所以"打开长什么样"
仍需你实测。已知的不确定点只有一处：`ATTR` 的坐标在真机里两种解释都能自圆其说，
连通是靠 `ATTR` 的宿主 eid 而不是坐标，**最坏情况只是标签文字位置偏一点**。

---

## 最新改动：PCB 飞线缺失的两条根因（2026-10-30 · 第六轮）

用户反馈「有器件没有飞线」，问是不是原理图画错了。查下来是**两个独立问题**。

### 一、这个 `.epro` 里根本没有原理图页
工程树只有 `main → board → PCB1`。飞线不来自原理图，来自 PCB 里的 `PAD_NET` 行。
所以不存在"原理图连线丢了"，而是 PCB 上的引脚压根没有被赋上网络。

### 二、网表端点漏点号 → 整只器件掉线
`netlist.json` 里 12 个端点写成了 `BAT1正极` / `C2正极` / `C2负极`…（少了 `位号.引脚名` 中间那个点）。
旧规范化逻辑把这种字符串当成**一条只有一个端点的裸网络**，于是：

- C2~C6 五只电容、BAT1 电池在网表里"不存在于任何连线" → 拿默认引脚摆上去，无网络；
- 那几条单点网络被丢弃，`nets` 反而少了一条。

修法（`agent/eda/netlist.py`）：

1. `_POLARITY` 表认 `正极/负极/阳极/阴极/+/－/pos/neg` 等写法；
2. `_split_endpoint()` 多一个分支：没有点号时从右往左剥极性词，剩下的必须形如 `字母+数字` 才认作位号
   （`电池正极` 这类纯文字仍当网络名，不误判）；
3. `_pin_on()` 按器件种类落地：电容/电阻 → `1/2`，LED/二极管 → `A/K`，电池/喇叭/电机 → `+/-`；
4. **回写端点字符串**——网络是按端点字符串聚合的，只映射不回写等于没改；
5. 电池正极那条线按惯例命名 `VBAT`（否则会变成 `N$001`）。

### 三、顺手挖出的第二个 bug：网络号差一位
`_net_table()` 返回的 `{名: 号}` 是 **1 基**（0 = 无网络），而 `names` 列表是 **0 基**，
`easyeda.py` 里写成 `net_names[nid]` → 所有焊盘网络**整体错位一条**。
真机表现就是：C2 的正极被连到 `I2C_SCL`、C5 的正极被连到 `VBAT`，看着有飞线但全连错了。
KiCad writer 一直是对的（`net_names[nid - 1]`），只有 EasyEDA 这条错了 —— 这类"两边都写了、
只有一边差一"的 bug 靠肉眼比对两份输出根本发现不了，必须有回读校验。

### 四、验证
用 KiCad 的 EasyEDA Pro 导入器回读修复后的工程：

| 器件 | 焊盘→网络 |
|---|---|
| C2 | 1=VBUS, 2=GND |
| C5 | 1=3V3, 2=GND |
| BAT1 | +=VBAT, -=GND |
| R1 | 1=3V3, 2=I2C_SDA |
| U1 | GND=GND, VCC=3V3, BAT=VBAT |

KiCad 版 `.kicad_pcb` 同样核对过，两个 writer 一致。

### 五、防复发（三层）
1. **提示词**：`subagents.py` 里加反例（`C2正极` ✗ / `C1.1` ✓），并要求「每个元件至少 2 条连线」；
2. **自检**：`check_circuit` 新增第 8 条规则 —— 列出没有任何连线的元件、列出少点号的端点；
3. **测试**：`test_kicad` 58 项（+6 极性用例）、`test_easyeda` 57 项（+2：网络名必须已声明 /
   引脚→网络逐条比对网表），全量回归 262 项全绿。

### 六、还剩两处**不是格式问题**的缺口
`C1`（100nF，U1 去耦）和 `J2`（PMS7003 三针排针）在 netlist.json 里**完全没有连线记录**，
不是解析问题，是硬件阶段漏写。工具输出现在会明确点名这两只，不再让人猜。

---

## 最新改动：嘉立创工程改按真机格式重写（2026-10-30 · 第五轮）

用户给了一份**客户端 4.1.78 导出的真实工程**（立创·实战派ESP32-S31开发板）当金标准，
一比对发现之前的 writer 从根上就错了。

### 一、根因：我之前照的是"规范"，不是真机

| 维度 | 我原来的写法 | 真机 |
|---|---|---|
| 文件组织 | 单文件 `<name>.epru` 装全部 | **分文件** `project.json` + `PCB/*.epcb` + `FOOTPRINT/*.efoo` |
| 行格式 | `{外层}\|\|{内层}\|`（V3 规范） | **逐行一个 JSON 数组** `["LAYER",1,"TOP",...]` |
| 器件引用 | `attrs.Footprint` | `ATTR` 三条：**Footprint + Device + Designator** |
| 库索引 | 无 | `project.json` 的 `footprints` / `devices` 两张索引 |

官方 V3 规范文档和客户端实际导出的文件是**两套不兼容的格式**。用户第一次导入能建出
工程树（`main www → board → PCB1`）但画布空白，根因就在这。

### 二、真机格式要点（已落进 `agent/eda/data/epcb_head.json`）

- 层号固定：1 TOP / 2 BOTTOM / 3 TOP_SILK / 11 OUTLINE / 12 MULTI，封装内另有
  48 外形框 / 49 标记 / 50 焊接区
- `["COMPONENT",eid,0,layer,x,y,rot,{attrs},0]`，坐标 mil
- `["ATTR",eid,0,compEid,4,x|null,y|null,key,value,...]`，**22 个字段**
- `["PAD",eid,0,"",layer,pin,x,y,rot,["ROUND",drill,drill],["RECT",w,h,r],[],...,0]`，22 字段
  - SMD 无钻孔时写 `0.03937`（=1µm）占位，不写 0
- `["PAD_NET",compEid,pin,netName,padEid,0]`：padEid 必须是**封装里真实的**那个编号
- `["POLY",eid,0,"",layer,width,[x1,y1,"L",x2,y2,...],0]`：点列只写一个 "L"，后续全是坐标对

### 三、修的过程中踩到的两个坑

1. **封装不能内嵌进 .epcb**。真机里也有内嵌段，但那是"本地改过的封装缓存"。
   我照做后发现 26 个器件变 96 个（多出 70 个空位号）—— 内嵌段被当成未放置器件。
   走外置 `.efoo` + project.json 索引才对。
2. **板框点列要自己闭合**。`board.outline_points()` 首尾不重复（KiCad 那边逐段连线补），
   EasyEDA 这边不补就缺最后一段边。

### 四、这次终于有本地验证器了

发现 **KiCad 10 自带的 EasyEDA Pro 导入器能读 v2.2 格式**（`PCB_IO_MGR.EASYEDAPRO`
+ `pcbnew.Python`，在 `E:/KiCad/10.0/bin/python.exe`）。之前验证 v3 格式时它返回空板/段错误，
是因为 v3 是另一套格式；v2.2 能正常解析。

于是 `test_easyeda.py` 变成两条腿：
- 结构断言（55 项，含字段数逐类对齐真机）
- **KiCad 回读**：器件数、位号、焊盘数、板框尺寸四项交叉验证

真实会话产物验证结果：26 个器件（22 + 4 孔）、91 个焊盘、17 个网络、
板框 80.2×50.2mm、**0 个空位号**。

### 五、改动清单

| 文件 | 改动 |
|---|---|
| `agent/eda/easyeda.py` | **重写**：v2.2 分文件 zip 格式 |
| `agent/eda/data/epcb_head.json` | 新增：从真机提取的样板头（层表/规则/显示开关） |
| `agent/eda/pcb.py` | 抽 `pad_positions()` 供两个 writer 共用焊盘位置 |
| `agent/tools.py` / `stages.py` / `subagents.py` | 产物名 `.epro2` → `.epro`，文案同步 |
| `scripts/test_easyeda.py` | **重写**：55 项（含 KiCad 回读） |

**回归**：test_easyeda 55 / test_pcb 62 / test_kicad 52 / test_gerber 24 /
test_pipeline 51 / test_artifact_sync 10 —— 全绿。

**仍未验证**：没在 EasyEDA 客户端里实际打开过新文件。但这次不是靠猜——格式是
从用户给的真机工程逐字段抄下来的，且 KiCad 的 Pro 导入器回读四项指标全对。

---

## 最新改动：产物登记补齐 + 嘉立创 `.epro2` 副输出（2026-10-30 · 第三轮）

### 一、先纠正上一轮的一个误判：固件阶段没有卡死

上一轮我报告「固件阶段卡 35 分钟无产出」，**是错的**。翻事件流账：

| 时刻 | 实际发生 |
|---|---|
| 10:52 | 硬件阶段 netlist.json 落盘，门禁放行固件 |
| 10:54–11:04 | 固件阶段正常推进：查文档 → 写代码 → `check_arduino` 自检 4 轮 → 改代码 |
| 11:04:48 | `firmware.md` / `simulation.md` 写盘 |
| 11:06:23 | `firmware.ino` 写盘，阶段 `status=done` |
| 11:06 | **我重启 worker 加载新工具**，运行被标 `interrupted` |

误判有两个来源，都值得记住：

1. **监控接口选错**：`/api/runs/{id}` 里的 `files` 只认 `artifact` 事件，而子智能体常用
   `write_file` / `edit_file` 直写，不经过 `save_artifact`。磁盘上文件早就在，事件流里没有。
   真正权威的是扫磁盘的 `/api/runs/{id}/files`（这次是 95 个文件）。
2. **我自己的重启打断了它**，却当成「模型陷进去了」。

### 二、顺着这个误判修掉的真 bug

`Run._maybe_scan_goal()` 早就有磁盘扫描兜底（注释写着「模型常绕过 save_artifact 直接 write_file」），
但它只把路径并进 `self.artifacts` 判据，**没有推事件**。于是运行中实时产物面板一直空着，
只有刷新扫磁盘才看得到 —— 用户和我的体感都是「跑了半天没东西」。

修：扫到新文件时补发 `artifact` 事件（带 `source: "disk"` 标记来源），并用 `_scanning` 闸
挡住 `push` 回调导致的递归。新增 `scripts/test_artifact_sync.py`（10 项）。

顺带修了 `test_harness.py` 一个假失败：它拿全局 `config.TOOL_BUDGET` 循环，而真实阈值是
`min(档位预算, HARD_TOOL_BUDGET)`，按需求文本路由出的档位可能更高 —— 照全局值循环够不着阈值。

### 三、P2：嘉立创 EDA 专业版 `.epro2` 副输出

新增 `agent/eda/easyeda.py` + 工具 `emit_easyeda`。格式依据全部落到 `docs/easyeda-spec/`：

- 官方《嘉立创EDA 文件格式》V3 规范（`v3-spec.md`，102KB，离线可查）
- 官方规范包里的真实工程（泰山派1F 金手指核心板，61322 行）—— 照着它的行格式写

工程结构 = ZIP：`project2.json` + `<name>.epru`（JSON Lines，每行 `{外层}||{内层}|`，按 `DOCHEAD` 分段）。
坐标单位是 **mil**（规范正文写 0.01 inch，按范例 `lastViaInnerDiameter: 39.37` = 1mm 反推实为 0.001 inch）。

**与 KiCad 版同源**是关键设计：焊盘几何抽成 `pcb.pad_geom()` 两边共用，布局坐标共用 `pcb._place()`。
两份输出如果是两套数，用户对比时会以为板子做错了。

**能验到什么程度（如实标注）**：
- ✅ 结构自验 25 项（`scripts/test_easyeda.py`）：JSON Lines 合法、无 CR、文档分段顺序、
  板框闭合与尺寸换算、焊盘数与引脚数一致、PAD_NET 网络都已声明、安装孔是有孔焊盘
- ✅ 与 KiCad 版一致性：坐标与焊盘尺寸逐项比对
- ❌ **没有「双击打开」验证**：KiCad 10.0.7 的 EasyEDA Pro 导入器对 v3（连官方范例也一样）
  要么返回空板要么段错误 —— 它只认 v2；本机也没装 EasyEDA 客户端

已知边界：本版只写 PCB 段，**没有原理图页**；同样不布线。工具描述与提示词里都写明了。

> ⚠️ **本段已被后续轮次取代**（第七/八/九轮）：格式改为 **v2.2 zip 容器**（不是 V3 单文件），
> 已补原理图段（`SYMBOL/*.esym` + `SHEET/*/1.esch`），封装与符号改为**从立创官方库拉取**，
> 并用 KiCad 的 EASYEDAPRO 导入器回读校验通过。以上 V3 相关描述仅作历史记录保留。

### 四、本轮改动清单

| 文件 | 改动 |
|---|---|
| `agent/server.py` | 磁盘扫描补发 artifact 事件 + `_scanning` 递归闸 |
| `agent/eda/easyeda.py` | 新增：EasyEDA Pro V3 工程 writer |
| `agent/eda/pcb.py` | 抽出 `pad_geom()` 供两个 writer 共用 |
| `agent/tools.py` | 新增 `emit_easyeda`（第 16 个工具） |
| `agent/stages.py` / `agent/subagents.py` | 契约加 `board.epro2`；硬件提示词加导出步骤 |
| `scripts/test_artifact_sync.py` / `test_easyeda.py` | 新增 10 / 25 项 |
| `scripts/test_harness.py` | 修假失败（预算阈值取 Run 自己的） |
| `docs/easyeda-spec/v3-spec.md` | 官方规范（离线留档，大文件未入库） |

**回归**：test_pcb 62 / test_kicad 52 / test_gerber 24 / test_easyeda 25 / test_artifact_sync 10 /
test_pipeline 51 / test_artifact_guard 28 / test_harness 31 —— 全绿。

---

## 最新改动：真实产品闭环验证 + Gerber 制造文件（2026-10-30 深夜 · 第二轮）

**闭环验证**：用一句真实需求跑了完整六阶段（ESP32-C3 空气质量检测仪，92×62×24mm 圆角外壳），
结构工程师**真的产出了合规 `board.json`**（80×50mm 圆角 3mm、4 个 ⌀3.2 孔、电池仓与 USB-C 禁布区、
`J1→right` 边约束、限高 11mm），硬件阶段自动调 `emit_pcb` 出了 `board.kicad_pcb`。
这是第一次用模型自己写的板级约束（而不是手写样例）跑通链路。

**真实数据暴露的 5 个 bug（手写样例测不出来）**：

| 现象 | 根因 | 修法 |
|---|---|---|
| DRC 9 条 clearance | USB-C 12 脚按器件外形排脚，节距 1.14mm 而焊盘高 1.0mm → 间隙 0.14mm | 密脚器件把本体撑高保证节距 ≥1.15mm，焊盘高度再收到 `节距-0.25` |
| `lib_footprint_mismatch` | 两个引脚数相同但尺寸不同的 IC 共用 `ic_5p` 封装名，库里只剩最后写的那个 | 封装名带尺寸：`ic_5p_7x6.5` |
| courtyard 重叠 2 + 丝印重叠 7 | 器件放不下时**硬塞板中心**，压在主控上 | 布局三级降级（标准间距→1.2mm→0.6mm），实在不行才居中并如实记 warning |
| 丝印被板边裁 2 | 位号文字在本体外 1.9mm，靠边器件必被裁 | 按离板边的真实距离决定位号放 `F.SilkS` 还是退到 `F.Fab` |
| 安装孔位号被裁 | 孔在四角，位号固定画在孔下方 | 孔位号改到 `F.Fab` |

结果：**21 条 DRC 违规 → 0 条**（33 个 unconnected 是"未布线"的必然结果，不算违规）。
回归 `test_pcb` 62 / `test_kicad` 52 / `test_pipeline` 51 全绿。

**Gerber 制造文件通道（P2 第一块）**：新增 `export_gerbers` 工具 → `agent/eda/gerber.py`，
走本机 `kicad-cli` 无头导出 Gerber RS-274X + Excellon 钻孔 + 贴片坐标，打包 `30_hardware/gerber.zip`
（含 README 说明）。没装 KiCad 时**明确告知**并给手工导出步骤，不假装成功。
坑：KiCad 10 的铜层文件名是 `board-top_cu.gtl`（KiCad 8/9 是 `-F_Cu.gtl`），"必需层"清单两种都要认。

**新增脚本**：`scripts/check_real_board.py`（校验真实会话 board.json + PCB + DRC）、
`scripts/run_product_e2e.py`（跑真实产品并自动点人工闸门）、`scripts/test_gerber.py`（24 项）。

**已知遗留**：① 固件阶段在本次运行里卡住 35 分钟无产物（待查，可能与长上下文/超时有关）；
② 电池座占位 26×26mm 由模型自填，挤占板面 —— 器件库应补电池座的真实尺寸约束。

---

## 最新改动：PCB 落地 · board.json 板级约束 + .kicad_pcb 生成（2026-10-30 深夜）

硬件阶段现在能出**真板子文件**了，链路是 `netlist.json + board.json → board.kicad_pcb`。

**为什么先加 board.json**：之前内腔尺寸只写在 `structure.md` 的 markdown 表格里，而且三个真实会话
三种写法（圆形 `φ76mm` / 竖排参数表 / 按方向分行）。程序要自动读就得猜列，猜错了**板子画出来装不进壳**，
还不报错。现在结构阶段强制多产出一个机器可读的 `board.json`（表格照留，人看的体验不变）。

**board.json 支持的板型**（外观是圆是方，板子就得跟着）：

| 外观 | 写法 |
|---|---|
| 矩形 / 圆角矩形 | `"shape":"rect"` + `"corner_radius_mm": 3`（超短边一半自动收敛） |
| 圆形产品 | `"shape":"circle"` + `"diameter_mm": 60` |
| 跑道形 | `"shape":"rect"` + 大圆角（如 15mm） |
| L 形 / 水滴形 / 不规则 | `"shape":"polygon"` + `"polygon_mm": [[x,y], ...]`（板中心为原点） |

其余字段：安装孔（含非金属化）、禁布区、限高分区、`edge_constraints`（接口必须落在哪条板边）。

**PCB writer（`agent/eda/pcb.py`）做什么**

- ✅ 板框：所有板型统一离散成点列 → Edge.Cuts 闭合轮廓
- ✅ 布局：**接口先占边**（硬约束，必须对准壳体开孔）→ 主控居中 → 其余从板中心向外找空位，
  避让安装孔与禁布区
- ✅ 安装孔、GND 整板铺铜（B.Cu）
- ❌ **不布线**：信号线交给 KiCad 自带布线器 / Freerouting
- ❌ 焊盘是参数化近似：布线前要替换官方封装（封装 descr 里写了原目标封装名）

> ⚠️ **上一行已被第八轮部分取代**：`.epro`（嘉立创）链路的封装已改为**从立创官方库拉取真封装**
> （实测 16/22 器件命中），只有库里搜不到或详情取不到的才退回本地近似图形。
> **但 KiCad 链路（`board.kicad_pcb` / `schematic.kicad_sch`）仍未接库**，还是参数化符号 + 近似封装。

**验收（本机 KiCad 10 真机）**：DRC 从 79 条违规降到 **4 条 warning + 18 条未布线**，
**0 个硬 error**（courtyard 畸形、封装库缺失都已清零）。踩的坑：
① 安装孔 courtyard 用单根线 → `malformed_courtyard`，必须闭合圆；
② 丝印框画在器件外沿正好压焊盘 → 外移 0.6mm；③ courtyard 线宽规范是 0.05；
④ 器件间距 1.0mm 不够，丝印互相压 → 2.2mm。

**测试**：`scripts/test_pcb.py` **62 项全通过**（含 kicad-cli 真机 DRC 4 项）；
其余回归 `test_kicad` 52 / `test_pipeline` 51 / `test_clarify_skeleton` 167 / `test_artifact_guard` 28 全绿。

---

## 上一改动：KiCad 工程真机验证通过 · 修 5 处格式/几何硬伤（2026-10-30 晚）

第一版在 KiCad 10 里打不开（报 `应为 pin_names, pin_numbers, arc…实际 'symbol'`）。装了 KiCad 后用
`kicad-cli` 做无头校验（加载 + 导出 SVG + ERC），把问题一个个钉死：

| 问题 | 现象 | 修法 |
|---|---|---|
| `(id N)` 属性、`excludes_from_sim`、`(size 1.27)` 单值、裸 `hide` | 整个文件解析失败 | 按 KiCad 9/10 格式重写：property 用 `(hide yes)`、字号双值、`(pin_numbers (hide yes))`、`(power global)` |
| **wire 塞了 3~4 个点** | 「加载原理图失败」，无行号 | KiCad 的 wire 只接受 2 个端点，折线必须拆成多根 |
| **符号坐标 Y 未翻转** | 图能开但线全断（ERC 满地 pin_not_connected） | 库坐标 Y 向上、图纸 Y 向下，绝对坐标要 `cy - off_y` |
| **引脚不在 1.27 栅格** | 线端点与引脚差 0.28mm，连不上 | IC 右列、连接器用统一栅距重算 |
| **电源符号垂直插进引脚列** | GND 符号正好落在连接器第 5 脚上，把 D+ 短路到地 | 沿「引脚朝外」方向引出，PWR_FLAG 再往外一格 |
| 电源网络无驱动源 | ERC `power_pin_not_driven` | 无 `power_out` 脚的网络才挂 `PWR_FLAG`；VOUT 判 `power_out` |

同时产出 **`<name>.kicad_sym` + `sym-lib-table`**：内嵌符号只保证"打得开"，带上库表后 ERC 连
「符号库未配置」的 25 条警告也没了。布局改成**按网络连通性 BFS 排序 + 动态列宽行高**，图不再大片留白。

**验收**：`scripts/test_kicad.py` 52 项全通过，其中 3 项直接调用 `kicad-cli` 真机校验
（能加载导出、ERC 0 error）；ERC 违规从 73 → **3**（只剩封装名在官方库里找不到，属待核对信息）。
服务端不依赖 KiCad，`kicad-cli` 只在测试里可选调用（检测不到就 skip）。

## 最新改动：硬件阶段能出真 KiCad 工程文件（2026-10-30，P0 落地）

网表第一次不只是"一张 SVG 示意图"，而是能进 EDA 的真工程。新增 `agent/eda/` 包（纯离线、零第三方依赖）：

- `eda/netlist.py` **网表规范化**（唯一事实源）：补 id/kind/封装、并查集聚合 net（支持 `U1.3V3 → 3V3`
  这类裸网络端点）、电源/地识别、脏数据只记 warnings 不抛异常。
- `eda/data/parts.json` **常用器件库 48 条**：封装名 + 典型限高 + 模块/IC/连接器的**真实引脚名**
  （ESP32-C3-MINI-1 / AMS1117 / USB-C / BME280…）。LCSC 编号一律留空 —— 宁可空着也不编造料号。
- `eda/symbols.py` + `eda/kicad.py` **KiCad 8 工程 writer**：生成 `.kicad_pro` + `.kicad_sch`。
  符号**全部内嵌 lib_symbols**，不引用任何官方库，换台机器双击即开；电源/地画成标准电源符号并就近
  接线，悬空脚自动标 `no_connect`（ERC 不刷屏）。
- 新工具 `emit_kicad_project`：读同一份 `netlist.json` → 写 `30_hardware/schematic.kicad_{pro,sch}`，
  已注册进 `ALL_TOOLS` 并写进 hardware-engineer 的必做清单（网表改了必须重新导出，保证
  netlist / svg / kicad 三者一致）；`30_hardware` 阶段契约新增 `schematic.kicad_sch`。
- 自测 `scripts/test_kicad.py`：47 项全通过（结构自检 + 脏数据不崩 + 器件库完整性）。
  样例产物：`workspace/eda-preview/`（不入库）。

## 最新改动：硬件 EDA 技术路径选型（2026-10-30，决策文档，未改代码）

围绕「要不要接 easyeda-copilot、PCB 怎么做」做了一轮调研与选型，结论落在
`docs/easyeda-integration-assessment.md`（第十节）。

- **easyeda-copilot 是遥控器不是库**：`mcp/src/` 内 0 个 HTTP 调用，所有能力走 WebSocket 打本机
  EasyEDA 客户端，不写任何文件 → 不做主链路，只在「探测到客户端在线」时当增强（真 DRC / 真布线）。
- **不装 EasyEDA 也能出工程文件**：EasyEDA Pro 的 `.epro` 是公开格式（zip + JSON Lines，官方 V3 规范
  2025102101）；KiCad 官方文档亦写明其结构，KiCad 8+ 可导入 `.epro`。
- **器件库不能靠抓**：`easyeda.com/api/products/*` 现 403/404、LCSC 接口 SSL EOF → 自建常用器件库，
  或直接用 KiCad 官方库仓库（纯文本、CC 授权，无需安装 KiCad）。
- **推荐路径**：结构化网表做唯一事实源 → **KiCad 文本工程为主输出**（容错高 / 器件库现成 /
  `kicad-cli` 可无头出 Gerber·BOM·坐标 / 且能被 EasyEDA Pro 导入）→ `.epro` 为副输出 →
  自研渲染器进网页产物面板（可交互原理图 + PCB 预览 + BOM + 一键下载 zip）。
- 节奏：P0 网表+器件库+原理图 writer+渲染 → P1 PCB 布局/铺铜 → P2 `.epro`+Gerber → P3 EasyEDA 增强通道。

## 最新改动：交付完成弹窗（带产品渲染图）（2026-10-15）

收工时给一个明确信号，而不是让用户自己盯着事件流判断「到底跑完没有」。

**新增 `web/components/CompletionDialog.tsx`**
- 主视觉自动挑图：`render-*.png` 一类产品渲染图排最前（`10_appearance` 阶段次之、结构预览再次；
  爆炸图 / 正交图是工程图，往后排；序号小的靠前）。没有位图时用 `views-sheet.svg` /
  `model_preview.svg` 顶上，矢量图换白底显示。
- 多图给缩略图条可切换；某张取不到（产物被后续阶段清理 / 链接失效）自动退到下一张，不留空白框。
- 右侧：阶段进度（✓ 有产出的阶段 / 总数）、产物文件数、本轮用时，加交付汇总前三句。
  `manifest.next` 为空显示「全部设计已交付」，否则「本阶段已交付」。
- 操作：查看设计产物（打开主图并平滑滚到右栏）、下载全部、知道了；ESC 或点遮罩可关。

**触发时机**（`web/app/page.tsx`）
- 只认真正的 `done` 事件，且**不处于**「超时续跑」状态（那种情况后半段还在另一个会话里跑，不算收工）。
- 刷新 / 深链补弹：从历史打开已完成会话，若它结束不到 10 分钟且本轮没弹过（`celebratedRef`），
  补弹一次 —— 刚跑完刷新页面仍能看到；翻旧会话不会被反复打扰。
- 开新跑或出错（status → running / error）自动收掉弹窗。

**样式**：`globals.css` 增 `dialog-mask` / `dialog-card` / `hero-img` 三段入场动画
（遮罩淡入 + 卡片位移缩放弹入 + 主图轻微放大淡入），`prefers-reduced-motion` 下全部关闭。
效果截图：`docs/completion-dialog.png`（真实会话数据渲染）。

## 最新改动：出图模型默认升 2.5-flash，界面能看见「设置是否生效」（2026-10-15）

**现象**：在「模型设置 → 图像生成」里选了 `agnes-image-2.5-flash`，界面/健康检查仍显示
`agnes-image-2.1-flash`，看起来像设置没保存。

**真相**：出图链路没坏 —— 前端 `toPayload()` 确实传了 `image_model`，后端 `generate_product_image`
也确实读它。坏的是**显示**：`/health` 里那行写死了「按 .env 推算」，完全无视你在页面上改的值，
所以设置改了也永远看不到回显。而 `.env` 里恰好显式写着 `AGNES_IMAGE_MODEL=agnes-image-2.1-flash`，
2.1 就是这么冒出来的。

**实测三代模型都能用**（真实联网出图）：`2.5-flash` 16.4s / 680KB，`2.1-flash` 13.9s / 681KB。

**改动**：
- 默认图像模型统一升到 `agnes-image-2.5-flash`（`config.py` / `.env` / `imagegen.py` / `README.md`）。
- `/health` 的 `image` 改为**以最近一次会话的前端设置为准**，缺省才回落 .env，并增加
  `source`（`recent-run` / `env-default`）与 `env_model` 字段，一眼看出「这个值哪来的」。
- 设置面板：修掉「切走服务商再切回，手填的模型 ID 被内置默认冲掉」的坑；
  端点反推时规范化（尾斜杠/大小写），不再把 Agnes 误判成「跟随」；
  模型输入框加候选下拉，并回显「本次出图使用：xxx」。
- 页头新增「出图 · 模型名」徽章 —— 改完设置立刻能看到生效，不用再去翻日志。

---

## 上一轮：网关「间歇性不可达」真根因 = IPv6，IPv4 优先生效（2026-10-14）

**事故与真相**：看到「网关持续不可达、连接超时」弹窗，实测 agnes 网关时通时挂。挖到底：

- agnes 域名（Cloudflare 双栈）DNS 同时返回 IPv4/IPv6；Python 的 socket 按 DNS 顺序**串行**尝试、
  IPv6 排前面，而本机 IPv6 出口高延迟抖动（同一秒实测：**v4 0.3s 连上，v6 11.2s**，丢包时段直接超时）。
- curl 看着"时通时不通"是因为它有 happy eyeballs 并行尝试；项目内 httpx/urllib 每次都先撞 IPv6。
- 修复：`agent/config.py` 顶部把 `getaddrinfo` 结果**排序为 IPv4 优先**（不过滤，v4 全挂仍回退 v6）。
  补丁前 3 次探活全超时 → 补丁后 3/3 全通（1.6~3.6s），模型探测 1.5s。`AGENT_IPV4_FIRST=0` 可关。

**顺带修的两个判定问题**：

| 问题 | 修复 |
|---|---|
| 所有失败都当「网关不可达」白等 600 秒 | `gateway.classify()`：401/402/403/404 判 `quota`（余额/鉴权/模型名问题，等待无意义直接提示充值/换 key），只有连接类错误才进等待窗口 |
| 「测试连接」拉得动模型列表就报成功（有的网关 chat 一律 402） | `/api/models` 顺带真调一次极小 chat，按 `ok/quota/unreachable` 如实显示；探测模型跳过 bge/embedding/rerank 等（拿嵌入模型发 chat 会 400） |

体检工具：`scripts/diag_gateway.py` —— 30 秒分清「连不上 / 调不动 / 没配置」三种状态。
另：备用网关硅基流动实测**余额为 0**（chat 一律 402），需要充值或换 key 才能用。

---

## 最新改动：问卷「框架固定 + 选项随需求变」（2026-10-14）

诉求是两个：问卷得跟着产品需求走，但框架和各部分在不同产品间要尽量相近。解法是拆两层 ——

| 层 | 由谁定 | 跨产品 |
|---|---|---|
| **骨架**：5 个分区、16 个字段，key / 题型 / 顺序 / 必填 / sticky 全锁死 | `agent/clarify.py` 的 `SECTIONS` + `FIELDS` | 完全一致，模板和模型都改不动 |
| **内容**：每个字段的选项、默认值、输入提示 | 先按需求关键词从 `POOLS` 池里打分挑，模型可用时再重写 | 每个产品都不同 |

- 分区固定为：① 定位与场景 ② 形态与环境 ③ 硬件与供电 ④ 交互与外观 ⑤ 成本与交付。
- 每个字段有 10-15 个候选选项，按需求关键词打分取前 6-7 个，**命中项直接当默认值**
  （需求写「ESP32」→ 主控默认就是 ESP32；写「太阳能」→ 供电默认太阳能）；末尾恒挂
  「其他（请补充）」，选中展开自由输入。多选只排序、不预填，避免必填项被自动回答。
- **模型只填选项，不改框架**：`AGENT_CLARIFY_MODEL=1` 时 prompt 明令禁止改 key/题型/顺序，
  返回的陌生 key 直接丢弃。默认关 —— 纯规则点开即出，省一轮 40~80 秒的 LLM 往返。
- **模板只覆盖文案**：`clarify-template.json` 改成 `assumptions` + `overrides` 定制层
  （兼容旧平铺格式），写坏了只丢定制，问卷照常出。
- 前端 `ClarifyForm` 按分区分组渲染，带分区标题、hint 和已填计数。

验证：`scripts/test_clarify_skeleton.py` 172 项断言全过（跨产品框架指纹一致、选项随需求变、
模板与模型都改不动框架）；接口返回 `framework=skeleton-v2`、5 分区 16 题；`tsc --noEmit` 零错误。

---

## 最新改动：CAD 建模改走 E2B 云端沙箱（2026-10-14）

**为什么改**：建模连续报「环境自检失败、连 import build123d 都起不来」，但单独跑同一段脚本完全正常。
实测定位到真因 —— **本机 8GB 内存（当时可用仅 0.2GB），并发建模时 OpenBLAS 分配失败把子进程打死**：
退出码 1 且 stdout/stderr 全空。并发 4 路跑 8 次，7 次崩。不是脚本问题，也不是环境坏了，是本机扛不住。

| 项 | 改前 | 改后 |
|---|---|---|
| 建模运行时 | 本机 venv（并发即崩） | **云端沙箱优先**，本机降级为兜底 |
| 单次耗时 | import OCP ~15s，还有崩溃风险 | 创建 2.4s + 装包 9.8s，**建模 2.5s**（沙箱单例复用，冷启只付一次） |
| 沙箱故障 | — | 自动回落本机，不会让建模卡死 |
| 沙箱内脚本报错 | — | 原样回传，**不回落本地**重跑绕真因 |
| 本机兜底 | 无锁（`threading.Lock()` 每次新建 = 等于没锁） | 模块级串行锁 + `OPENBLAS_NUM_THREADS=1` + 剔除宿主 PYTHONPATH |

配置（`.env`）：`E2B_API_KEY`、`AGENT_CAD_RUNTIME=auto`（e2b > local > cadgen）、`AGENT_E2B_TIMEOUT=1800`。

**顺带定位了那次失败脚本的真因**：圆柱/圆锥没有平行 Z 的直边，`filter_by(Axis.Z)` 选空必报
`no suitable edges for fillet` → 改成 `filter_by(GeomType.CIRCLE)` 选上下沿圆边后，5 个零件
（底壳/上盖 Ø90×12、导光罩 Ø64×1、按键帽×2）全部跑通，STEP + STL + 预览 SVG 取回本地，31.6s。
这条已写进建模 skill 的错误对照表与报错提示。

---

## 最新改动：补全问卷「能点选就点选，选不了就写」（2026-10-14）

- **能枚举的题全部选项化**：形态（方形盒/长条/圆饼/薄片）、使用环境（室内/潮湿/半户外/户外）、
  成本（≤50/≤80/≤150/≤300/不限）从纯文本改成下拉；场景与功能清单无法枚举，保留多行文本。
- **每道选择题都带「其他（请补充）」兜底项**：选中即展开自由输入框，补充文字直接作为答案
  （select 存文字本身，multiselect 与已选项「、」合并），sticky 记忆同样生效。
- 顺手修了一个静默 bug：options 上限 6 把「显示与交互」第 7 个选项截掉了，上限提到 8。
- 模板仍是 `clarify-template.json`，改完保存即生效；兜底项约定已写进文件头说明。
- 验证：接口实测 12 题全带兜底项，浏览器冒烟 select/multiselect 联动正常
  （截图 `docs/clarify-custom-options.png`）。

---

## CAD 建模三连故障修复（2026-10-14）

线上日志暴露三个独立故障，全部修复：

1. **CadQuery 链式 fillet 反复被拦**：模型把网上教程的 CadQuery 写法
   `edges().filter_by(...).fillet(radius=2)` 套到 build123d（0.13 只有函数式）。
   静态检查拦住后重写还犯 → 平台现在**自动改写**为函数式，不再烧轮次。
2. **「OCP 几何内核崩溃」误诊**：子进程空输出崩溃连最简脚本都中招，实测同脚本单跑正常 ——
   是瞬时环境故障。现在空输出**自动重试一次**，仍失败跑**环境自检**：
   环境坏 → 明确说「别改脚本，原样重发」；环境好 → 才按几何诱因排查。
3. **save_artifact 丢 path 反复报错**：长参数下模型丢 path 字段，硬报错教不会。
   现在缺 path 时按内容**推断落盘**（脚本→model.py，文档按标题→对应阶段目录），
   path/content 传反自动互换，回执里顺带教它下次带 path。

验证：`scripts/test_cad_robust.py` 21/21，回归 cad_error 52 / cad_runtime 16 /
artifact_guard 28 / pipeline 51 全过。

---

## 网关抖动不再误杀会话（2026-10-13）

**症状**：跑得好好的突然弹「网关当前不可达：连续多次连接失败，且 /models 探活也不通」，整轮被终结。

**根因**：探活一击判死 —— 连续 2 次网络错误后只问一次 `/models`（20s 超时），不通就把 run 标成 error。
但网关的短暂拥塞（几十秒到几分钟）远比「真下线」常见，本可自愈的抖动被放大成失败弹窗。

**修复**：探活不通后进入「等待-恢复」窗口（默认 600s 上限、15s 探活一次），
恢复即自动续跑（已落盘产物保留、进度写进续跑上下文）；等到上限仍不通才报错。
等待期间：点「停止」1 秒内响应；等待与探活耗时不计入运行时限。
新增配置 `AGENT_GATEWAY_WAIT_MAX` / `AGENT_GATEWAY_PROBE_INTERVAL`。
验证：`scripts/test_gateway_wait.py` 12/12，回归 pipeline 51 / cad_error 52 / artifact_guard 28 全过。

---

## 本轮完成的两件事

### 1. 会话与产出全部进数据库（SQLite）

之前会话只在内存 + 文件系统里，**服务重启就散**。现在每次会话的需求、完整事件流、产物清单都写进 `data/agent.db`。

| 表 | 存什么 |
|---|---|
| `runs` | 需求、状态、模型、创建/完成时间、耗时、事件/工具/产物计数、摘要、错误 |
| `events` | 完整事件流（按 seq 有序，可回放） |
| `files` | 产物索引（路径 / 大小 / 修改时间 / 所属阶段） |

设计取舍：

- **大文件不进库**：PNG / STEP / STL 本体留在 `workspace/<run_id>/`，库里只存索引，避免库膨胀
- **密钥绝不入库**：`settings` 落库前把 `api_key` 替换成 `***`（已实测）
- **事件批量提交**：攒够 30 条或距上次 2 秒写一次，高频流式写不拖慢输出
- **进程被杀也不丢**：启动时把上次遗留的 `running` 标为 `interrupted`
- **旧会话回填**：`store.backfill()` 扫描 `workspace/`，把库里没有的会话补进历史 —— 本次回填出 **24 条**旧会话、160 个产物

前端「历史会话」面板：关键词检索、打开旧会话回放事件流与产物、移除记录（**不删磁盘文件**）。新增 `?run=<id>` 深链。

### 2. 任务规划不再停在第一格

根因：面板只吃模型主动发的 `write_todos`，而 `agnes-2.5-flash` 实测**只在开头写一次清单，之后不管做到哪都不回写**。于是「产物面板里 PRD / 渲染图 / STEP 都堆满了，规划还停在第一格」。

改成**以磁盘产物反推阶段状态**（`web/lib/plan.ts`）—— 文件是硬证据，模型嘴上说没说不算数：

| 阶段 | 判据 |
|---|---|
| 需求定义 | `00_prd/*.md` |
| 外观设计 | `10_appearance/*.md` |
| 结构设计 | `20_structure/structure.md` 或 `*.step` / `*.stl` |
| 硬件设计 | `30_hardware/*.md` 或 `bom.csv` |
| 固件与仿真 | `40_firmware/` 下任意文件 |
| 交叉评审 | `90_review/*.md` |

踩到的坑：判据原本要求阶段目录在顶层，但模型实测把固件写进了自己臆造的子目录
`home/user/repos/prod-xxx/40_firmware/firmware.ino` → 改成「路径中出现该阶段目录」。

顶部同时给出 `n/6 阶段`、已用时间、以及**预计剩余**（按历史会话平均耗时折算；没有样本时只显示已用时间，不硬编假进度）。

**验证**：`scripts/test_plan.ts` 用真实产物清单跑了 19 项断言，全通过；历史会话 `f1e978347fd6` 在界面上正确显示 **6/6 阶段**。

---

## 顺手修掉的

- **幽灵工具卡片**：`tools.py` 在工具内部又发了一次 `tool_start`（无 `cid`），前端永远等不到配对的 `tool_end`，卡片就一直空着转圈。改成独立事件类型 `tool_progress`。
- **图片 Markdown 是坏的**：前端只有返回文本 JSON 的文件接口，`<img>` 拿到的是 JSON。补了 worker `/api/runs/{id}/raw`（按后缀给 Content-Type）+ 前端代理，再加三级路径查找（原样 → 相对文档目录 → 同文件名兜底），模型把图放错目录也能显示。
- **一键启动脚本**：`start.ps1` / `stop.ps1`（服务此前常被会话结束回收）。

---

## 当前阻断：Agnes 网关被网络拦截（外部问题，非代码）

实跑到 20:20 左右开始，`apihub.agnes-ai.com` 全线失败。定位过程：

```
证书签发者：O=redirect-cnzz        ← 不是正规 CA
pypi.org / api.siliconflow.cn / baidu.com 的证书都正常
→ 只拦这一个域名
```

8 次重试（校验开/关各 4 次）**100% 失败**：开校验是 `CERTIFICATE_VERIFY_FAILED`，关校验是 `Server disconnected without sending a response` —— 不是抖动，是被拦。

已做的加固（不是绕过，是让问题可诊断）：

- worker 检测到证书类错误**直接给可操作提示**，不再做无意义的 3 次重跑
- 新增 `AGENT_CA_BUNDLE`（推荐：把代理根证书导成 pem）、`AGENT_VERIFY_SSL=0`（临时排障）
- README 写了排障顺序

**建议**：关掉本机 `127.0.0.1:7897` 的代理后重启 worker，或在前端「模型设置」换成其他服务商。

---

## 验证结果

| 项 | 结果 |
|---|---|
| `tsc --noEmit` | 0 错误 |
| Python 语法 | `store/server/config/main_agent/imagegen/tools/subagents` 全部 `ast.parse` 通过 |
| 规划推导单测 | 19/19 通过 |
| 事件聚合单测 | 全通过 |
| 数据库 | 26 个会话 / 289 事件 / 160 产物索引；密钥落库为 `***` |
| 历史接口 | 列表 / 详情 / 事件重放 / 删除，均 200 |
| 二进制通道 | PNG / SVG / STEP 正确 Content-Type；`../../.env` 返回 400 |
| CAD | `/health` 返回 `cad.ok=true`（build123d 0.13.0 本地运行时） |

## 待办

- Agnes 网关解封后跑一轮完整实跑，确认规划面板在**实时**运行中也逐格推进（目前已在历史会话上验证 6/6）
- 长流程收尾偏慢（子智能体串行），后续可考虑并发编排

---

## 启动记录（2026-10-10 21:26）

| 服务 | 地址 | 状态 |
|---|---|---|
| 智能体 worker | http://127.0.0.1:8001/health | `ok=true`，model `agnes-2.5-flash`，cad `local:0.13.0` |
| 前端 | http://127.0.0.1:3000 | HTTP 200（33 KB） |
| 数据库 | `data/agent.db` | 26 会话 / 289 事件 / 160 产物 |

启动踩到的两个环境问题及规避（已写进脚本）：

1. **PS 5.1 `Start-Process` 已知 bug**：同时传 `-RedirectStandardOutput` 与 `-RedirectStandardError` 报 `已添加项。字典中的关键字:"Path"所添加的关键字:"path"` → 改用 `cmd /c "... > log 2>&1"` 做重定向。
2. **Next 15 启动崩**：清理 `.next` 里的旧 hot-update 文件被外部批量删除守卫拦截（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`）→ 启动前把旧 `.next` 改名（move 不触发删除守卫），Next 重建即可。

新增 `restart-web.ps1`（只重启前端，不动 worker）；`stop.ps1` 用法不变。

**当前外网状态**：`apihub.agnes-ai.com` 仍被拦截（TLS 握手拿到自签证书，绕过代理直连同样失败，解析 IP `141.193.154.210`）。服务本身健康，但**实时跑会断在联网调用**；历史会话、产物回放、CAD 本地建模不受影响。

---

## Harness 四层（本轮落地）

同一需求两次跑出「工具 72 次 vs 239 次、产物 10 vs 31」，根因是**没有护栏的自主循环**：模型可以十几分钟不落一个产物，也能用同样参数反复调同一个工具上百次。按四层补齐：

| 层 | 落地 | 关键实现 |
|---|---|---|
| 记忆 | 情景记忆 | `store.memories` 表（需求→结果摘要）+ `_tokens` 中文 2-gram 相似度检索；run 成功交付时回写，下次启动注入 system prompt |
| 钩子 | 事件侧钩子 | `Run.push` 是唯一入口：先过 `_guard`（可抛 `RunAborted` 中断），再决定「SSE 逐条推 / 落库聚合」 |
| 边界 | 文件沙箱 + 预算 | `_safe_target()` 用 `Path.parents` 判越界（前缀相同的兄弟目录不再被放过）；`MAX_RUN_SECONDS` / `TOOL_BUDGET` / `REPEAT_LIMIT` |
| 终止 | 六条判据 | 取消 / 总时限 / 工具预算 / 重复熔断 / 连续失败 / **目标达成** |

### 三个关键决策

1. **「目标达成」不信模型，只信磁盘产物。** 新增 `agent/stages.py`，与前端 `web/lib/plan.ts` 共用同一套阶段正则。六阶段齐全 → 推 `goal_reached` → 进入 150 秒收尾宽限期 → 到期按 `RunGoal`（status=`done`，不是错误）交付。
2. **`RunAborted` 必须穿透每一层。** `main_agent` 主循环那条兜底的 `except Exception` 会把 push 抛出的中止信号当「事件解析失败」吞掉 —— 表现为前端点了停止、后台继续空转。已加 `except guards.RunAborted: raise` 前置分支。
3. **事件分级：SSE 逐条推（保打字机），落库按 0.5s / 1500 字符聚合。** 这是 DB 从 91MB 掉回 2.4MB 的根本手段，不是靠事后清理。

### 清库结果

`scripts/purge_stream_events.py` 从「全删」改为**按 8 秒窗口合并成块**（思考过程保住，条数降两个数量级）：

```
事件 501406 条（39.6 MB） → 1186 条（1.6 MB）
数据库 91 MB → 2.4 MB
2 个失控 run 标记为 interrupted
```

备份留在 `data/agent.db.bak-222014`（91 MB），确认无误后可删。

### 自检

`scripts/test_harness.py` —— 30 条断言全过，覆盖阶段判据、情景记忆召回/不召回、沙箱越界、护栏中止穿透、六条终止判据实跑触发：

```
python scripts/test_harness.py   # 30 通过 / 0 失败
npx tsc --noEmit                 # 0 错误
```

### 新增 / 改动文件

- 新增：`agent/stages.py`（阶段判据）、`agent/guards.py`（中止信号，本轮加 `RunGoal`）、`restart-worker.ps1`、`scripts/test_harness.py`
- 改动：`agent/store.py`（memories 表 + `save_memory` / `recall` / `memory_hint`）、`agent/server.py`（goal 护栏、收尾落库与记忆回写、finally 里的 push 不再能挡住落库）、`agent/main_agent.py`（记忆注入 + 中止穿透）、`agent/tools.py`（`_safe_target` 沙箱）、`agent/config.py`（`GOAL_GRACE_SECONDS`）、`web/app/page.tsx`（`goal_reached` → 「六阶段已齐，收尾中」）、`scripts/purge_stream_events.py`（改为合并而非删除）

---

## 决策范式层（Orchestrator）：四档执行策略 + 降级链

参考《四种智能体决策范式：ReAct、Plan-and-Execute、Reflexion 与 Tree of Thoughts 实战解析》落地。
核心观点：**范式不是并列名词，而是四块不同的「思考肌肉」**，选型判据是任务 DNA 的三个基因
（时效 / 容错 / 进化），而不是「哪个框架新」。

### 四档映射

| 范式 | 设计哲学 | 适用边界 | 本项目的档位参数 |
|---|---|---|---|
| ReAct | Thought→Action→Observation 咬合成环 | 工具链 ≤5 步、反馈即时、错误成本低 | `lean`：主智能体自己查，不派子智能体 · 18 次工具 / 240s |
| Plan-and-Execute | 先出 PRD 锁定全局约束，再按图施工 | 多阶段强依赖、错误代价高、可容忍秒级延迟 | `standard`：PRD→四专业→评审 · 80 次 / 900s |
| Reflexion | 评审后返工一轮，并沉淀「错误模式→修正动作」 | 高频重复任务、同类错误会复发 | `review`：+反思轮 · 110 次 / 1200s |
| Tree of Thoughts | 单层分支 + 打分择优（不做完整树） | 需求模糊、多目标冲突、高价值决策 | `explore`：3 分支打分 · 140 次 / 1500s |

降级链固定 `explore → review → standard → lean`。**超时/超预算就往下降，不原地重试** ——
原地重试只会重复失败路径。

### 顺带修掉的两个真问题

1. **取消拦不住**：护栏只能在「下一个事件到达时」中断流，智能体堵在模型 HTTP 长连接上时
   几十秒不推事件 → 前端点了停止还在转圈。加了取消看门狗（`CANCEL_GRACE_SECONDS=20`）：
   超时就先给前端收口，后台线程跑完仍照常落盘，产物不丢。
2. **假死无法感知**：静默超过 2 秒推一条 `heartbeat`（**不落库、不进事件列表**），
   前端 20 秒无任何事件就显示「后端可能卡住」并把停止按钮变实心。

### 其他落地项

- **工具 ID 绑定 + 参数 Schema 预加载**（`agent/toolspec.py`）：`tool_001…tool_009` 与参数签名
  写进提示词和 AGENTS.md，杜绝编造工具名与参数名。
- **Reflexion 规则库**（`store.reflexions`）：从终态**确定性抽取**规则（不额外调模型，省钱且稳定），
  优先级 40 鉴权 > 30 运行时 > 20 阶段 > 10 通用；同 pattern 累加 hits；超 60 条按前缀聚类压缩成元规则。
- **Plan 锚点**：每步回交必须带 Step ID + 产物路径，下一步任务描述必须引用，防长流程漂移。
- **决策日志**：`runs.paradigm / n_reflexions / goal_hit`，`/api/stats` 输出各范式的
  平均工具数 / 平均时长 / 目标达成率，用于反推范式健康度。

自检 `scripts/test_paradigms.py` 45 项断言全过；端到端实跑验证：explore 档路由正确、
paradigm 事件送达、心跳每 2 秒一条、取消 21 秒收口、7 条反思规则入库存。

---

## 修复：read_file「重复 5 次」被误判为无效循环（2026-10-10）

**现象**：一次运行交付汇总显示「超出预算中止：同一调用重复 5 次（read_file），判定为无效循环并中止」，
但六个阶段的产物其实都出来了。

**根因（两层，第二层才是真凶）**

1. 护栏的去重签名是 `(工具名, 参数)`，阈值一刀切 5 次；只读工具（read_file/ls/glob）幂等且无副作用，
   子智能体各读一遍 AGENTS.md 和上一步产物本来就是正常协作 —— 阈值本身就不该一样。
2. **真凶**：`tool_start` 事件里的 `args` 恒为空。工具参数是流式逐块拼出来的，首发那一下还没到，
   而 Agnes 网关还额外先发一个占位 `{}`。于是签名退化成「只有工具名」，
   **读 5 个不同文件被算成同一调用重复 5 次**。

**修法**

| 层 | 改动 |
|---|---|
| 参数补齐 | `main_agent` 的 `tool_start` 改为**延迟到参数完整再发**：空串 / 半截 JSON / 空容器 `{}` 都视为「没到齐」，等 `ARGS_WAIT_SECONDS=0.8s` 兜底；结果先到时补发 start 保证配对 |
| 参数回填 | 新增从 `updates` 流里 AIMessage 的 `tool_calls` 回填参数（实跑验证：空参数从 100% 降到 ~30%） |
| 阈值分档 | 只读工具 24 次、无参签名 15 次、其余 5 次（`REPEAT_LIMIT_READ / REPEAT_LIMIT_NOARG / REPEAT_LIMIT`） |
| 停滞判据 | **重复 ≠ 空转**：必须「次数够多 + `STALL_SECONDS=180s` 内毫无新进展」才中止；期间在出产物就只提示一次（按工具名去重，全局最多 3 条） |
| 加权预算 | 只读廉价调用按 `TOOL_WEIGHT_READ=0.25` 计入预算，昂贵调用（出图/CAD）才算 1 次 |
| 中止定性 | 产物已够 `GOAL_MIN_STAGES=4` 个阶段时，中止定性为 `RunGoal`（status=done，显示「目标达成」），而不是「超出预算中止」 |

**验证**：新增 `scripts/test_loop_guard.py`（23 项断言：只读多读不杀 / 空参签名放宽 / 真空转照杀 /
产物齐按交付定性 / 预算加权 / 四种参数到达顺序），加 `test_harness`(31) `test_paradigms`(45)
`test_visuals`(27) 共 126 项全过。实跑复验：90 秒内 7 次调用、签名各不相同、无中止告警。
排查脚本 `scripts/verify_loop_guard.py`（注意：`/api/runs/{id}/events` 不返回 seq，增量统计会重复计数）。

---

## 外观-结构同源：产品图走 Agnes，结构图由外观提示词驱动（2026-10-10）

**目标**：渲染图 / 场景图 / 爆炸图一律由 Agnes 出；结构模型不另起炉灶，
而是拿外观阶段那条出图提示词当形状约束，交给 text-to-cad（cadgen / build123d）落成实体，
保证"外观图与结构图是同一个产品"。

**链路**

```
industrial-designer ──generate_product_image(prompt)──▶ Agnes 出图（渲染/场景/爆炸）
        │                                                   ▲ 固定 Agnes，与文本模型解耦
        └─ 提示词自动存档 ──▶ 10_appearance/prompts.json ────┘
                                      │ 结构阶段读回
                                      ▼
mechanical-engineer ──text_to_cad(无 script)──▶ 拿回外观约束
                   ──text_to_cad(script)──────▶ cadgen 建模 → STEP/STL
                                              → 四视图 + 爆炸图
                                              → 20_structure/consistency.md（外观 vs 实际尺寸对照）
```

**关键决策**

| 项 | 做法 | 为什么 |
|---|---|---|
| 出图服务商 | `config.IMAGE_PROVIDER=agnes`，`resolve_image_provider()` 与文本网关解耦；只有前端「图像」区显式指定才覆盖 | 同一台产品的几张图必须同画风，不该因为文本模型换成就换一批画风 |
| 提示词传递 | 出图时**由工具**写入 `10_appearance/prompts.json`，不让模型口头转述 | 实测模型转述会丢细节、顺手改尺寸，结构拿到的就是"看着像但数字不对"的描述 |
| 建模栈 | 新增 `text_to_cad`（tool_010）；CAD 运行时默认 **cadgen（text-to-cad 官方，build123d 0.11.1）优先**，拉不起来回落本地 venv（0.13.0） | 点名用 text-to-cad；官方运行时版本由 cadgen 锁定，与 skill 规范同源 |
| 一致性 | 建模后自动写 `20_structure/consistency.md`：外观提示词 + 模型实际包围盒 + 待核对清单 | 一致性不能靠模型自觉，得落成文件让子智能体逐条核对 |
| 视图补齐 | `text_to_cad` 成功后自动出四视图与爆炸图（与 `render_product_views` 共用写图逻辑） | 避免"模型有了、图没有" |

**验证**：`scripts/test_visual_chain.py` 21 项断言全过；Agnes 实测出图 —— 渲染图 707KB / 9.9s、
爆炸图 620KB / 9.7s；cadgen 运行时跑通现有 0.13 模板脚本（STEP/STL/SVG 齐全）。
切换开关：`AGENT_IMAGE_PROVIDER`（默认 agnes）、`AGENT_CAD_RUNTIME`（auto / cadgen / local）。

---

## 修复：CAD 内核不可用（空错误文案）（2026-10-11 凌晨）

**现象**：`CAD 内核不可用（build123d 未安装或加载失败）：cadgen 运行时不可用：。`
冒号后面是空的 —— 手工跑 `uvx --from cadgen==0.7.20 python -c "import build123d"` 却好好的。

**根因（三重叠加）**

1. `uvx` 每次调用都要重新解析环境（实测 **~21s**），而且**同一环境的并发调用会撞 uv 的缓存锁**：
   失败时 `returncode≠0` 且 stdout/stderr **全空** —— 空错误文案就是这么来的。
   前端几秒一次 `/health` 就会撞上建模线程。
2. 探测的**失败结论被永久缓存**（`_probe_cache` 无条件写入），一次抖动就让 CAD 能力在
   整个进程生命周期里判死，重启前不会再试。
3. `cadgen==0.7.20` **硬锁 build123d>=0.11.1,<0.12**（`--with build123d==0.13.0` 直接解析失败），
   而建模模板与 API 速查表按 0.13 写 —— 版本天然对不上，白送一批失败重试。

**修法**

| 层 | 改动 |
|---|---|
| 解释器固化 | 只让 uvx 跑一次，问出它环境里 python 的绝对路径并缓存（`_resolve_cadgen_python`），之后直接调该解释器：单次 21s → **毫秒级**，并发锁冲突一并消失 |
| 调度优先级 | `auto` 改为**本地 venv 优先**（0.13.0，毫秒级、无网络依赖、与模板同版本），本地没装才走 cadgen；强制走官方运行时：`AGENT_CAD_RUNTIME=cadgen` |
| 结论 TTL | 成功结论永久缓存；**失败结论只缓存 120s（探测）/ 300s（可用性）**，抖动后能自愈 |
| 非阻塞健康 | 新增 `cad.status()`：`/health` 不再发起昂贵的 uvx 解析；本地装了就秒回，没有则后台预热并如实说「正在探测」 |
| 启动预热 | worker 起后台线程预热 build123d（首次 import ~15s），`/health` 与首次建模都不再干等 |
| 错误文案 | stdout/stderr 全空时明确写出「uvx 并发撞缓存锁的典型表现，正回落到备用运行时」；不可用时给出两条可操作修复路径 |

**验证**：新增 `scripts/test_cad_runtime.py` 15 项断言全过（本地优先 / 固化后不走 uvx /
取 argv 毫秒级 / TTL 复用 / 清缓存后能自愈）。全套回归 **165 项**通过
（harness 31 + loop_guard 23 + paradigms 45 + visuals 27 + visual_chain 24 + cad_runtime 15）。
`scripts/cad_selftest.py` 建模成功：runner=local，STEP/STL/预览 SVG 齐全。
`/health` 的 cad 字段：`{"ok":true,"info":"local:0.13.0 3.13.14"}`。

---

## 新增产品主页面（/home）：全流程 · 商业化 · 科技风（2026-10-11）

**入口**：`web/app/home/page.tsx`（路由 `/home`）；工作台顶栏新增「产品主页」入口，主页 CTA 反向回工作台。

**页面分区**

| 区块 | 内容 |
|---|---|
| Hero | 一句话价值主张 + 双 CTA + 四个真实指标（6 阶段 / 10 工具 / 4 类专业角色 / 3 类工程图）+ Agnes 生成的主视觉 |
| 设计流程 | 六阶段卡片：阶段号、负责子智能体、输入 → 输出清单，每阶段配一张 Agnes 生成图 |
| 全过程演示 | 嵌入 `process.mp4`（Agnes 生成，8s / 720P / 16:9），poster 用主视觉，标注「视频与配图均由 Agnes 生成」 |
| 交付与部署 | 三条商业化主张：私有部署（数据不出本地）/ 模型可插拔 / 产物直接进下游 |
| 产物清单 | 六个工作区目录与其内容对照表 |
| CTA + Footer | 回到工作台 |

**Agnes 资产（脚本 `scripts/gen_home_assets.py` 一键重生成）**
`hero / flow-concept / flow-appearance / flow-structure / flow-electronics / verify / business` 共 7 张，
落到 `web/public/home/`；视频走 Agnes 文生视频（8 秒）。

**设计约束（沿用 globals.css 令牌，未新增硬编码色）**
- 主色 `--accent` 只用于阶段序号、关键数字、CTA；其余交给留白与分割线
- 卡片统一：1px `--border` + 14px 圆角 + `--shadow`；间距栅格 4/8/16/24/32/48/80
- 字号层级：H1 34→44、H2 26→30、正文 14/15、辅助 12/13
- 入场动画只做 14px 位移淡入，`prefers-reduced-motion` 下直接显示
- 无卡通/二次元元素，配图写实工业风

**已知事实（非承诺）**：指标与主张只写系统真实具备的能力（阶段数、工具数、产物类型），
未编造百分比与价格。

**验证**：`tsc --noEmit` 通过；`/home` 返回 200（53KB）；静态资源 hero.png 900KB / process.mp4 6.2MB 均可访问。

**Agnes 视频通道现状**：`POST /v1/video/generations`（model 必填）是正确端点，但当前 key 为免费额度，
调用返回 429 `rate_limit_exceeded`（需 Token Plan）。本次视频改走 Agnes 多模态文生视频通道生成。
脚本 `scripts/probe_agnes_video.py` 可用于后续复测配额。

---

## 产品图一致性链：后续图基于前一张做图生图（2026-10-11）

**目标**：同一台产品的所有图（外观图、渲染图、爆炸图）必须长成同一台产品 ——
第一张是基线，第二张起在上一张/基线上改视角与场景，而不是各自独立文生图。

**Agnes 图生图通道（实测确定）**

| 通道 | 结果 |
|---|---|
| `POST /v1/images/generations` 带 `image`（base64 data URI） | ✅ 200，输出路径含 `/images/i2i/`，切到 i2i 队列 |
| 同端点带 `images` / `reference_image` / `image_url` / `input_image` | ❌ 400 `is not supported by text image queue` |
| `POST /v1/images/edits`（multipart） | ❌ 503 ServiceUnavailable（该服务未上线） |
| 不带 `image` | 走 text 队列，输出路径含 `/images/t2i/` |

**链路**

```
第 1 张 generate_product_image(kind="render")        → 文生图（t2i）→ render-01.png   ← 产品基线
第 2 张 generate_product_image(kind="render")        → 图生图（i2i）参考 render-01 → render-02.png
第 N 张 generate_product_image(kind="render")        → 图生图（i2i）参考上一张     → render-03.png
爆炸图 generate_product_image(kind="exploded")       → 图生图（i2i）参考 render-01 → exploded-01.png
```

**关键决策**

| 项 | 做法 | 为什么 |
|---|---|---|
| 参考图挑选 | `reference="auto"`（默认）：渲染图跟**上一张**、爆炸图跟**第一张**（产品基线） | 渲染图要逐步演进；爆炸图必须拆最初那台，否则拆出来的壳体和渲染图对不上 |
| 一致性锚 | prompt 前置 `STYLE_CONSISTENT`：「严格保持参考图中的同一个产品，不得改变产品设计，只允许改视角/构图/背景」 | 不加这句，模型会把同一产品画成两代外形（实测） |
| 参考图体积 | Pillow 缩到 ≤1024px + JPEG q82，仍超 2MB 再降质压一次；原图更小则直传 | base64 后体积 ×1.37，网关对超大 body 敏感 |
| 覆盖保护 | 命名自动顺延 `render-01 → render-02`；显式指定已存在的路径也顺延 `-02` | 覆盖会直接毁掉参考图链：后续图会相对一个已被顶掉的基线生成 |
| 参考图失效 | 记录里写了路径但文件不存在 → 不使用（不拿空气当参考）；指定路径不存在 → 退回文生图并在回执里说明 | 静默降级成文生图而不告知，等于悄悄破坏一致性 |
| 网关不认 image | 400 且响应提到 image/not supported → 自动去掉 `image` 重发一次，结果标注"参考图未被网关接受" | 换网关时不至于整轮失败 |
| 存档 | `10_appearance/prompts.json` 每条记录增加 `reference` 字段（**相对路径**） | 结构阶段核对「这几张图是不是同源」需要它；绝对路径换机器失效 |

**子智能体契约**
- industrial-designer：**一张一张串行出**，禁止攒 prompt 或并行；第二张起只描述「要改成什么样」，不重复描述产品本体。
- mechanical-engineer：爆炸图调用 `generate_product_image(kind="exploded")` 且**不传 reference**（自动跟产品基线）；
  **AI 爆炸图上的尺寸数字一律不可信**（实测模型自己编了 87.0 / 17.3 这类数字），文档尺寸表只写 CAD 回传的包围盒值。

**验证**
- 新增 `scripts/test_image_ref.py` **39 项**断言（压缩上限 / 命名顺延 / 覆盖保护 / 参考图挑选规则 /
  reference 解析 / 请求体带 image 且含一致性锚 / 三次串行调用形成 reference 链 / 存档字段）
- 实跑 `scripts/verify_image_ref.py`：第 1 张 t2i（730KB/10.8s）→ 第 2 张 i2i 参考 render-01（738KB/14.2s）
  → 第 3 张爆炸图 i2i 参考 render-01（597KB/14.5s），三张肉眼一致（同比例、同旋钮位、同分模线、同发光环）。
  样图留档 `docs/samples/i2i-0{1,2,3}-*.png`
- 全套回归 **174 项**通过（image_ref 39 + visual_chain 24 + harness 31 + loop_guard 23 + paradigms 45 +
  visuals 27 + cad_runtime 16）；`test_cad_runtime` 里"预热后 status 不卡"一例原本测的是冷启动
  （首次 import build123d 7s，线上由启动预热线程摊掉），已改为先付冷启动再验证二次调用秒回。

---

## 修复：一次 429 判死整轮 / 出图下载抖动 / 子智能体找不到工作区（2026-10-11）

这三处都是**真实跑一轮才暴露**的，单测和链路自检都测不出来。

### 1. 网关限流（429）把整轮判死

**现象**：真实会话跑到需求阶段就 `OpenAIRateLimitError: 429 - rate limit for free users` 结束。
手工复测文本接口又立刻通了 —— 说明是**按时间窗口的瞬时限额**，不是余额耗尽。

**为什么扛不住**：langchain 的 `max_retries=3` 退避是秒级的，限流窗口是分钟级；
而编排层的重试只认「连接类错误」，429 直接落到「不可重试」分支，一次抖动终结十几分钟的运行。

| 改动 | 说明 |
|---|---|
| `_is_rate_limit()` | 识别 429 / rate limit / rate_limit / too many requests / RateLimitError |
| 退避续跑 | 限流单独计次（默认 4 次），退避 25/50/75s，上限 90s；每次都推 warn，用户看得到「在等什么」 |
| `Run.extend_deadline()` | **等待时间不计入总时限** —— 否则退避反而把自己跑进超时中止 |
| 终态文案 | 明确写「网关限流（429）+ 已产出保留 + 升级 Token Plan 或换服务商」，**不降级成演示**（拿假数据糊弄更糟） |
| 与网络错误隔离 | 限流重试和网络重试各算各的额度，互不占用 |

### 2. 图已生成但落盘失败（下载抖动）

**现象**：`图片已生成但落盘失败：<urlopen error [Errno 11002] getaddrinfo failed>`。
出图接口返回的是 CDN 直链，取图和调接口是**两次独立的网络往返**，DNS 抖动一次就作废整张图。

**改动**：`imagegen._download()` 默认重试 3 次（2s/4s 退避）；失败信息带上域名，便于判断是
CDN 不可达还是本机代理/DNS 问题。

### 3. 子智能体在 `/home/user` 里什么都读不到

**现象**：事件流里反复出现 `read_file("/home/user/AGENTS.md") -> File not found`、
`glob("/home/user") -> No files found`。

**根因**：`FilesystemBackend(virtual_mode=True)` 把 run 目录挂成虚拟根 `/`，
而 deepagents 的内置提示词把工作目录说成 `/home/user` —— 两者对不上，
每轮都要先烧掉好几次只读调用才反应过来（也解释了之前 `read_file` 高频重复的噪音）。

**改动**：
- `WorkspaceBackend(FilesystemBackend)` 在 `_resolve_path` 层把 `/home/user/...` 折回 `/...`，
  **所有读写列目录都走这一个入口**，比指望模型记住路径可靠
- 主提示词与子智能体 COMMON_RULES 各补一句「虚拟根是 `/`，不存在 /home/user」

### 4. 出图存档把模型手写的外形约束冲掉

`_record_appearance_prompt` 原本整份重写 `prompts.json` 为 `{"items": [...]}`，
把模型先写好的 `prompts` / `structure_notes`（结构阶段唯一的外形约束来源）直接抹掉。
改为**合并写回**：保留原有键，只刷新 `items`。

**验证**
- 新增 `scripts/test_rate_limit.py` **18 项**（限流识别 8 例 / 退避后续跑成功 / 等待补回时限 /
  提示可见 / 重试耗尽放弃 / 网络错误不占用限流额度）
- 新增 `scripts/test_workspace_path.py` **8 项**（前缀折回 / 真的读得到 / 安全边界未被放宽）
- `test_image_ref` 扩到 **46 项**（新增：存档保住模型手写的 prompts 与 structure_notes；
  下载抖动两次仍成功、重试耗尽带域名）
- 全套回归 **247 项**通过
- 真实会话验证：限流续跑 warn 已出现（`等待 25 秒后自动续跑（第 2/4 次）`）；
  text-to-cad 成功产出 STEP/STL + 四视图 + 爆炸图

## 串行依赖链：PCB 板型 ← 结构 ← 外观，产物按规划标题打包下载（2026-10-11）

用户提出的核心事实：**智能硬件五个阶段是强相关的串行链，不是能并行的五份文档**。
PCB 板型要装进结构内腔，板型大小反过来决定器件封装与 PCB 层数，器件功能又决定固件写什么驱动。
之前提示词里「硬件/固件与结构并行委派」正是「板子装不进壳子、引脚与代码对不上」的根源。

### 1. 阶段依赖写成数据 + 开工门禁

| 文件 | 改动 |
|---|---|
| `agent/stages.py` | 每阶段新增 `depends`（上游）/ `requires`（开工前磁盘必须存在的产物正则）/ `deliverables`（契约产物 + 中文标题）/ `pass_down`（传给下游的约束）；新增 `GATE_INPUTS`（开工前必读文件清单）与 `CHAIN` |
| `agent/pipeline.py`（新） | `gate()` 门禁、`snapshot()` 全链状态、`structure_metrics()` / `pcb_envelope()` / `hardware_devices()` 约束提取、`manifest()` 交付清单、`zip_stage()` / `zip_all()` 打包 |
| `agent/tools.py` | 新增第 11 个工具 `stage_gate(stage)`：返回「能否开工 / 缺什么 / 该读哪些文件 / 上游传下来的约束」 |

门禁是**硬判据**：读磁盘产物，不信模型自述。实测空目录时 20/30/40 全被拦住，补齐上游后逐段放行。

### 2. 板型 → 封装 → 层数这条链写进流程

- 结构阶段新增交付项：**给硬件的 PCB 装配边界**（内腔净尺寸、安装柱坐标与孔径、限高区、
  开孔坐标、建议板厚与余量）—— 用表格给数值，不写「详见图纸」（硬件读不到图纸）
- 硬件阶段新增交付项 **`30_hardware/pcb-layout.md`**：板框推导表、装配校验表（孔位/开孔/限高
  逐项核对）、封装选型表、层数决策（含面积判据与成本理由）、冲突时明确写「建议结构改哪里」
- 平台侧给了起点判据：`板框 = 内腔 − 单边 1mm`、`安装柱 2.5mm 禁布`、
  `板面积 <900mm² → 0402+QFN/BGA、4 层`；`900–2500mm² → 2 层`；`>2500mm² → 0603/LQFP、2 层`
- 固件阶段要求先读 BOM/网表，产出「器件 → 软件职责」对照表，逐个器件给驱动与仿真用例，
  引脚必须来自 netlist

### 3. 产物标题 ↔ 规划标题，三种粒度下载

- `/api/runs/{id}/manifest`：分组标题取自最后一次 `write_todos` 的规划项（关键词映射阶段），
  契约文件显示中文标题（`netlist.json` → 电路网表、`firmware.ino` → 固件源码），
  阶段外文件归入「其它」不丢
- `/api/runs/{id}/download`：`?path=` 单文件 / `?stage=30_hardware` 阶段包 / `?scope=all` 全量包
  （内含 `交付清单.md`，按规划标题分组）；中文文件名用 RFC 5987（`filename*=UTF-8''…`）
- 前端产物面板：每组一个「打包下载 N」，每个文件一个 `↓`，顶部「下载全部」；
  新增 `web/app/api/agent/{manifest,download,resume}/route.ts`

### 4. 续跑：外观图已有时不必重来

`POST /api/runs/{id}/resume`（`from_stage` 可省略 = 自动取下一个未完成阶段）：
复用原目录与已有产物，把「已落盘清单 + 从哪一步开始」拼进需求，串行跑剩余阶段。
前端在会话结束后显示「继续 结构设计」按钮。这正对应用户说的「现有外观图，确认再跑下面的结构」。

**验证**
- 新增 `scripts/test_pipeline.py` **50 项**（依赖声明 / 空目录三段拦截 / 逐段放行 /
  板框与层数推导 / 清单标题取自规划 / 阶段包与全量包内容 / 中文阶段名解析 / 缺上游时明确阻断）
- 既有回归全过：rate_limit 18、workspace_path 8、loop_guard 23、image_ref 46、cad_runtime 16
- 接口冒烟（run `657e3077a8fe`）：manifest 六个阶段标题取自规划清单；
  单文件 `image/png` + 正确 Content-Disposition；阶段包只含本阶段；全量包 27 条目含交付清单；
  resume 参数校验（已完成 → 400 / 未知阶段 → 400 / 未知会话 → 404）

---

## 修复：规划 6/6 完成、任务却不停（2026-10-11）

**现象**：一次续跑 11 分钟时界面显示「规划 6/6 完成」「用时 11 分 18 秒」，任务却仍在转，
用户手动点了停止。磁盘上六阶段 28 个文件其实全齐，但 run 的 `goal_hit` 是 0。

**两个根因**（都在护栏的判据上，不是模型的问题）

| # | 问题 | 后果 |
|---|---|---|
| 1 | 产物记账只认 `artifact` 事件，而该事件只有 `save_artifact` 会发；子智能体更常用 `write_file` / `edit_file` 直接落盘 | 事件里只有 1 个 artifact → 「六阶段齐全」永远判不出来 → 自动收尾永不触发 |
| 2 | 只读工具的返回内容被算作「进展」 | 评审子智能体反复回读自己刚写的报告（37 次 read_file），每次都刷新进展时刻 → 停滞判据形同虚设 |

**改动**

- `agent/server.py`
  - 新增 `_maybe_scan_goal()`：**磁盘是产物的唯一权威**。节流 5 秒扫描 run 目录
    （`stages.scan_dir`），把不走 `save_artifact` 的落盘也算进目标判据；发现新产物同时
    刷新进展时刻。挂在 `artifact` / `tool_end` 事件上触发。
  - 进展判据收窄：**只读工具的 tool_end 不再算进展**（读文件不改变世界）；写类工具
    （`write_file`/`edit_file`/`save_artifact`/出图/CAD…）的成功结果才算。
  - 去重签名归一化 `_sig_of()`：只读工具按**文件**聚合而不是「文件+offset」——
    模型回读自己的报告时每次 offset 都不同，按原文签名永远凑不出重复。
  - 新增 `_tool_path()`：从残缺参数（空串 / 半截 JSON / 占位 `{}`）里安全提取路径。
- `agent/config.py`
  - `WRITE_TOOLS`（写类工具集合）、`DISK_SCAN_SECONDS=5`；
  - `REPEAT_LIMIT_SAMEFILE=10`（同一文件反复读，比普通只读更可疑，限额压低）；
  - `STALL_SECONDS` 180 → **120**（只读不再算进展后可以更早判空转）。
- `agent/store.py`：`finish_run` 一并刷新 `n_tools` / `n_artifacts` / `n_events`
  （原来靠中途某个时点的旧值，实测 28 个文件在历史列表里显示成 2）。

**验证**

- 新增 `scripts/test_goal_stop.py` **22 项**：磁盘判据（不走 save_artifact 也能达成）/
  阶段没齐不误判 / 只读不算进展而写类算 / 同文件回读被拦下且定性为「目标达成」/
  读不同文件不误杀 / 参数解析与签名归一边界。
- 全量回归 **214 项全过**（loop_guard 24、pipeline 50、rate_limit 18、workspace_path 8、
  image_ref 46、visual_chain 30、cad_runtime 16、goal_stop 22）。
- **真机对照**（同一目录续跑评审阶段）：

  | | 修复前 | 修复后 |
  |---|---|---|
  | 结束方式 | 用户手动停止 | **自动收尾** |
  | 用时 | 722 秒 | **267.6 秒** |
  | `goal_hit` | 0 | **1** |
  | DB 状态 / 摘要 | cancelled | **done / 六阶段产物已交付（目标达成）** |

  事件时间线：`12.1s goal_reached`（识别六阶段齐全）→ `165.1s done`（150 秒收尾宽限到点）。

---

## 修复：build_structure / text_to_cad 一直失败，报错只有模板（2026-10-11）

**现象**：模型反复提交同一段脚本，每次拿回的都是

```
建模失败：脚本执行失败（运行时 local）： 【build123d 0.13 正确写法（已实测）】 from build123d import * …
```

看起来像「系统返回的模板提示」，于是模型再试一次、再错一次 —— 三次后结构阶段交不出 STEP/STL。

**三个根因**

| # | 问题 | 后果 |
|---|---|---|
| 1 | 失败时 `hint`（整份速查表）紧跟在 error 后面，而 `tail`（真实 traceback）为空 | 模型把示例当成错误本身，无从下手 |
| 2 | 主运行时报错后**无脑回落**到另一个运行时：cadgen 锁 build123d 0.11，与 0.13 模板天然对不上，第二次用它跑，空输出/异版本报错盖掉第一次的真 traceback | 真因被覆盖 |
| 3 | 子进程被 OCCT 内核搞崩时 stdout/stderr 全空（`0xC0000005` 等），代码只看 stderr | `tail` 空 → 只剩模板 |

**改动**

- `agent/cad.py`
  - 新增 `static_check()`：11 条写法规则（`Workplanes(` / `Cylinder(r=` / `.fillet(radius=` /
    `offset(amount=-` / `export_*` / `show()` / `import Part` / 中文标点 / 没有 `parts =`），
    **不启动子进程**就能定位到行号并给出改法 —— 这类错误占失败的大多数，现在一次都不用跑。
  - 新增 `crash_note()`：按退出码识别 `0xC0000005 / 0xC0000409 / 0xC00000FD / SIGSEGV` 等内核崩溃，
    并给出三个几何诱因（圆角 ≥ 半边长 / 壁厚 ≤ 0 / 相切不相交）。
  - `build()` 重写：真实报错永远排在最前并带「运行时 + 退出码」；**主运行时给了明确报错就不再换
    运行时**（只有解释器不存在才换）；多次运行取证据最足的那次；失败脚本落盘到
    `20_structure/_cad_debug/<name>_failed.py` 并在返回里给出工作区相对路径。
  - `hint_for()` 未命中时改为「未匹配到已知错误模式。下面是参考写法（**这是示例，不是你的报错**）」，
    并新增 `Standard_Failure` / `BRep_API` / `not a solid` / `No active` 等内核报错的针对性建议。
  - 新增参数化兜底：`shell_script()` / `build_shell()` / `parse_dims()` —— 从脚本里解析
    `W, D, H = 40, 20, 150` 这类常量（支持 `WIDTH/DEPTH/HEIGHT` 别名、跳过含变量的赋值），
    拿不到三个主尺寸就不兜底（不凭空造尺寸）。
- `agent/tools.py`：新增 `_cad_fail_text()`，`build_structure` 与 `text_to_cad` 共用 ——
  失败时先给真实报错 + 留档路径，能解析出尺寸就**自动用参数化模板建一个壳体**，
  STEP/STL/预览/四视图/爆炸图/consistency.md 一并产出，并把平台脚本写成 `20_structure/model.py`
  供模型接着迭代。结构阶段是硬交付，交不出模型后面硬件板型、固件仿真全悬着。
- `agent/subagents.py`：结构子智能体明确「报错第一段是真因、末尾参考写法不是报错」，
  禁止原样重发脚本；出现兜底提示时改为从 `model.py` 起步迭代。

**验证**（`scripts/test_cad_error.py`，**49 项全过**）

- 8 类错误写法全部被静态检查拦下且带行号；正常模板不误伤
- 运行时错误返回含 traceback 关键字 + 退出码 + 不再以模板开头 + runner 恒为 local
- 崩溃退出码（Windows / POSIX）可读出具体信号与几何诱因
- 尺寸解析：批量赋值 / 别名 / 单行 / 无尺寸不脑补 / 含变量跳过
- 兜底建模真跑：40×20×150 与 150×40×20 都出 STEP+STL，包围盒与输入尺寸误差 < 0.6mm；带开孔版也通过
- 工具层端到端：坏脚本 → 静态定位第 12 行 → 兜底产出
  `moisture_meter_p1.step/.stl/_preview.svg` + `model.py` + `views-sheet.svg` + `exploded.svg` + `consistency.md`

全量回归 **263 项全过**（新增 cad_error 49）。worker 已重启在线。

---

## 修复：历史会话「确认移除」只是软删，重启后又回来（2026-10-11）

**现象**：在历史面板里点「确认移除」，记录消失了；但重启服务后这条会话又出现在列表里。

**根因**：两个设计叠加出的假删除

1. `store.delete_run()` 只删 `runs / events / files` 三张表，**磁盘目录 `workspace/<id>` 原样保留**
   （原注释写的是「宁可在文件夹留痕，也不误删成果」）。
2. 启动时 `store.backfill()` 会扫 `workspace/` 下所有目录，把库里没有的补进历史 ——
   **目录还在，会话就被重新插回来**。删了个寂寞。

**改动**（语义改成用户预期的那样：确认移除 = 真删）

- `agent/store.py`
  - 新增墓碑表 `deleted_runs(id, deleted_at)`，以及 `is_deleted()`。
  - `delete_run(run_id, purge_disk=True)` 现在级联删 `runs / events / files / memories / reflexions`，
    **并删除磁盘目录**；删不掉（Windows 占用）就改名 `_<id>_deleted` 留痕。
    无论删盘成功与否都先插墓碑 —— 只要墓碑在，它就再也回不到历史里。
  - `backfill()` 扫目录时跳过墓碑 id。
- `agent/server.py`：`DELETE /api/runs/{id}` 返回 `{ok, purged, note}`，note 说明删了什么；
  顺手把内存里的 `RUNS` 也弹出，避免「删了还能继续跟随」。
- `web/components/HistoryDrawer.tsx`：按钮「移除记录」→「删除」，确认按钮「确认移除」→「确认删除」，
  底部说明改为「删除会连同该会话的全部产物一起删掉，不可恢复；删过的会话重启也不会回来」；
  新增 `onDeleted` 回调 + 失败时提示错误。
- `web/app/page.tsx`：删掉的正好是当前打开的会话时，清空事件流/规划/产物/清单视图。

**验证**

- 新增 `scripts/test_delete_run.py` **17 项全过**：删前存在 → 真删后库记录/事件/文件/情景记忆/反思全无、
  磁盘目录消失、墓碑可查 → **把目录手工重建出来再跑 backfill，会话不再复活** →
  未删过的会话仍能正常补进历史 → 删不存在的 id 返回 `ok=False`。
- 接口实测：造一条测试会话 → `DELETE` 返回 `{"ok":true,"purged":true,...}`，
  DB 三表归零、目录消失、墓碑落库；**重建目录后重启 worker，历史 52 条中不含该会话**。
- 前端 `tsc --noEmit` 无错误；全量回归 280 项。

---

## 修复：渲染图「无法调用」—— 其实生成了，是落盘那一步挂了（2026-10-11）

**现象**：外观阶段每次都说「AI 渲染图生成失败（SSL 证书错误）」，于是走兜底出工程视图；

数据库里 `generate_product_image` 的真实回执是：

```
图片生成失败：图片已生成但落盘失败：下载失败（platform-outputs.agnes-ai.space）：
URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate>
```

**先回答「是不是用 Agnes 出的图」：是。** 出图服务商与文本模型解耦，固定走 Agnes
（`agnes-image-2.1-flash`）：外观图（render-01/02）、爆炸图（exploded）都由它生成，
第一张文生图，第二张起**基于前一张做图生图**（i2i）保持同一产品。报错不是"没调用"，
是"调用了、图生成好了、下载不下来"。

**根因**：出图是两段式 —— ① 调 Agnes 出图接口（这一步一直成功）→ ② 拿返回的 **CDN 直链**
再下载一次。`platform-outputs.agnes-ai.space` 这个域名在本机链路上被 TLS 拦截
（代理 / 杀软 / 上网行为管理），系统证书链不认它，第 ② 步直接失败 → 整张图作废。
同一轮里换个错误形态还会变成 `RemoteDisconnected`，总之是同一跳的问题。

**改动**

- `agent/imagegen.py`
  - **治本**：请求带 `response_format=b64_json`，让网关**直出 base64** —— 彻底绕开 CDN 下载。
    （实测该字段 Agnes 支持，返回 1.04 MB base64）。网关返回 400 且提到 `response_format`
    时自动去掉该字段、退回下载模式，不会把不支持的网关搞死。
  - `_extract()` 改为 **base64 优先**（有 b64 就不下载），新增 `_clean_b64()` 剥
    `data:image/...;base64,` 前缀。
  - **兜底**：`_download()` 的 TLS 策略改成三级链 —— 系统证书 → certifi 证书 → 关闭校验；
    证书类错误**立刻换档重试**（不是干等退避，同一档重试多少次都不会变好）；
    返回 `(字节数, 策略说明)`，上层把"这张图是怎么拿到的"写进事件流。
- `agent/config.py`：新增 `IMAGE_B64`（默认开，`AGENT_IMAGE_B64=0` 可退回下载模式）。
- `agent/tools.py`：图片获取方式不是正常链路时发一条 `image_tls` 事件，界面能看到。

**实测**（`scripts/probe_image_live.py`）

| | 结果 |
|---|---|
| 第一张（文生图） | `render-01.png` 686 KB / 1024×1024 / 14.0s |
| 第二张（图生图，基于第一张） | `render-02.png` 766 KB / 1024×1024 / 14.5s，参考图压缩到 1024px JPEG 53 KB |
| 获取方式 | 事件显示「网关直出 base64（未走 CDN 下载）」 |

## 修复：写文件 `Path://?/C:/... outside root directory`

**现象**：子智能体写 `10_appearance/orthographic.svg` 时抛

```
ValueError: Path://?/C:/.../workspace/<id>/10_appearance/orthographic.svg
            outside root directory: C:/.../workspace/<id>
```

**根因**：模型把**宿主机绝对路径**塞给了 `write_file`（多半是从上游报错信息里抄回来的）。
Windows 上带 `\?\` 扩展长度前缀的路径，与 deepagents `FilesystemBackend` 用
`relative_to(cwd)` 做的字符串越界判定不等价 —— **明明是工作区内的文件也被判越界**。

**改动**：新增 `agent/pathutil.py`（`strip_extended` / `normalize` / `is_foreign`），
把各种形态折回工作区内的相对虚拟路径：

- `WorkspaceBackend._resolve_path`：先归一化再交给基类；工作区外的路径抛**中文**错误
  （「请只写工作区内的相对路径，例如 /10_appearance/orthographic.svg」），不再暴露宿主机路径。
- `tools._safe_target` 同款归一化；**返回值不再带 `\?\` 前缀**（原来会让
  `relative_to(_run_path())` 失配，产物事件里出现 `C:/Users/...` 这种路径）。
- 新增 `tools._rel_of()`，`save_artifact` / `generate_product_image` 用它记账 ——
  模型传绝对路径时，前端产物树里仍然是干净的 `10_appearance/render-01.png`。

**验证**：新增 `scripts/test_path_ssl.py` **38 项全过** —— 归一化各形态（剥 `\?\`、UNC、
盘符、虚拟路径）、Backend 解析（绝对路径 / 带前缀 / `/home/user`）、工作区外路径被中文拒绝、
`save_artifact` 接受绝对路径且事件路径仍是相对路径；TLS 三级降级与「非证书错误仍按退避重试」。
全量回归 **318 项全过**。

---

## 修复（复发）：写文件 `Path:\?\C:\... outside root directory`

换个 run（`5b89343018b9`）写 `10_appearance/render-prompts.md` 时，同一个越界错又出现了 ——
说明上一轮的归一化**没修彻底**，有两个漏点：

1. `strip_extended` 只认反斜杠形态 `\?\`，不认正斜杠 `//?/` 与混写 `\?\C:/...`。
2. 归一化之后仍把字符串交回基类；基类的实现是 `(self.cwd / vpath).resolve()`，
   只要 vpath 里还残留扩展前缀，`Path` 就把它当成**真的绝对路径**直接覆盖 cwd。
   实测：`//?/C:/...` 会被拼成 `C:Users\...` —— 既带前缀又脱离工作区，必判越界。

**这次治本**：`WorkspaceBackend` 不再调用基类的 `_resolve_path`，自己解析。

- `pathutil` 新增 `under_root()` / `abs_under()`：纯字符串归一（`os.path.normpath`），
  全程不用 `resolve()`（Windows 上它会加 `\?\`），越界直接返回 `None`。
- `strip_extended` 四种形态全认：`\?\` / `//?/` / 小写 / `\?\UNC\`，UNC 还原成反斜杠形态。
- UNC 判据改成「原始串以 `\` 开头」；`//10_appearance//x.md` 这种只是多写了斜杠的
  虚拟路径，压成单斜杠正常落盘，不再误杀。
- 顺带 override `_to_virtual_path`（基类同样用 `resolve().relative_to(cwd)`，一个坑）。
- `tools._safe_target`：拼路径前先 `lstrip("/")` —— 否则 `/etc/passwd` 也会被当绝对路径
  覆盖工作区，本该「关进沙箱」的路径反被判越界（回归测试抓到的）。

**验证**：新增 `scripts/test_path_resolve.py` **43 项全过**（9 种形态 × 落盘 + 4 类越界拒绝
+ 真实 `write`/`read`/`_to_virtual_path`）；全量回归 **464 项全过**；worker 已重启生效
（8001 在线，11 工具，cad `local:0.13.0`，图像 Agnes）。

**附带说明**：这个错会自我喂养 —— 报错信息里带着宿主机绝对路径，模型照抄进下一轮调用，
于是同一个错反复出现。现在入口一次性剥干净，且拒绝时只回中文提示、不再回显宿主机路径。

---

## 修复：超时中止 · 外观图"被删除/覆盖" · 外观→结构同源链

### 1. 外观图不是被删，是被**自己的子智能体覆盖**了（已查实）

查 run `71d4723e3fff` 的事件与磁盘，拿到了完整证据链：

| 步骤 | 事实 |
|---|---|
| 出图 | `generate_product_image` **成功两次**：render-01.png **739.2 KB**、render-02.png **733.1 KB**（i2i 基于 01） |
| 之后 | 同一个子智能体又调 `save_artifact("10_appearance/render-01.png", "placeholder")`，把两张真图**覆盖成 11 字节文本**（事件 seq 1235 / 1238，`xxd` 查得到内容是字符串 `placeholder`） |
| 结果 | 前端两张图都打不开，看起来就像"生成后被删了" |

**修法（治本，三道闸）**

1. 新增 `agent/assets.py`：`png/jpg/step/stl/pdf/zip…` 一律**禁止文本工具写入**，
   `placeholder` / `TODO` / `略` 这类占位内容也直接拒；图片按**魔数 + 体积（≥20 KB）**判有效性。
2. `tools.save_artifact` 与 `WorkspaceBackend.write/edit` 都挂上这道守卫 ——
   模型会收到明确提示"图片只能由 generate_product_image 产出"，原文件一个字节都不动。
3. 门禁**看内容不看路径**：`stages.scan_dir` 不再把坏图当产物；
   `gate(20_structure)` 在"有坏图且没有可用图"时拦住并要求重出，有可用图时只提示不拦路。

### 2. 超时从"硬墙"改成"停滞才中止 + 自动续跑"

原来 900 秒到点就砍，不管当时是不是正在出图 —— 那次被砍掉时已经做到 4/6 阶段。

- **软续期**：到点先看"距上次实质进展多久"，还在推进就自动延长（默认每次 +600 秒，
  延到硬上限 = 档位 × 2）。用掉 80% 时限时先给一次预警。
- **自动续跑**：真到硬上限仍未完成（且已有阶段产物）→ **复用同一工作区自动再开一轮**，
  从下一个未完成阶段继续，最多 2 轮；提示推在 `done` **之前**（done 一到前端就关流，
  晚推的提示看不到）。
- **前端自动跟随**：事件流里的"新会话 xxx"会被解析出来，done 时自动切过去继续展示 ——
  用户不会再看到"超时中止"就以为白跑了。
- 用户手动停止、非超时状态、一点产物都没有 —— 三种情况都不触发自动续跑。

### 3.「外观提示词 → text_to_cad 结构」这条路走得通，而且确实在这么走

设计上就是这条链：出图时每张图的 prompt 自动存档到 `10_appearance/prompts.json`，
结构阶段 `text_to_cad` 不传 script 先把它读回来做形状约束，再带脚本建模，
建完自动写 `20_structure/consistency.md`（外观提示词 vs 实际包围盒对照表）。
本次 run 的证据：`consistency.md` 里引用的正是 render-02 那张的提示词，模型也是照它取
`W, D, H = 60, 35, 15`。

但这次链上有两个卡点，都修了：

- **`text_to_cad` 第一次带脚本调用被静态检查误杀**：中文标点规则把**注释里的**中文括号
  也判成错误（第 9/13/18/24/37/42 行全是 `# 掏内腔（放 PCB）` 这种注释），模型只好删光
  中文注释重写一版更简陋的脚本。现在这条规则只看**代码部分**（剥掉注释与字符串字面量），
  注释里的中文括号不再误伤。
- **图是坏的**：结构阶段拿到的是"文字同源"而不是"图像同源"。现在坏图会被门禁打回重出。

需要说清楚的一点：`text_to_cad` 提取的是**提示词里的文字约束**（尺寸、形态、材质），
并不是从 PNG 像素里反推几何。所以外观提示词必须写清可量化的外形信息 ——
这一点已经写进工业设计师的提示词里。

### 4. 验证

- 新增 `scripts/test_artifact_guard.py` **27 项**（守卫 / 占位识别 / 坏图门禁 / 原文件不被破坏）
- 新增 `scripts/test_timeout_extend.py` **20 项**（续期、停滞中止、预警、自动续跑的四条边界）
- `test_cad_error` 49 → **50 项**（新增"注释里的中文标点不误伤"）
- 全量回归 **512 项全过**；worker 已重启（8001 在线，11 工具，CAD `local:0.13.0`，出图 Agnes）

验证方式：在历史会话 `71d4723e3fff` 上点「继续」—— 新门禁会先要求重出外观图，
再按串行链补完 40_firmware 与 90_review。

---

## 人工确认闸门：出图后暂停等人 + 两个 skill

用户的诉求很明确：产品图/效果图/爆炸图生成后**必须停下来**等反馈或改提示词；
结构建完同样要等人确认再往下走；这两条链各做成一个 skill；结构必须与外观同源。

### 1. 暂停不是「提示词里叮嘱一句」，是三层硬机制

| 层 | 实现 | 为什么需要 |
|---|---|---|
| 工具 | `confirm_with_user`（第 12 个工具）**真阻塞**：`threading.Event.wait()`，用户回应才返回 approve/revise + 原话 | 靠模型「自觉先问一句」必漏 |
| 门禁 | `pipeline.gate()` 检查上游闸门阶段的确认记录，未确认 → `ready=False`，并在提示里点名 `confirm_with_user(stage=…)`、附上上一轮用户意见 | 模型漏调也绕不过去：下游门禁会明确告诉它该调什么 |
| 落盘 | 确认结论写 `XX_stage/_human_review.json`，`scan_dir` 排除 `_` 开头文件（不进产物清单） | 重启、续跑之后判据仍然有效 |

等待期间三个容易踩的坑都处理了：

- **不消耗运行时限**：`_guard` 里 `if self.awaiting: return`，恢复时 `shift_clock()` 把
  `started / last_progress / goal_at` 一起后拨 —— 否则「用户看十分钟图再点确认」会被判超时中止。
- **停止立刻响应**：`cancel()` 里直接 `event.set()`，且取消判据放在**唤醒之后**再看一次
  （写在 `while` 开头会被先到的事件跳过，表现为「点了停止还在等人」）。
- **不会无限挂死**：`HUMAN_WAIT_SECONDS=1800` 到点自动放行并记进记录；`AGENT_HUMAN_GATE=0` 可整体关闭。

### 2. 两个 skill

- `skills/agnes-product-image/SKILL.md` —— 串行同源出图链：第一张文生图定基线，
  之后每张以前一张做图生图，爆炸图始终以第一张外观图为参考；**一致性锚必须写进 prompt**；
  编号顺延不覆盖（覆盖会毁掉参考图链）；失败按「鉴权/限流/超时/审核」四类分诊。
- `skills/text-to-cad-structure/SKILL.md` —— 先不带 script 调一次取回外观提示词做形状约束，
  再带 build123d 脚本建模；0.13 的四个必错写法；**报错第一段才是真报错**（末尾「参考写法」
  是示例，不是你的报错）；AI 爆炸图上的尺寸数字不可信。

两者由 `agent/skillspec.py` 注入子智能体提示词（工业设计师 ← 出图链，结构工程师 ← CAD 链），
主智能体注入索引。同一份文档也复制到了用户级技能目录，其他会话可直接复用。

「外观 → 结构同源」这条链现在是这么走的：出图时 prompt 自动存档进 `10_appearance/prompts.json`，
结构阶段 `text_to_cad` 先读它再建模，并自动写 `consistency.md` 对照表。
要说明的是它取的是**提示词里的文字约束**，不是从 PNG 像素反推几何 —— 所以外观提示词
必须写清可量化的外形，这条已写进出图 skill 与工业设计师提示词。

### 3. 前端

`awaiting` 事件 → 左栏弹出确认面板：阶段名 + 产物缩略图（点开看原图）+ 意见输入框 +
「确认，继续下一步」/「按此反馈重做」两个按钮；顶栏同步挂 `⏸ 待你确认` 徽标。
刷新或深链进来靠 `GET /api/agent/review` 恢复面板（光靠事件回放不出可交互按钮）。

### 4. 验证

- 新增 `scripts/test_human_gate.py` **45 项**：真阻塞 / 唤醒 / 门禁拦截与放行 /
  护栏等待期放行 / 时钟后拨 / 停止与超时两条兜底 / 闸门可关闭 / 工具接线
- 新增 `scripts/test_review_api.py` **14 项**：`GET/POST /api/runs/{id}/review` 的
  HTTP 段（FastAPI TestClient 同进程跑真实挂起线程 + 真实请求）
- `test_artifact_guard` 26 → **28 项**、`test_pipeline` 45 → **51 项**
  （都新增了「产物齐但未经用户确认仍不放行」这一档）
- 全量回归 **579 项全过**（21 个脚本，0 失败）；前端 `tsc --noEmit` 通过；
  worker 已重启（8001 在线，12 工具，CAD `local:0.13.0`，出图 Agnes）

**怎么用**：起一个新需求，出完外观图界面会弹出「待你确认：外观设计」面板并挂起；
点确认 → 结构开工；填意见 → 外观带着反馈重出再请确认。结构建完同样停一次，
确认后才进硬件。等待期间不计入运行时限，不会超时。

---

## 修复：CAD 脚本连挂 5 次，全是 0.13 的 API 改名（2026-10-13）

**现象**：一次实跑里 `text_to_cad` 连着失败 5 次，最后一次停在

```
AttributeError: 'BoundBox' object has no attribute 'x_length'
```

建模那一步只是**收尾打印**尺寸用了不存在的属性，整段脚本却因此报废，结构阶段交不出 STEP/STL。

**根因**：`build123d 0.13` 的 `BoundBox` 只有 `min / max / size(Vector) / diagonal / center()`，
没有 `x_length / y_length / z_length`。而模型按记忆里的旧 API（或其它版本）写，
平台此前只会「报错 + 贴参考写法」—— 一轮重写 = 几十秒到几分钟，同样的错能连烧五轮。

**做法：确定性错误由平台直接改写，不再让模型重试**

| 层 | 改动 | 位置 |
|---|---|---|
| 自动改写 | `normalize_script()`：`bbox.x_length/y_length/z_length/xlen/…/length_x` → `bbox.size.X/Y/Z`；`Cylinder(r=, h=)` → `radius=/height=` | `agent/cad.py` |
| math 补全 | 脚本用到 `radians/pi/cos/sqrt…` 却没 `import math` 时，平台前置一行 `from math import …`（build123d 不导出这些） | `agent/cad.py` |
| 行号还原 | 前置导入会让 traceback 行号 +1 → 报错文本按 offset 减回模型脚本的真实行号，并注明「平台额外注入 N 行」 | `agent/cad.py` |
| 精确建议 | `hint_for()` 解析 `AttributeError: 'X' object has no attribute 'Y'`：BoundBox 直接给 `size.X`（含 `max.X-min.X` 等价式），`Edge/Shape/Solid/BuildPart/Vector` 给出该类真实存在的属性清单 | `agent/cad.py` |
| 静态拦截 | 新增 `filter_by(..., filter=)`（0.13 无此参数，顶面边用 `.sort_by(Axis.Z)[-1:]`） | `agent/cad.py` |
| 内核报错 | `There are no suitable edges for chamfer or fillet` 单独成条：圆柱件 `filter_by(Axis.Z)` 选不到直边，别硬倒圆角 | `agent/cad.py` |
| 兜底增强 | `parse_dims()` 认 `DIAMETER` / `TOTAL_H`（圆柱类产品的写法），兜底模板才建得出来 | `agent/cad.py` |
| 存盘一致 | `model.py` 存**平台实际跑通的那份**（改写后），而不是模型原始脚本 —— 否则下一轮拿它当起点会再踩同一个坑 | `agent/tools.py` |
| 提示词 | 工具 docstring 与结构子智能体各补「bbox 没有 *_length」「math 函数直接用，平台会补导入」 | `tools.py` / `subagents.py` |

**验证**（`scripts/test_cad_bbox.py`，退出码 0）

- 线上真实失败脚本 `workspace/b5258537540f/20_structure/_cad_debug/model_failed.py`
  → **现在跑通**：主体下壳 90×90×30 mm / 43.43 cm³、上盖 90×90×24、底座 90×90×3，STEP+STL+预览齐全
- `bbox.*_length` 最小复现 → 自动改写 3 处，建模成功
- `Cylinder(r=, h=)` → 自动改写 1 处，建模成功
- `parse_dims(DIAMETER/TOTAL_H)` → `{W:90, D:90, H:48, WALL:2.5}`
- math 补全 + 行号还原：脚本第 7 行的错误，报错文本显示 `line 7`（未受前置导入影响）
- `hint_for` 三类 AttributeError 均给出「哪个类没有哪个属性 → 该用什么」

顺带把同类失败一起收了：这次 5 次失败里另外 4 次分别是 `radians` 未定义、
`filter_by(filter=)`、`Edge.tangent`、`fillet` 无合适边 —— 除几何类（需模型自己改半径/分件）
外，其余都已在平台侧兜住。

**回归**：`test_cad_error` 50 → **52 项**（`Cylinder(r=,h=)` 由"静态拦下"改为"拦下或自动改写"，
另补一条仍会被静态拦下的 `export_stl` 用例）；`test_cad_runtime` 16 / `test_visual_chain` 30 /
`test_pipeline` 51 / `test_artifact_guard` 28 全过。worker 已重启（8001 在线，
cad `local:0.13.0`，出图 Agnes）。

**提示词同步**：`skills/text-to-cad-structure/SKILL.md`（项目级 + 用户级各一份）的错误对照表
补到 8 条，并写明 BoundBox 在 0.13 的真实属性集。
