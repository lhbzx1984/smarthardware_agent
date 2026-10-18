"""需求补全：固定骨架 + 按需求定制选项。

设计要点（这套问卷的核心约束）：

- **框架跨产品一致**：5 个分区、16 个字段，key / 类型 / 顺序 / 必填 / sticky 全部锁死在
  SKELETON + FIELDS 里。做第十个产品时看到的还是同一张表，用户能形成肌肉记忆，
  sticky「记忆」也能跨项目复用，下游简报结构同样稳定。
- **内容随需求变**：每个字段背后有一池候选选项，按需求文本里的关键词打分，点亮最相关的
  那几个排到前面。换一个产品，浮上来的是另一批选项；选项池覆盖不到的，末尾恒有
  「其他（请补充）」兜底，选中即展开自由输入。
- **模型只填选项，不改框架**：AGENT_CLARIFY_MODEL=1 时模型只能输出 options / default /
  placeholder，key、label、type、分区一律以骨架为准，模型改不动。
- **模板只覆盖文案和池**：clarify-template.json 的 overrides 只能改已有字段的 label /
  options / placeholder，未知 key 直接丢弃；文件写坏了也只丢定制，框架照常出。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import config

# 骨架版本：前端/后端据此判断问卷结构是否变化（改分区或字段时 +1）
FRAMEWORK_ID = "skeleton-v2"

# ---------------------------------------------------------------- 骨架：分区
# 顺序即渲染顺序。所有产品共用这一套分区，不许增删。
SECTIONS: list[dict[str, Any]] = [
    {
        "id": "positioning",
        "title": "① 定位与场景",
        "hint": "谁用、在哪用、解决什么问题",
        "keys": ["product_name", "category", "scenario", "core_functions"],
    },
    {
        "id": "form",
        "title": "② 形态与环境",
        "hint": "长什么样、多大、待在什么环境",
        "keys": ["form_factor", "size_limit", "environment"],
    },
    {
        "id": "hardware",
        "title": "③ 硬件与供电",
        "hint": "电从哪来、谁来做主控、要挂什么器件",
        "keys": ["power", "mcu", "modules"],
    },
    {
        "id": "interface",
        "title": "④ 交互与外观",
        "hint": "怎么操作、什么风格、什么材质",
        "keys": ["ui", "style", "material"],
    },
    {
        "id": "business",
        "title": "⑤ 成本与交付",
        "hint": "预算、批量、做到哪一步",
        "keys": ["cost", "volume", "delivery"],
    },
]

# ---------------------------------------------------------------- 骨架：字段
# type / required / sticky / why / label 都是骨架的一部分，定制层不许改。
FIELDS: dict[str, dict[str, Any]] = {
    "product_name": {
        "label": "产品名称",
        "type": "text",
        "required": True,
        "sticky": False,
        "why": "所有文档与产物的命名基准",
        "placeholder": "例如：桌面式温湿度监测小夜灯",
    },
    "category": {
        "label": "产品类目",
        "type": "select",
        "required": False,
        "sticky": True,
        "why": "决定形态基调、防护等级与堆叠思路",
        "default": "桌面设备",
    },
    "scenario": {
        "label": "使用场景",
        "type": "multiselect",
        "required": True,
        "sticky": False,
        "why": "决定形态、防护等级与交互复杂度",
        "placeholder": "可补充：谁用、解决什么、特殊环境",
    },
    "core_functions": {
        "label": "核心功能与指标",
        "type": "multiselect",
        "required": True,
        "sticky": False,
        "why": "功能清单直接决定硬件选型与固件结构",
        "placeholder": "可补充：具体功能与精度/续航等硬指标",
    },
    "form_factor": {
        "label": "形态与尺寸",
        "type": "select",
        "required": False,
        "sticky": False,
        "why": "结构分件与堆叠方案的硬边界",
        "default": "桌面方形盒",
    },
    "size_limit": {
        "label": "最大外形尺寸",
        "type": "text",
        "required": False,
        "sticky": False,
        "why": "结构堆叠与内部空间的硬约束",
        "placeholder": "如 ≤120×80×40mm，不填由智能体按结构定",
    },
    "environment": {
        "label": "使用环境",
        "type": "select",
        "required": False,
        "sticky": False,
        "why": "决定防护等级、选材与密封结构",
        "default": "室内常温",
    },
    "power": {
        "label": "供电方式",
        "type": "select",
        "required": True,
        "sticky": True,
        "why": "影响电源树、BOM 成本与安全设计",
        "default": "USB 5V 直供",
    },
    "mcu": {
        "label": "主控与平台",
        "type": "select",
        "required": False,
        "sticky": True,
        "why": "决定引脚分配、固件框架与仿真可行性",
        "default": "由智能体决定",
    },
    "modules": {
        "label": "传感与外设模块",
        "type": "multiselect",
        "required": False,
        "sticky": False,
        "why": "器件清单决定原理图、BOM 与外壳开孔",
        "placeholder": "可补充：具体型号或特殊器件",
    },
    "ui": {
        "label": "显示与交互",
        "type": "multiselect",
        "required": False,
        "sticky": False,
        "why": "交互件是结构与硬件的主要分叉点，不选即「无」",
        "placeholder": "可补充：触摸滑条、语音识别等",
    },
    "style": {
        "label": "外观风格",
        "type": "select",
        "required": False,
        "sticky": True,
        "why": "决定 CMF 方案与渲染提示词",
        "default": "由智能体决定",
    },
    "material": {
        "label": "外壳材质与工艺",
        "type": "select",
        "required": False,
        "sticky": True,
        "why": "决定壁厚、拔模、装配方式与成本",
        "default": "由智能体决定",
    },
    "cost": {
        "label": "整机成本上限",
        "type": "select",
        "required": False,
        "sticky": True,
        "why": "BOM 选型与工艺路线的硬约束",
        "default": "≤80 元",
    },
    "volume": {
        "label": "预计批量",
        "type": "select",
        "required": False,
        "sticky": True,
        "why": "决定工艺路线（打印/钣金/注塑）与选型渠道",
        "default": "小批量 10-100 台",
    },
    "delivery": {
        "label": "交付深度",
        "type": "select",
        "required": False,
        "sticky": True,
        "why": "决定结构公差、DFM 与仿真细度",
        "default": "全套含仿真验证",
    },
}

# 每个字段挑几个选项上桌（不含末尾的「其他」兜底项）
_OPT_LIMIT = {"select": 6, "multiselect": 7}

# ---------------------------------------------------------------- 选项池
# (选项文案, 命中关键词元组)。关键词命中越多，该选项在本次需求里排得越靠前。
# 池内顺序 = 无命中时的默认出场顺序，写池时把最常见放前面。
POOLS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "category": [
        ("桌面设备", ("桌面", "桌上", "桌面式", "放置")),
        ("手持便携", ("手持", "便携", "掌上", "握持", "随身")),
        ("可穿戴", ("穿戴", "手环", "手表", "贴身", "佩戴")),
        ("传感节点", ("传感", "监测", "采集", "节点", "探头")),
        ("家电配件", ("家电", "配件", "改装", "配套")),
        ("桌面摆件", ("摆件", "装饰", "摆设", "桌面装饰")),
        ("教育套件", ("教育", "教学", "套件", "学生", "课程", "教具")),
        ("园艺/种植设备", ("花盆", "园艺", "种植", "绿植", "浇水", "盆栽")),
        ("工业模块", ("工业", "车间", "机柜", "产线", "工厂")),
        ("车载设备", ("车载", "车内", "车充", "点烟器")),
        ("户外装备", ("户外", "露营", "野餐", "钓鱼", "登山")),
        ("宠物/婴童用品", ("宠物", "猫", "狗", "婴儿", "宝宝", "儿童")),
        ("其他（请补充）", ()),
    ],
    "scenario": [
        ("家居桌面/床头", ("家居", "床头", "卧室", "客厅", "家里", "家用")),
        ("办公桌面", ("办公", "工位", "办公室", "书房", "电脑旁")),
        ("厨房/浴室", ("厨房", "浴室", "卫生间", "洗手间", "油烟")),
        ("阳台/庭院/花园", ("阳台", "庭院", "花园", "花盆", "绿植", "种植", "阳台上")),
        ("随身携带/通勤", ("随身", "通勤", "口袋", "外出", "包里")),
        ("车载/出行", ("车载", "车内", "自驾", "车上")),
        ("户外/运动", ("户外", "露营", "骑行", "跑步", "运动", "野外")),
        ("工业/车间/机房", ("工业", "车间", "机房", "工厂", "产线")),
        ("实验室/教学", ("实验室", "教学", "课堂", "学生", "实训")),
        ("店铺/商用", ("店铺", "门店", "商用", "零售", "收银")),
        ("医疗/护理", ("医疗", "护理", "病房", "康复", "护士")),
        ("其他（请补充）", ()),
    ],
    "core_functions": [
        ("环境参数采集", ("温湿度", "温度", "湿度", "采集", "监测", "检测", "传感")),
        ("数值/状态显示", ("显示", "屏幕", "数值", "读数", "读数显示")),
        ("灯光照明/氛围", ("灯", "照明", "氛围", "夜灯", "发光", "氛围灯")),
        ("声音提醒/报警", ("报警", "蜂鸣", "提醒", "声音", "铃声", "提示音")),
        ("无线连接/联网", ("Wi-Fi", "wifi", "蓝牙", "联网", "无线", "App", "app", "手机", "远程", "上报")),
        ("定时/自动控制", ("定时", "自动", "控制", "联动", "自动浇水", "自动喂食", "自动开合")),
        ("电机/执行机构", ("电机", "舵机", "水泵", "风扇", "继电器", "转动", "升降")),
        ("计数/计时", ("计数", "计时", "倒计时", "秒表", "考勤")),
        ("定位/追踪", ("定位", "GPS", "gps", "追踪", "防丢", "北斗")),
        ("身份/权限识别", ("识别", "指纹", "刷卡", "NFC", "nfc", "门禁", "人脸")),
        ("数据记录/存储", ("记录", "存储", "日志", "历史", "上传", "导出")),
        ("语音交互", ("语音", "播报", "说话", "麦克风", "语音助手")),
        ("低功耗长续航", ("续航", "低功耗", "待机", "省电", "电池供电")),
        ("其他（请补充）", ()),
    ],
    "form_factor": [
        ("桌面方形盒", ("桌面", "盒子", "方形", "方盒", " rectangular")),
        ("圆饼/圆柱", ("圆", "圆柱", "圆饼", "筒", "圆形")),
        ("手持长条/棒状", ("手持", "棒", "长条", "笔状", "条状")),
        ("卡片/薄片", ("卡片", "薄", "薄片", "贴片")),
        ("可穿戴环/带", ("穿戴", "手环", "环", "腕带", "表带")),
        ("挂壁/贴附", ("挂", "壁挂", "贴", "吸附", "磁吸", "粘贴")),
        ("支架/夹持式", ("夹", "支架", "夹持", "固定夹", "抱箍")),
        ("模块化拼接", ("模块", "拼接", "积木", "可扩展")),
        ("仿生/造型化", ("仿生", "造型", "卡通", "动物", "玩偶")),
        ("其他（请补充）", ()),
    ],
    "environment": [
        ("室内常温", ("室内", "常温", "居家", "室内常温")),
        ("室内潮湿/油烟", ("潮湿", "厨房", "浴室", "油烟", "防水", "水汽")),
        ("半户外/阳台", ("半户外", "阳台", "窗台", "屋檐")),
        ("户外日晒雨淋", ("户外", "防雨", "防尘", "日晒", "淋雨", "露天")),
        ("高低温极端", ("高温", "低温", "冷库", "极端", "严寒", "酷暑")),
        ("震动/移动", ("震动", "振动", "移动", "颠簸", "车载")),
        ("粉尘/多尘", ("粉尘", "多尘", "车间", "扬尘")),
        ("接触人体/皮肤", ("皮肤", "贴身", "佩戴", "接触人体", "亲肤")),
        ("涉水/浸泡", ("涉水", "浸泡", "水下", "潜水", "浸水")),
        ("其他（请补充）", ()),
    ],
    "power": [
        ("USB 5V 直供", ("USB", "usb", "5V", "Type-C", "type-c", "插电", "直供", "充电线")),
        ("锂电池 + 充电管理", ("锂电", "充电", "电池", "续航", "18650", "聚合物")),
        ("干电池/纽扣电池", ("干电池", "纽扣", "AA", "AAA", "5号", "7号", "碱性")),
        ("适配器/市电", ("适配器", "市电", "220V", "电源适配器", "插墙")),
        ("太阳能/能量采集", ("太阳能", "光伏", "能量采集", "光能")),
        ("无线充电", ("无线充电", "Qi", "qi", "磁吸充电")),
        ("PoE/网线供电", ("PoE", "poe", "网线供电")),
        ("未定（由智能体决定）", ()),
        ("其他（请补充）", ()),
    ],
    "mcu": [
        ("ESP32", ("ESP32", "esp32", "Wi-Fi", "wifi", "蓝牙", "联网", "乐鑫")),
        ("Arduino UNO (ATmega328P)", ("Arduino", "arduino", "UNO", "uno", "328", "328P")),
        ("Arduino Nano", ("Nano", "nano")),
        ("Raspberry Pi Pico", ("Pico", "pico", "RP2040", "rp2040")),
        ("STM32 系列", ("STM32", "stm32", "STM", "意法")),
        ("树莓派/单板机", ("树莓派", "Raspberry", "raspberry", "Linux", "单板", "派")),
        ("51/AVR 单片机", ("51", "AVR", "avr", "STC", "stc")),
        ("成品模块（免开发）", ("成品模块", "免开发", "现成模块", "模块直接")),
        ("由智能体决定", ()),
        ("其他（注明型号）", ()),
    ],
    "modules": [
        ("温湿度传感器", ("温湿度", "温度", "湿度", "DHT", "dht", "SHT", "sht")),
        ("光照/环境光", ("光照", "光敏", "环境光", "BH1750", "亮度")),
        ("土壤湿度", ("土壤", "花盆", "绿植", "浇水", "种植")),
        ("水位/液位", ("水位", "液位", "水浸", "水量")),
        ("气体/空气质量", ("气体", "空气", "CO2", "甲醛", "PM2.5", "VOC", "空气质量")),
        ("人体/红外感应", ("人体", "红外", "PIR", "存在检测", "感应")),
        ("加速度/姿态", ("加速度", "姿态", "陀螺仪", "MPU", "倾角", "计步")),
        ("称重/压力", ("称重", "压力", "重量", "称重传感器")),
        ("摄像头/视觉", ("摄像头", "视觉", "拍照", "OV2640", "图像")),
        ("RFID/NFC", ("RFID", "rfid", "NFC", "nfc", "刷卡", "标签")),
        ("GPS/定位", ("GPS", "gps", "定位", "北斗")),
        ("继电器/电机驱动", ("继电器", "电机", "水泵", "风扇", "驱动", "舵机")),
        ("RTC/时钟", ("RTC", "rtc", "时钟", "实时时钟", "走时")),
        ("超声/测距", ("超声", "测距", "避障", "距离")),
        ("其他（请补充）", ()),
    ],
    "ui": [
        ("OLED/LCD 显示屏", ("OLED", "oled", "LCD", "lcd", "显示屏", "屏幕", "显示")),
        ("LED 指示灯/灯带", ("LED", "led", "指示灯", "灯带", "灯珠", "发光", "氛围灯")),
        ("轻触/机械按键", ("按键", "按钮", "轻触", "开关", "旋钮")) ,
        ("旋钮/编码器", ("旋钮", "编码器", "旋转", "电位器")),
        ("触摸/滑条", ("触摸", "触控", "滑条", "电容触摸")),
        ("蜂鸣器/喇叭", ("蜂鸣器", "喇叭", "声音", "报警", "扬声器")),
        ("手机 App/蓝牙配网", ("App", "app", "蓝牙", "手机", "小程序", "配网")),
        ("语音播报/识别", ("语音", "播报", "说话", "麦克风")),
        ("遥控/红外接收", ("遥控", "红外", "遥控器")),
        ("无交互（全自动运行）", ("无交互", "全自动", "免操作")),
        ("其他（请补充）", ()),
    ],
    "style": [
        ("极简白盒", ("极简", "白盒", "简洁", "纯白", "简约")),
        ("圆润亲和", ("圆润", "亲和", "柔和", "可爱", "萌")),
        ("工业硬朗", ("工业", "硬朗", "机械", "方正", "硬核")),
        ("透明探索感", ("透明", "探索", "亚克力", "通透", "透视")),
        ("复古怀旧", ("复古", "怀旧", "黄铜", "老式", "蒸汽朋克")),
        ("自然有机", ("自然", "有机", "木", "木质", "原木", "植物", "森系")),
        ("科技未来感", ("科技", "未来", "赛博", "光效", "机甲")),
        ("由智能体决定", ()),
        ("其他（请补充）", ()),
    ],
    "material": [
        ("3D 打印（PLA/ABS）", ("3D打印", "打印", "PLA", "pla", "ABS", "abs", "增材", "打样")),
        ("注塑 ABS/PC", ("注塑", "量产", "PC", "模具", "开模")),
        ("铝合金/CNC", ("铝", "金属", "CNC", "cnc", "铝合金")),
        ("亚克力/透明板", ("亚克力", "有机玻璃", "透明板")),
        ("木质/竹材", ("木", "竹", "木质", "榫卯")),
        ("硅胶/软胶", ("硅胶", "软胶", "柔性", "亲肤")),
        ("不锈钢", ("不锈钢", "304", "钣金")),
        ("由智能体决定", ()),
        ("其他（请补充）", ()),
    ],
    "cost": [
        ("≤30 元", ("≤30", "30元", "极低成本", "超低成本")),
        ("≤50 元", ("≤50", "50元")),
        ("≤80 元", ("≤80", "80元")),
        ("≤150 元", ("≤150", "150元")),
        ("≤300 元", ("≤300", "300元")),
        ("不限成本", ("不限", "不差钱", "不在乎成本")),
        ("自定义金额", ()),
    ],
    "volume": [
        ("样品 1-5 台", ("样机", "样品", "打样", "1台", "一台", "单台")),
        ("小批量 10-100 台", ("小批量", "10台", "50台", "100台", "几十台")),
        ("中批量 100-1000 台", ("中批量", "500台", "1000台", "几百台")),
        ("量产 1000 台以上", ("量产", "上万", "大批量", "规模化")),
        ("未定", ()),
        ("其他（请补充）", ()),
    ],
    "delivery": [
        ("概念方案（外观 + 结构草图）", ("概念", "草图", "方案阶段", "看看效果")),
        ("可打样（3D 打印 + 手焊验证）", ("打样", "手焊", "验证样机", "样品")),
        ("可量产（注塑 DFM + 器件选型）", ("量产", "DFM", "dfm", "开模", "可制造")),
        ("全套含仿真验证", ("仿真", "全套", "验证", "电路仿真", "完整")),
    ],
}

# 内置兜底的默认假设；模板文件里的 assumptions 优先
_ASSUMPTIONS = [
    "供电默认 USB 5V 直供",
    "主控默认 Arduino UNO，便于 Cirkit Designer 仿真",
    "结构默认 3D 打印小批量，壁厚 2.0 mm",
]

# 可编辑模板：只覆盖文案与选项池，改不动骨架。改完保存即生效，不用重启服务。
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "clarify-template.json"

_ALLOWED_TYPES = ("text", "textarea", "select", "multiselect", "number")


def _is_other(v: str) -> bool:
    """兜底选项：选中后前端会展开自由输入框。"""
    return v.startswith("其他") or v.startswith("自定义")


# ---------------------------------------------------------------- 定制：按需求挑选项
def _pick(key: str, req: str) -> tuple[list[str], str]:
    """从一个字段的选项池里，按需求挑出本次该上桌的选项。

    返回 (选项列表, 命中的最佳项)。命中 = 需求文本里出现了该选项的关键词；
    命中数越多排越前。一个都没命中时，按池内默认顺序出场（通用选项）。
    """
    pool = POOLS.get(key) or []
    others = [v for v, _ in pool if _is_other(v)]
    low = req.lower()
    scored: list[tuple[int, int, str]] = []
    for i, (v, tags) in enumerate(pool):
        if _is_other(v):
            continue
        hit = 0
        for t in tags:
            if not t:
                continue
            if t.lower() in low or t in req:
                hit += 1
        scored.append((-hit, i, v))
    scored.sort()
    limit = _OPT_LIMIT.get(FIELDS.get(key, {}).get("type", "select"), 6)
    picked = [v for _, _, v in scored[:limit]]
    # 兜底项恒在末尾：选项池覆盖不到时用户还能自己写
    picked += [o for o in others if o not in picked][:1]
    best = scored[0][2] if scored and scored[0][0] < 0 else ""
    return picked, best


_NAME_LEAD = re.compile(r"^(帮我|请|我想|我要|我想要|想要|做一个|做个|做一个?|设计一款|设计一个|设计|开发一款|开发|来一个|搞一个|做一台)")


def _name_guess(text: str) -> str:
    """从需求首句猜一个产品名：'做一个智能花盆，…' → '智能花盆'。"""
    head = text.split("\n")[0].strip()
    head = re.split(r"[，,。；;！!？?、]", head)[0].strip()
    head = _NAME_LEAD.sub("", head).strip(" 的")
    return (head or text)[:24]


def _load_overrides() -> tuple[dict[str, dict[str, Any]], list[str], str]:
    """读模板的覆盖层。模板只能改已有字段的 label / options / placeholder 和 assumptions。

    兼容旧格式（平铺 questions）：从里面抽同名 key 的 label/options 当覆盖层。
    文件缺失或写坏 → 返回空覆盖，问卷照常按内置骨架出。
    """
    try:
        data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("模板根节点不是对象")
        raw = data.get("overrides")
        if not isinstance(raw, dict):
            # 旧格式：把 questions 里同名 key 的文案捞出来当覆盖
            raw = {}
            for q in data.get("questions") or []:
                if isinstance(q, dict) and q.get("key") in FIELDS:
                    raw[str(q["key"])] = {
                        k: q.get(k) for k in ("label", "options", "placeholder") if q.get(k)
                    }
        ov: dict[str, dict[str, Any]] = {}
        for k, v in raw.items():
            if k in FIELDS and isinstance(v, dict):
                ov[k] = {
                    "label": str(v.get("label") or "")[:20],
                    "options": [str(o) for o in (v.get("options") or [])][:10],
                    "placeholder": str(v.get("placeholder") or "")[:80],
                }
        assumes = [str(a) for a in (data.get("assumptions") or [])][:8]
        return ov, (assumes or list(_ASSUMPTIONS)), "template"
    except Exception:  # noqa: BLE001  模板写坏只丢定制，不能挡住主流程
        print(f"[clarify] 模板不可用，按内置骨架出题：{TEMPLATE_PATH}", file=sys.stderr)
        return {}, list(_ASSUMPTIONS), "builtin"


def skeleton_plan(requirement: str, model_fields: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """按固定骨架出题，选项由需求（或模型）定制。

    model_fields 为模型给的定制层，只允许带 options / default / placeholder。
    """
    text = (requirement or "").strip()
    req = text[:4000]
    overrides, assumes, source = _load_overrides()

    questions: list[dict[str, Any]] = []
    for section in SECTIONS:
        for key in section["keys"]:
            spec = FIELDS.get(key)
            if not spec:
                continue
            qtype = spec["type"]
            ov = overrides.get(key) or {}
            mf = (model_fields or {}).get(key) or {}
            options: list[str] = []
            default = ""
            if qtype in ("select", "multiselect"):
                picked, best = _pick(key, req)
                # 优先级：模型给的 > 模板覆盖的 > 按需求挑的
                explicit = [str(o) for o in (mf.get("options") or ov.get("options") or []) if str(o)]
                options = (explicit or picked)[:10]
                if not any(_is_other(o) for o in options):
                    options = options + ["其他（请补充）"]
                if qtype == "multiselect":
                    # 多选不预填：命中项已经排在最前，但不替用户「勾选」一个答案
                    default = str(mf.get("default") or ov.get("default") or "")
                else:
                    default = str(mf.get("default") or ov.get("default") or best or spec.get("default") or "")
                if default and default not in options and not _is_other(default):
                    if explicit:
                        # 选项表是模板/模型显式指定的：不往里塞内置默认值，落到首项
                        default = options[0]
                    else:
                        options = [default] + [o for o in options if o != default]
            else:
                if key == "product_name" and text:
                    # 需求首句就是最好的名称初值：去掉「做一个」这类引导词和第一个逗号后的内容
                    default = _name_guess(text)
                else:
                    default = str(mf.get("default") or ov.get("default") or spec.get("default") or "")
            questions.append(
                {
                    "key": key,
                    "label": str(ov.get("label") or spec["label"])[:20],
                    "type": qtype,
                    "options": options,
                    "placeholder": str(mf.get("placeholder") or ov.get("placeholder") or spec.get("placeholder") or "")[:80],
                    "default": default,
                    "required": bool(spec["required"]),
                    "sticky": bool(spec["sticky"]),
                    "why": spec["why"],
                    "section": section["id"],
                }
            )

    return {
        "product_name": text[:24] or "未命名产品",
        # 骨架模式不调模型，充分度只能按需求文本的信息量粗估
        "readiness": max(10, min(45, 10 + len(text) // 4)),
        "missing": [q["label"] for q in questions if q["required"]],
        "framework": FRAMEWORK_ID,
        "sections": [
            {"id": s["id"], "title": s["title"], "hint": s["hint"], "keys": list(s["keys"])}
            for s in SECTIONS
        ],
        "questions": questions,
        "assumptions": assumes,
        "source": "skeleton+model" if model_fields else "skeleton",
    }


# ---------------------------------------------------------------- 可选：模型填选项
CLARIFY_SYSTEM = """你是资深硬件产品定义专家。问卷框架已由系统固定（分区、字段 key、题型、顺序都不能动），
你的唯一任务是：针对本次需求，给每个字段挑出最贴切的候选选项，让用户能点选而不是打字。

输出要求（严格执行）：
- 只输出一个 JSON 对象，不要 Markdown 代码块、不要任何解释文字。
- 结构：{"fields": {"<key>": {"options": ["选项1", ...], "default": "建议默认，可空", "placeholder": "输入提示，可空"}}}
- 只输出下面列出的 key，不要新增、不要删减、不要改 key 名字。
- select 给 4-6 个选项，multiselect 给 5-7 个选项；选项要具体到能直接指导设计（写清器件/材质/工艺）。
- 每个字段的最后一个选项必须是「其他（请补充）」，保证用户覆盖不到时能自己写。
- 选项文案用中文，不要编号，不要重复。
- 字段含义如下（key | 题面 | 题型）：
{field_list}
"""

CLARIFY_USER = """产品需求原文：
{req}

请按上面的规则输出 options JSON。"""


def _model_customize(requirement: str, settings: dict[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    """让模型按需求填选项。失败返回 None —— 定制层可有可无，骨架永远在。"""
    from main_agent import get_model  # 延迟导入：避免启动时加载大模型依赖

    try:
        llm = get_model(settings)
        if not llm:
            return None
        field_list = "\n".join(
            f"{k} | {FIELDS[k]['label']} | {FIELDS[k]['type']}" for k in FIELDS
        )
        resp = llm.invoke(
            [
                {"role": "system", "content": CLARIFY_SYSTEM.format(field_list=field_list)},
                {"role": "user", "content": CLARIFY_USER.format(req=(requirement or "")[:4000])},
            ]
        )
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        t = re.sub(r"^```(?:json)?|```$", "", (content or "").strip(), flags=re.M).strip()
        m = re.search(r"\{[\s\S]*\}", t)
        if not m:
            return None
        data = json.loads(m.group(0))
        raw = (data or {}).get("fields")
        if not isinstance(raw, dict):
            return None
        out: dict[str, dict[str, Any]] = {}
        for k, v in raw.items():
            if k not in FIELDS or not isinstance(v, dict):
                continue  # 模型改不动框架：陌生 key 直接丢
            opts = [str(o) for o in (v.get("options") or [])][:10]
            if not opts:
                continue
            out[k] = {
                "options": opts,
                "default": str(v.get("default") or "")[:60],
                "placeholder": str(v.get("placeholder") or "")[:80],
            }
        return out or None
    except Exception:  # noqa: BLE001  定制层失败不能挡住主流程
        return None


def clarify(requirement: str, settings: dict[str, Any] | None) -> dict[str, Any]:
    """生成需求补全清单：固定骨架打底，选项按需求定制。

    默认走纯规则定制（点开即出，不占一轮 LLM 往返 —— 网关排队时一轮 40~80 秒，
    为一份问卷等这么久不划算）。设 AGENT_CLARIFY_MODEL=1 时额外让模型按本次需求
    重写选项，但框架仍然锁死，模型改不动分区、字段和题型。
    """
    mf = None
    if config.CLARIFY_BY_MODEL:
        mf = _model_customize(requirement, settings)
    return skeleton_plan(requirement, mf)


def build_brief(requirement: str, answers: dict[str, str], plan: dict[str, Any] | None = None) -> str:
    """把用户填的答案组装成结构化需求简报，作为智能体的输入。"""
    lines = [f"# 产品需求简报：{plan.get('product_name') if plan else '未命名产品'}", ""]
    lines.append("## 原始需求")
    lines.append(requirement.strip())
    lines.append("")
    labels = {}
    order = []
    for q in (plan or {}).get("questions", []):
        labels[q.get("key")] = q.get("label")
        order.append(q.get("key"))
    filled = {k: v for k, v in (answers or {}).items() if str(v).strip()}
    if filled:
        lines.append("## 已确认信息")
        # 按问卷顺序输出，智能体看到的顺序和用户在 UI 上填的顺序一致
        for k in order:
            if k in filled:
                lines.append(f"- **{labels.get(k) or k}**：{filled[k]}")
        for k, v in filled.items():
            if k not in order:
                lines.append(f"- **{labels.get(k) or k}**：{v}")
        lines.append("")
    if plan and plan.get("assumptions"):
        lines.append("## 未填写项的默认假设")
        for a in plan["assumptions"]:
            lines.append(f"- {a}")
        lines.append("")
    lines.append("## 交付要求")
    lines.append("按以上确认信息完成产品定义、外观、结构、硬件与固件仿真，全部产物落盘并在最后汇总。")
    return "\n".join(lines)
