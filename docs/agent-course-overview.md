# 交付说明：专用智能体系统设计（流程 / 提示词 / Harness）

## 交付物

| 文件 | 说明 |
|---|---|
| `docs/agent-system-design-course.html` | 交互式教学报告（单文件、离线可用、light 主题） |
| `docs/agent-course-overview.md` | 本说明 |

报告以本仓库的「智能硬件设计智能体」为唯一案例，八章：

0. 开篇：专用化 = 流程 + 提示词 + Harness，任务 DNA 四档路由
1. 系统全景：架构图 + 三条边界（调度者不执行 / 上下文隔离 / 磁盘是唯一权威）
2. 流程设计：六阶段链（可点击看契约与门禁）、约束链、四条经验
3. 提示词设计：五段式骨架、COMMON_RULES 逐条配真实事故、主/子智能体提示词原文、兜底链
4. Harness 设计：工具白名单 → 门禁 gate() → 幂等状态机 → 人工闸门 → 预算护栏 → 网络层 → 事件流
5. 失败模式博物馆：9 个真实事故（现象 → 根因 → 修复 → 教训）
6. 落地清单 12 项（localStorage 记录勾选进度）
7. 随堂测验 6 题（含解析）

交互：侧边目录滚动高亮、阅读进度条、代码块复制、阶段链点击、事故卡展开、
人工闸门模拟器（演示 rounds 去重）、预算加权模拟器（只读 0.1 / 昂贵 1.0，对比 150 档位）、清单与测验。

## 本轮顺带修的三个问题

1. **LLM 网关排队被误判为网络中断**（`OpenAIConnectionError`）
   - 新增 `scripts/diag_llm_conn.py` / `scripts/diag_chat.py` 分层探测：DNS / TCP / TLS / GET /models / chat / stream
   - 实测：链路全通，但 `/chat/completions` 单次 12.8s（flash）、1.4s（pro-alpha），流式首字节 17.4s，偶发 >60s 断开
   - 修复：connect 15s / read 120s 分离超时；编排层退避重试 4 次（12/24/36s，等待不计入运行时间）；
     连续 2 次失败后自动切 `AGENT_FALLBACK_MODEL`（切换前校验目标模型在该网关存在）
2. **`/health` 500**：`store._connect()` 把未初始化的连接挂到全局后被并发读到，
   `count_reflexions()` 的 `fetchone()` 返回 None → 改为局部建好再赋全局，只读统计加兜底
3. **前端 datalist 同 key 告警**：网关 `/models` 返回重复 id → 去重 + 复合 key

## 验证

- `python -m compileall -q agent/` 通过
- worker 重启后 `/health` 连续探测 5 次全部 200，日志无异常
- 配置生效确认：`read=120 connect=15 net_retry=4 fb_after=2 fallback=agnes-2.5-pro-alpha`
- 报告页无头 Chrome `--dump-dom` 冒烟：动态区块全部渲染，模拟器与计分初始化正常
