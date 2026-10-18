---
name: text-to-cad-structure
description: 依据外观阶段的出图提示词与渲染图，用 build123d 建出真实 3D 结构（STEP/STL + 四视图 + 爆炸图 + 一致性对照表），保证结构与外观是同一台产品。当任务涉及「结构设计」「建模」「导出 STEP/STL」「爆炸图」「DFM」时使用。
agent_created: true
---

# text-to-cad 结构设计（外观 → 结构同源建模）

## 第零步：先取回外观的形状约束（不做这一步，后面全白做）

```
text_to_cad()                       # 不传 script
```

它会把 `10_appearance/prompts.json` 里存档的出图提示词与 `appearance.md` 摘要交回来。
**外形尺寸、比例、分模位置一律以它为准**，不要自己另编一套尺寸 ——
「外观图与结构图对不上」的根因就是这一步被跳过。

注意：它提取的是**提示词里的文字约束**（尺寸、形态、材质），不是从 PNG 像素反推几何。
所以外观提示词必须写清可量化的外形信息；上游没写清，就按 PRD 的硬约束取值并在
`structure.md` 里注明「外观提示词未给出尺寸，按 PRD 约束取 XX」。

## 第一步：带脚本建模

```
text_to_cad(script=<build123d 脚本>, base_name="model")
```

导出 STEP + STL，自动补四视图、爆炸图与 `20_structure/consistency.md`
（外观提示词 vs 实际包围盒对照表）。

### 脚本契约

```python
from build123d import *
W, D, H = 100.0, 80.0, 40.0            # 单位 mm，XY 平面 + Z 向上
WALL, R, BASE_H = 2.0, 8.0, 30.0       # 关键尺寸写成具名常量

with BuildPart() as shell:
    Box(W, D, BASE_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(shell.edges().filter_by(Axis.Z), radius=R)
    with Locations((0, 0, WALL)):      # 掏内腔：显式减去内缩实体
        Box(W-2*WALL, D-2*WALL, BASE_H-WALL,
            align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    with Locations((0, 12, BASE_H-WALL/2)):      # 视窗
        Box(34, 18, WALL, mode=Mode.SUBTRACT)

with BuildPart() as lid:
    Box(W, D, H-BASE_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(lid.edges().filter_by(Axis.Z), radius=R)

parts = {"下壳": shell.part, "上盖": lid.part.moved(Location((0, 0, BASE_H)))}
```

规范：
- 单位毫米，XY 平面 + Z 向上，选一个功能性基准面
- 必须是**闭合的正体积实体**，不留曲面 / 开放壳
- 装配用 `.moved(Location((x, y, z)))` 定位，不用相对偏移堆砌
- 壁厚、拔模、倒角要有意识地表达 —— 它们就是后面要检查的 DFM 项

### 这几个写法一定报错

| 错误写法 | 正确写法 |
|---|---|
| `Box(...).fillet(radius=2)` | `fillet(builder.edges().filter_by(Axis.Z), radius=R)` |
| `part.edges()....fillet(radius=2)`（CadQuery 链式） | `fillet(part.edges()..., radius=2)`——0.13 的 ShapeList 没有 `.fillet()/.chamfer()` 方法；平台会自动改写，但自己写对省一轮 |
| `Workplanes(...)` | 0.13 已移除，用 `Locations` + `Box(..., mode=Mode.SUBTRACT)` |
| `Cylinder(r=5, h=3)` | `Cylinder(radius=5, height=3)` |
| `offset(amount=-WALL)` | 它只生成内缩实体、不掏空，用上面的内腔减法 |
| `bbox.x_length / .y_length / .z_length` | `bbox.size.X / .size.Y / .size.Z`（等价 `bbox.max.X-bbox.min.X`）|
| `edges().filter_by(Axis.Z, filter="top")` | `filter_by()` 没有 `filter=`，顶面边用 `.filter_by(Axis.Z).sort_by(Axis.Z)[-1:]`，底面 `[:1]` |
| `edge.tangent` | `Edge` 没有这个属性，切线用 `edge.tangent_at()` |
| `radians(...) / pi` 直接用 | build123d 不导出 math；平台会自动补 `from math import ...`，自己写一行也行 |
| 圆柱/圆锥上 `filter_by(Axis.Z)` 再 fillet | 回转体**没有平行 Z 的直边**，必报 `no suitable edges for fillet`。选上下沿圆边：`filter_by(GeomType.CIRCLE).sort_by(Axis.Z)[-1:]`（上沿）/ `[:1]`（下沿） |

BoundBox 在 0.13 只有：`min` / `max` / `size`（Vector）/ `diagonal` / `center()`（**方法，要括号**）。
其实不必自己算：工具本来就会回传每个零件的包围盒尺寸与体积，写尺寸表用那些数。

## 第二步：怎么读报错（这一步最容易做错）

返回的「建模失败：」后面**第一段**是真实报错（traceback / 静态检查命中行），
末尾标着「参考写法」的那段是示例，**不是你的报错** —— 别把它当错误内容照抄回去。

按真实报错给出的行号改那一行，最多 3 次；**绝不能原样重发同一段脚本**。

出现「平台已按脚本里解析出的尺寸参数兜底建模」：STEP/STL 已经产出了（参数化模板），
此时不要重试原脚本，直接读 `20_structure/model.py` 当起点，往上加开窗 / 开孔 / 卡扣，
并在 `structure.md` 里注明哪些特征来自兜底模板。

三次都没跑通：在文档里写明失败原因 + 给出参数化尺寸表兜底，
**禁止伪造尺寸或谎称成功**。

## 第三步：视图与爆炸图

- `text_to_cad` 已自动出 `20_structure/exploded.svg` 与 `10_appearance/views-sheet.svg`，
  不要再重复调 `render_product_views`。
- 若用 `build_structure` 单独建模，必须补一次 `render_product_views(kind="both")`
  （离线秒级、必定成功，读刚导出的 STL 用真实几何生成）。
- AI 风格爆炸图：`generate_product_image(kind="exploded")`，不传 reference，
  工具会自动以第一张外观图为参考。它是加分项，鉴权失败就立刻停。
- **AI 爆炸图上的尺寸数字一律不可信**（实测模型会自己编 87.0 / 17.3 这种数字）。
  尺寸表只写 CAD 回传的包围盒数值，图注写明「标注为示意，尺寸以本表为准」。

## 第四步：写文档（硬件能不能开工全看这里的数值）

`20_structure/structure.md` 必须包含：
- 外形与关键配合尺寸表（数值必须与 CAD 回传一致）
- 分件方案、装配序列、固定方式、材料工艺选型
- DFM 检查表
- **给硬件的 PCB 装配边界**：内腔净尺寸（长×宽×净高，已扣壁厚）、
  安装柱位置坐标与孔径 / 柱高、限高区与禁布区、接口与视窗开孔坐标与尺寸、
  建议板厚与板框余量。用表格给数值，不要写「详见图纸」—— 硬件读不到图纸。
- 外观一致性章节：逐条核对 `consistency.md` 的待核对项

## 收尾必须做

```
confirm_with_user(stage="20_structure", summary=…, files=…)
```

summary 写清：外形尺寸、分件数、壁厚、材料、CAD 是否成功、与外观提示词的偏差。
返回「需修改」就按反馈改脚本重跑，改完**再调一次**确认。

下游（硬件）的门禁会检查这份确认记录 —— 没确认，`stage_gate("30_hardware")` 不会放行。
