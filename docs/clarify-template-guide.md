# 需求补全问卷：固定骨架 + 按需求定制选项

## 一、这次要解决什么

两个诉求看着矛盾，其实要一起满足：

- **问卷要跟着产品需求走** —— 做花盆和做工控节点，选项不该是同一批。
- **框架要跨产品稳定** —— 第十次打开时分区和字段还是那几项，用户能形成肌肉记忆，
  sticky「记忆」也能跨项目复用，下游需求简报的结构也才稳定。

做法是拆成两层：

| 层 | 谁定 | 变不变 |
|---|---|---|
| **骨架**（5 个分区、16 个字段、key / 题型 / 顺序 / 必填 / sticky） | `agent/clarify.py` 的 `SECTIONS` + `FIELDS` 锁死 | 任何产品都不变，模板和模型都改不动 |
| **内容**（每个字段的选项、默认值、输入提示） | 先按需求关键词从选项池里挑，模型可用时再由模型重写 | 每个产品都不一样 |

分区（跨产品固定）：

```
① 定位与场景   product_name / category / scenario / core_functions
② 形态与环境   form_factor / size_limit / environment
③ 硬件与供电   power / mcu / modules
④ 交互与外观   ui / style / material
⑤ 成本与交付   cost / volume / delivery
```

## 二、选项怎么随需求变

每个字段在 `POOLS` 里有一池候选（10-15 个），每个候选带一组关键词。出题时按需求文本
给候选打分，命中越多排越前，取前 6-7 个上桌，末尾恒挂一个「其他（请补充）」

（选中即展开自由输入框）。命中项若是单选，直接当默认值。

实测两个需求（都是纯规则，没调模型）：

| 字段 | 「智能花盆…太阳能…木质」 | 「桌面温湿度小夜灯…ESP32」 |
|---|---|---|
| 产品类目 | 园艺/种植设备 | 桌面设备 |
| 使用场景 | 阳台/庭院/花园 排第一 | 家居桌面/床头 排第一 |
| 供电方式 | 太阳能/能量采集 | USB 5V 直供 |
| 主控 | 由智能体决定 | **ESP32**（需求里写了就直接命中） |
| 传感模块 | 土壤湿度 排第一 | 温湿度传感器 排第一 |
| 外观风格 | 自然有机 | 由智能体决定 |
| 外壳材质 | 木质/竹材 | 由智能体决定 |

多选只排序、不预填 —— 命中项排最前，但不替用户勾选，避免必填项被"自动回答"。

## 三、改选项：编辑 `clarify-template.json`

文件在项目根目录，**改完保存即生效，不用重启服务**。它是"定制层"，只能改已有字段的
文案和选项，改不动框架（写了陌生 key 会被忽略）。

```json
{
  "assumptions": ["用户没填时，智能体采用的默认假设"],
  "overrides": {
    "cost": { "options": ["≤50 元", "≤80 元", "≤120 元", "不限", "自定义金额"] },
    "mcu":  { "label": "控制器" }
  }
}
```

| 字段 | 说明 |
|---|---|
| `assumptions` | 未填项的默认假设，写进需求简报 |
| `overrides.<key>.label` | 覆盖题面（≤20 字） |
| `overrides.<key>.options` | 写死选项。**写了就不再按需求自动匹配**，所有产品共用这一组；想保留随需求变化就别填 |
| `overrides.<key>.placeholder` | 输入提示 |
| 其它 key | 一律忽略 —— 框架锁死，模板加不了题也删不掉题 |

改坏了不用慌：JSON 语法错、根节点不对、内容全空 → 只丢定制，问卷按内置骨架照常出，
stderr 里留一行 `[clarify] 模板不可用，按内置骨架出题`。

旧格式（平铺 `questions`）仍然兼容：会按 key 把 `label` / `options` 捞出来当覆盖层。

## 四、`sticky`：哪些项自动带出上次的值

| sticky | 含义 | 字段 |
|---|---|---|
| `true` | 稳定偏好，跨项目复用上次填写，改一次记住新的 | 产品类目、供电方式、主控与平台、外观风格、外壳材质、成本上限、预计批量、交付深度 |
| `false` | 每次重新填 | 产品名称、使用场景、核心功能、形态尺寸、最大外形尺寸、使用环境、传感模块、显示交互 |

界面上 sticky 项带灰色「记忆」徽章；带入了值会在顶部显示「已带入 N 项上次填写的稳定偏好」，
旁边有「清空记忆」按钮。存在浏览器 localStorage 的 `pd.clarify.sticky`（只存本机）。

## 五、想让模型来写选项

`.env` 里加一行，重启 worker：

```
AGENT_CLARIFY_MODEL=1
```

模型**只能输出 options / default / placeholder**，prompt 里明确禁止改 key、label、题型和顺序；
返回的陌生 key 直接丢弃。代价是每次多等一轮 LLM（网关排队时 40~80 秒），界面顶部显示
「框架固定，选项由模型按本次需求生成」。不设这个开关就是纯规则定制，点开即出。

## 六、改动文件

| 文件 | 改动 |
|---|---|
| `agent/clarify.py` | 重写：`SECTIONS` / `FIELDS` 骨架、`POOLS` 选项池、`_pick()` 打分挑选、`_name_guess()` 名称摘要；`clarify()` 默认纯规则，模型只覆盖选项 |
| `clarify-template.json` | 改为 `assumptions` + `overrides` 定制层（兼容旧平铺格式） |
| `agent/server.py` | `/api/clarify` 透传 `sections` / `framework` |
| `web/components/ClarifyForm.tsx` | 抽出 `Field` 子组件；新增 `Section` 类型，按分区分组渲染（带标题、hint、已填计数） |
| `web/app/page.tsx` | `ClarifyPlan` 加 `sections` / `framework`，传给表单；文案改为「框架固定，选项按需求匹配」 |
| `scripts/test_clarify_skeleton.py` | 新增，172 项断言：跨产品框架指纹一致、选项随需求变、模板/模型都改不动框架 |

验证：后端接口返回 `framework=skeleton-v2`、`source=skeleton`，5 分区 16 题；
`scripts/test_clarify_skeleton.py` 全过；前端 `tsc --noEmit` 零错误。
