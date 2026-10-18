"""CAD 运行时自检：本地优先 / uvx 解释器固化 / 失败结论不永久缓存。

背景：cadgen 走 uvx 每次都要重新解析环境（~21s），且并发会撞 uv 的缓存锁
（表现为 returncode≠0 且 stdout/stderr 全空）—— 用户看到的就是一条空消息。
这里把三条修复钉成断言，防止改回去。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

import cad  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


print("[1] 运行时优先级：云端沙箱优先，没配 key 才回落本机")
# 本机 8GB 内存扛不住并发 OCP（实测 8 并发 7 崩），所以 auto 现在优先 E2B。
# 没配 E2B_API_KEY 时必须安静回落本机，不能因为多了个可选运行时就变得不可用。
with mock.patch.object(cad, "_e2b_ready", return_value=False):
    kind, argv = cad.detect_runner()
check("无沙箱时走本机运行时", kind == "local", kind)
check("本机 argv 就是当前解释器", argv == [sys.executable], str(argv))
with mock.patch.object(cad, "_e2b_ready", return_value=True):
    kind_e2b, _ = cad.detect_runner()
check("配了沙箱时 auto 优先 e2b", kind_e2b == "e2b", kind_e2b)

# 下面几组验证的是本机路径，固定运行时，别被 auto 的沙箱选择带偏
cad.detect_runner = lambda: ("local", [sys.executable])

print("\n[2] 非阻塞状态（/health 用）")
# 冷启动第一次 import build123d 要 7s 上下 —— 这件事由 worker 的启动预热线程负责（_warm_cad）。
# 这里照样先付一次冷启动，验证的是「预热之后 /health 必须秒回」，也就是线上真实时序。
t0 = time.time()
st, info = cad.status()
warm = time.time() - t0
check("首次探测能拿到真实结论", st, info)
check("首次冷启动确实慢（所以必须靠启动预热摊掉）", warm < 60, f"{warm:.2f}s")
t1 = time.time()
st, info = cad.status()
dt = time.time() - t1
check("预热后 status 不卡", dt < 2.0, f"{dt:.2f}s")
check("info 带 build123d 版本", "0.13" in info or "0.11" in info, info)

print("\n[3] available 与互备")
av, ainfo = cad.available()
check("available 报告可用", av, ainfo)

print("\n[4] uvx 解释器固化")
check("能定位 uvx", bool(cad._uvx_exe()), cad._uvx_exe())
py = cad._resolve_cadgen_python()
check("解析出 cadgen 解释器", bool(py), str(py))
if py:
    check("固化路径真实存在", Path(py[0]).exists(), py[0])
    t0 = time.time()
    got = cad._cadgen_argv()
    dt = time.time() - t0
    check("固化后不再走 uvx 前缀", got[0] == py[0], str(got[:1]))
    check("取 argv 是毫秒级", dt < 1.0, f"{dt:.3f}s")

print("\n[5] 失败结论不永久缓存")
check("STATE_TTL 已设置", getattr(cad, "STATE_TTL", 0) > 0, str(getattr(cad, "STATE_TTL", 0)))
check("PROBE_TTL 已设置", getattr(cad, "PROBE_TTL", 0) > 0, str(getattr(cad, "PROBE_TTL", 0)))
cad._probe_cache["local"] = (False, "模拟一次 uvx 抖动", time.time())
st2, _info2 = cad._probe([sys.executable], "local")
check("TTL 内的失败结论直接复用（不重跑 20s）", st2 is False)
cad._probe_cache.pop("local", None)
st3, info3 = cad.available()
check("清掉缓存后能重新探测成功", st3, info3)

print(f"\n{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
