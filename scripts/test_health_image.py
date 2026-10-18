"""回归：/health 里的出图配置回显。

踩过的坑：/health 原来只按 .env 推算图像模型，用户在前端把模型改成
agnes-image-2.5-flash 后，这里仍显示 .env 里的 2.1 —— 用户以为设置没生效，
跑去查「到底哪一步丢了」。现在以「最近一次会话的前端设置」为准，缺省才回落 .env。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


client = TestClient(server.app)
_orig = store.latest_settings


def health_image() -> dict:
    return client.get("/health").json()["image"]


print("== /health 出图配置 ==")

# 1) 前端设置过图像模型：以设置为准
store.latest_settings = lambda: {
    "image_model": "agnes-image-2.5-flash",
    "image_base_url": "https://apihub.agnes-ai.com/v1",
    "image_api_key": "***",
}
img = health_image()
check("回显前端设置里的图像模型", img["model"] == "agnes-image-2.5-flash", img)
check("来源标记 recent-run", img["source"] == "recent-run", img)
check("凭据只给占位不泄露", img["key"] == "***(前端设置)", img)
check("host 取自设置里的端点", img["host"] == "apihub.agnes-ai.com", img)
check("同时下发 .env 默认值备查", bool(img.get("env_model")), img)

# 2) 没有任何设置：回落 .env 默认
store.latest_settings = lambda: {}
img = health_image()
check("无设置时回落 .env 默认模型", img["model"] == config.AGNES_IMAGE_MODEL, img)
check("来源标记 env-default", img["source"] == "env-default", img)

# 3) 旧会话（那时还没有 image_* 字段）：也要给出可用默认，不能是空串
store.latest_settings = lambda: {"model": "agnes-2.5-flash", "api_key": "***"}
img = health_image()
check("旧会话缺字段时不留空", bool(img["model"]), img)
check("旧会话来源仍是 env-default", img["source"] == "env-default", img)

# 4) 只换图床不写模型：host 跟着变，模型回落默认
store.latest_settings = lambda: {"image_base_url": "https://api.siliconflow.cn/v1"}
img = health_image()
check("只换图床时 host 跟着变", img["host"] == "api.siliconflow.cn", img)
check("只换图床也标 recent-run", img["source"] == "recent-run", img)

# 5) 空串/空白不该覆盖默认（前端留空 = 跟随默认）
store.latest_settings = lambda: {"image_model": "   ", "image_base_url": ""}
img = health_image()
check("空白值不覆盖默认", img["model"] == config.AGNES_IMAGE_MODEL, img)
check("空白值不标记 recent-run", img["source"] == "env-default", img)

store.latest_settings = _orig

print(f"\n通过 {PASS} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
