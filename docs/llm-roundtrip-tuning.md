# 「跑得很慢 + 阶段重复执行」定位与修复

日期：2026-10-12 · 针对真实运行日志（run `11683314ca53`）

## 一、定性：慢的不是代码，是网关排队

用 `scripts/bench_model.py` 实测各模型首字节（TTFT）：

| 模型 | thinking | 首字节 | 总耗时 |
|---|---|---|---|
| agnes-2.5-flash | on | 72.4s | 连接失败 |
| agnes-2.5-flash | off | 49.8s | 50.2s |
| agnes-2.5-pro-alpha | on | 43.8s | 44.5s |
| agnes-2.5-pro-alpha | off | 45.2s | 45.4s |
| agnes-2.5-pro | on | 43.1s | 44.5s |
| agnes-2.5-pro | off | 81.9s | 连接失败 |

三个模型都在 43~82 秒区间 —— **换模型救不了，是网关在排队**。

于是本项目的性能公式变成：

```
一次完整六阶段 ≈ 主智能体 8 轮 + 子智能体 20~40 轮
每轮 45~80 秒  →  总计 20~40 分钟
```

结论：**优化的第一原则是砍 LLM 往返次数**，不是调超时、不是换模型。

## 二、实测到的重复往返

从事件流时间戳读数（run `11683314ca53`）：

| 时刻 | 事件 | 间隔 |
|---|---|---|
| T+0s | run_start | — |
| T+52s | 主智能体 `write_todos` | 52s |
| T+91s | 主智能体 `stage_gate`（00_prd） | 39s |
| T+214s | 子智能体 `stage_gate`（00_prd）← **与上一次重复** | 123s |
| T+350s | 子智能体继续思考 | 135s |

每个阶段门禁被查了 **2 次**：主智能体查一次决定派谁，子智能体开工又查一次。
第二次的结论与第一次完全一样（产物没变），纯粹多烧一轮往返。

## 三、修复

| 改动 | 文件 | 作用 |
|---|---|---|
| 主智能体不再为「确认顺序」调 stage_gate | `agent/main_agent.py` | 每阶段省 1 轮往返；门禁交给真正需要上游约束的子智能体 |
| write_todos 与下一步 task 同一条消息并行发 | `agent/main_agent.py` | 每阶段再省 1 轮往返 |
| COMMON_RULES：stage_gate 每阶段只调一次 | `agent/subagents.py` | 堵住子智能体中途「再确认一下」 |
| `AGENT_THINKING` 开关接通 | `agent/main_agent.py` + `.env` | 见下 |
| `AGENT_STANDARD_SECONDS=2400` | `.env` | 历史完整 run 实测 1040s / 1411s，900s 时限一路告警 |

六阶段合计：往返从 ~12 次多余开销降到 0，按 45s/轮算省 **9 分钟以上**。

### 顺手修掉的死开关

`main_agent.get_model()` 里原本写的是 `s.get("thinking", True)` —— 前端不传 `thinking`
就恒为真，而 `config.THINKING`（读 `.env` 的 `AGENT_THINKING`）**全项目没有任何消费点**。
也就是说 `.env` 里配 `AGENT_THINKING=0` 一直不生效，想关深度思考只能改代码。

已改为：前端没显式给时回落 `config.THINKING`，并设 `AGENT_THINKING=0`
（默认关深度思考；前端设置里仍可单独打开）。

## 四、验证方式

不用跑完整任务，直接查提示词：

```python
import main_agent as m, subagents as s, config
print('不要为了「确认能不能开工」自己去调 stage_gate' in m.MAIN_PROMPT)   # True
print('write_todos 和下一步的 task 并行调用' in m.MAIN_PROMPT)            # True
print('每阶段只调这一次' in s.COMMON_RULES)                               # True
print(config.THINKING)                                                    # False
```

跑起来之后，看事件流里 `stage_gate` 的出现次数：**每个阶段应当只有 1 次**（子智能体发起）。
出现 2 次就说明提示词没被遵守，需要把门禁结论改由主智能体在派单时下发。

## 五、已知限制

- 网关排队属于上游问题，本轮改动只能减少往返次数，无法让单次往返变快。
- 那次 run 最终 `cancelled`：网关 `OpenAITimeoutError` 连续重试 4 次仍失败后中止。
  产物已落盘在 `workspace/11683314ca53`，可从下一阶段续跑。
