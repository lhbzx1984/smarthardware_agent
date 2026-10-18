"""生成主页视觉资产（全部走 Agnes 图像网关），落到 web/public/home/。

风格约束（企业级科技风）：深色底 + 冷色调 + 工程/数据感，写实产品渲染，
不出现卡通、二次元、吉祥物类元素。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import imagegen  # noqa: E402

OUT = ROOT / "web" / "public" / "home"
OUT.mkdir(parents=True, exist_ok=True)

# (文件名, 提示词)。提示词写「可量化」的外形与镜头，方便后续结构建模复用同一描述。
ASSETS = [
    (
        "hero.png",
        "深色科技感产品设计中枢主视觉：深空蓝黑背景，中央悬浮一台圆柱形智能桌面设备的写实产品渲染，"
        "亚光金属与半透扩散罩材质，边缘冷白轮廓光；周围环绕半透明的数据面板、参数标签与工程线框，"
        "细网格地面反射；电影级产品摄影，浅景深，16:9 横向构图，无文字，无人物",
    ),
    (
        "flow-concept.png",
        "工业设计概念阶段场景：深色工作台上的设计草图本、数位笔与一台正在显示三维线框模型的显示器，"
        "屏幕里有圆柱形设备轮廓；冷色调环境光，写实渲染，浅景深，无人物，无文字",
    ),
    (
        "flow-appearance.png",
        "产品外观渲染阶段：一台圆柱形智能桌面小夜灯的产品级渲染图，哑光米白外壳，顶部圆形显示窗发出柔和暖光，"
        "放在深色木质桌面，侧逆光勾勒轮廓，背景深空蓝，写实产品摄影，无文字",
    ),
    (
        "flow-structure.png",
        "产品结构工程视图：圆柱形智能设备的爆炸装配图，外壳、扩散罩、主控板、电池、底盖沿轴线分离，"
        "带装配引导线与编号标注；深蓝背景，工程蓝图风格线框与实体混合渲染，无文字",
    ),
    (
        "flow-electronics.png",
        "电子硬件设计：一块深色 PCB 电路板特写，走线、芯片、接插件清晰，局部有绿色阻焊与金色焊盘，"
        "旁边示波器屏幕显示波形；冷色调工业摄影，浅景深，无文字",
    ),
    (
        "verify.png",
        "验证与量产阶段：洁净的实验台上摆放着三台同款智能设备样机，旁边是测量仪器与测试夹具，"
        "背景有淡淡的产线灯光；写实工业摄影，冷色调，浅景深，无人物，无文字",
    ),
    (
        "business.png",
        "商业化交付概念图：深色背景中悬浮的数据看板界面，包含成本曲线、交付周期柱状图与增长折线，"
        "冷蓝与青色高亮，玻璃质感面板，写实 UI 渲染，无任何可读文字与乱码文字",
    ),
]


def main() -> int:
    base, model, prov = config.resolve_image_provider()
    print(f"provider={prov} base={base} model={model}")
    print(f"输出目录: {OUT}\n")
    done = 0
    for name, prompt in ASSETS:
        dest = OUT / name
        t0 = time.time()
        res = imagegen.generate(prompt=prompt, dest=dest, kind="render", base_url=base, model=model)
        dt = round(time.time() - t0, 1)
        if res.get("ok"):
            done += 1
            print(f"  ok   {name:<22} {round(res['bytes']/1024, 1):>8} KB  {dt}s")
        else:
            print(f"  FAIL {name:<22} {str(res.get('error'))[:120]}  {dt}s")
    print(f"\n完成 {done}/{len(ASSETS)}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
