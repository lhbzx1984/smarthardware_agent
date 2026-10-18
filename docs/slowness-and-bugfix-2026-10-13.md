# 「跑得慢」定位 + 四个真 bug 修复

日期：2026-10-13 · 涉及 `agent/config.py`、`server.py`、`main_agent.py`、`store.py`、新增 `agent/gateway.py`

---

## 一、先定性：慢的是网关，不是代码

| 时间 | flash 首字节 | pro-alpha | /models 探活 |
|---|---|---|---|
| 10-12 晚 | ConnectTimeout（60s 连接超时都建不上） | 偶发 41.2s | **不通** |
| 10-13 早 | **0.88s** | 4.5s | 通 |

同一个网关、同一个 key，隔夜差了两个数量级。**遇到「突然变慢」先探网关，不要先改代码。**
判定方法：`agent/gateway.py::reachable()`（GET /models）。

但代码把这次拥塞**放大**了 —— 这才是要修的部分。

---

## 二、四个 bug

### 1. 主智能体仍在重复查门禁（提示词拦不住）

现象：run `22404139186a` 事件流里，同一个 `00_prd` 阶段被查了两次门禁。

```
+51.9s   主智能体  stage_gate(00_prd)
+225.4s  子智能体  stage_gate(00_prd)   ← 同一阶段，结论一模一样
```

改了三轮提示词（"不要为了确认开工去查门禁"）都无效。**结论：模型不守的规矩要从工具层堵。**

修复：`main_agent.MAIN_TOOLS` 从 `[stage_gate, confirm_with_user]` 改成 `[confirm_with_user]`。
主智能体根本拿不到这个工具，门禁交给子智能体在自己第一步自查（它本来还需要拿上游硬约束）。

验证：新一轮 run 里 4 次 `stage_gate` 全部 `agent=tools`（子智能体），主智能体 **0 次**。

### 2. 僵尸 running 会话

进程被杀、崩溃、关机时走不到 `finish_run`，DB 里永远留着一条 `status='running'`。
前端「历史会话」按 status 显示 spinner → 一个永远在转、点开什么都没有的会话。

修复：`store.reap_orphans()`（带 error 文案），由 `store.init()` 调用。
原来 `init()` 里内联了一段同样功能的 UPDATE 但不写 error，两处重复会互相覆盖，已合并。

### 3. 事件时间倒流（实测 31 次，最大 -21.5s）

根因**不在** push，在聚合落库：`Run._flush_agg()` 写的是 `"ts": rec["first"]`。
thinking/token 攒够 `AGG_SECONDS` 才落库，此时它的 seq 比这期间推走的所有事件都大，
ts 却是几秒甚至二十几秒之前 → 前端按 ts 排序会把整段思考插到上一条之前。

修复：
- `_flush_agg()` 落库改 `time.time()`，首帧时间留 `ts_first`
- `Run.push` / `main_agent.push` 由 `setdefault("ts", ...)` 改**覆盖**（聚合事件 payload 自带首帧 ts）

验证：修复前 31 次倒流（最大 -21.5s）→ 修复后 **0 次**。

### 4. `/health` 500

```
TypeError: 'NoneType' object is not iterable
  store.py:867 in stats → **dict(row)
```

启动瞬间 `/health` 与建表/迁移线程抢同一个连接，`fetchone()` 取到 None。
后果是前端把「服务正常」显示成「服务异常」。

修复：`stats()` 里所有聚合结果兜底（row / avg / 范式 hit·tools·sec 全部 `or 0`），
取不到就按空库返回 —— 健康检查不该因为统计口径崩掉。

验证：重启后连续 6 次探活全 200，ASGI 异常 0 条。

---

## 三、顺带做的提速（10-12 晚）

### 重试次数在相乘

`LLM_MAX_RETRIES=4`（SDK 层）× `NET_RETRY_MAX=4`（编排层）= **最多 16 次请求**，
网关一拥塞全是空转，每次还要等满连接超时。

| 参数 | 原值 | 现值 | 理由 |
|---|---|---|---|
| `AGENT_LLM_MAX_RETRIES` | 4 | 1 | 编排层已负责整体重跑，SDK 留 1 次挡单次抖动即可 |
| `AGENT_NET_RETRY_MAX` | 4 | 3 | 3 次足够，再多无益 |
| `AGENT_NET_RETRY_BACKOFF` | 12s | 8s | 8/16/24，少白等 |
| `AGENT_NET_FALLBACK_AFTER` | 2 | 1 | 第一次失败就换模型，别在同一条队列排两次 |
| `AGENT_LLM_CONNECT_TIMEOUT` | 15s | 25s | 15s 在拥塞时几乎必失败 |

### 开跑前的模型选路（新增 `agent/gateway.py`）

同一个网关下不同模型的排队深度差一个数量级，**而且随时间变**（昨天 flash 12.8s / pro-alpha 1.4s，
今天两个都堵）。写死模型名等于押运气。`pick_fastest()` 开跑前并发探测候选，选最快那个：
一次几十秒，换后面二三十轮往返全走快模型。

两个必须踩过的坑：
- `MODEL_RACE_TIMEOUT` 给 40s 不够 —— 实测 pro-alpha 41s 才出首字节，会被误判淘汰。设 60s。
- 判「网关挂了」要谨慎：`reachable()` 超时 8s 会把「只是慢」误判成死（/models 本身也要十几秒）。
  现在给 20s，且隔 10s 再问一次才判死。

### 重跑要带上下文

网络中断后重跑，智能体不知道上一轮做到哪 —— 会重新读需求、重新写 PRD、重新出图。
产物有幂等锁不会覆盖，但每轮都要白烧好几次往返，这就是「重试一次就慢一截」的来源。

`_resume_hint_for_retry()` 把 `run.requirement` 换成：
「已完成 N 个阶段（…），产物均已落盘，不要重做，直接从 X 继续」。

---

## 四、验证方式（以后复用）

```bash
# 1. 网关是不是锅
python -c "import gateway,config; print(gateway.reachable(config.DEFAULT_BASE_URL, config.fallback_key(config.DEFAULT_BASE_URL)))"

# 2. 端到端：开跑 → 轮询事件
POST /api/run            → run_id
GET  /api/runs/{id}/events → 统计 tool_start 里 stage_gate 的 agent 归属 + ts 单调性
POST /api/runs/{id}/cancel → 收尾
```

## 五、已知限制

- 网关整体不通时现在会**直接不开跑**并明确报错（而不是空转十几分钟再失败）；偶发抖动误判的话，重跑一次即可。
- 模型选路的候选默认「当前模型 + `AGENT_FALLBACK_MODEL`」，可用 `AGENT_MODEL_RACE_CANDIDATES` 加（逗号分隔，必须是同一网关可用的名字）。
