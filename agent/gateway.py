"""网关选路与存活探测。

为什么需要这一层：实测（2026-10-12 本机，agnes 网关）同一个网关下不同模型的排队
深度差一个数量级 —— /chat/completions 简单请求首字节 flash 72s、pro-alpha 44s，
而前一天的测量是 flash 12.8s / pro-alpha 1.4s。**哪个模型快是随时间变的**，写死在
配置里没有意义，只能在开跑前现测。

一次探测的代价（几十秒）换的是后面 30+ 轮往返全走快模型，净收益是分钟级的。
全部候选都探不通时返回空，调用方按原样继续（重试链路负责兜底），不在这里拦流程。
"""
from __future__ import annotations

import concurrent.futures as futures
import time
from typing import Any

_PROBE_MSG = [{"role": "user", "content": "只回复一个字：好"}]


def _probe_one(base: str, key: str, model: str, timeout: float) -> tuple[str, float | None, str]:
    """探测单个模型的流式首字节耗时。返回 (模型名, 秒数|None, 失败原因)。"""
    import httpx

    import config

    body = {"model": model, "messages": _PROBE_MSG, "max_tokens": 8, "stream": True}
    # httpx 0.28 起 Timeout 必须给 default 或四个参数全给，只给 connect+read 会 ValueError
    to = httpx.Timeout(
        timeout,
        connect=min(20.0, timeout),
        read=timeout,
        write=15.0,
        pool=15.0,
    )
    t0 = time.time()
    try:
        with httpx.Client(verify=config.ssl_verify(), timeout=to) as cli:
            with cli.stream(
                "POST",
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as r:
                if r.status_code != 200:
                    return model, None, f"HTTP{r.status_code}"
                for _ in r.iter_bytes():
                    return model, round(time.time() - t0, 2), ""
                return model, None, "empty"
    except Exception as e:  # noqa: BLE001  探测失败是正常结果，不是异常路径
        return model, None, type(e).__name__


def pick_fastest(
    base: str,
    key: str,
    candidates: list[str],
    timeout: float = 45.0,
) -> tuple[str, float, list[tuple[str, float | None, str]]]:
    """并发探测候选模型，返回 (最快可用模型, 耗时, 全部结果)。全部不可用则返回 ("", 0, results)。"""
    cands = [c for c in dict.fromkeys(candidates) if c]  # 去重保序
    if not cands or not key:
        return "", 0.0, []
    results: list[tuple[str, float | None, str]] = []
    with futures.ThreadPoolExecutor(max_workers=len(cands)) as pool:
        futs = {pool.submit(_probe_one, base.rstrip("/"), key, m, timeout): m for m in cands}
        for f in futures.as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:  # noqa: BLE001
                results.append((futs[f], None, type(e).__name__))
    ok = [(m, s) for m, s, _ in results if s is not None]
    if not ok:
        return "", 0.0, results
    model, sec = min(ok, key=lambda x: x[1])
    return model, sec, results


def reachable(base: str, key: str, timeout: float = 20.0) -> bool:
    """轻量探活：只问 /models，不生成。用来判断「网关整体不可用」还是「只是慢」。

    超时别给太短：实测网关拥塞时连 /models 都要十几秒，8 秒会把「很慢」误判成「挂了」，
    那就不开跑 —— 明明能跑的任务被自己的探活拦下来，比慢更糟。
    """
    import httpx

    import config

    if not key:
        return False
    try:
        with httpx.Client(verify=config.ssl_verify(), timeout=timeout) as cli:
            r = cli.get(f"{base.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"})
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


# 这类状态码说明「网关是通的，但这次调用注定失败」：重试、换模型、等待恢复都没用，
# 只有换 key / 充值 / 改模型名才行。把它们和「连不上」区分开，否则会白等一整个恢复窗口。
_FATAL_HTTP = ("HTTP401", "HTTP402", "HTTP403", "HTTP404", "HTTP422")


def classify(results: list[tuple[str, float | None, str]]) -> str:
    """从探测结果判断网关状态。

    返回：
    - ""            有模型可用，照常开跑
    - "quota"       网关通，但鉴权/额度/模型名有问题（402 余额不足、401 key 无效、404 模型不存在）；
                    重试和等待都救不了，必须换 key / 充值 / 改模型
    - "unreachable" 连不上（超时、连接重置、TLS 握手失败）—— 才值得等网关恢复
    """
    if any(s is not None for _, s, _ in results):
        return ""
    errs = [e or "" for _, _, e in results]
    if not errs:
        return ""  # 没探测过（单模型等场景）按可用算，不改变原行为
    if all(any(k in e for k in _FATAL_HTTP) for e in errs):
        return "quota"
    return "unreachable"


def describe(results: list[tuple[str, float | None, str]]) -> str:
    """把探测结果压成一行人话，给事件流用。"""
    parts = []
    for m, s, err in results:
        parts.append(f"{m}={s:.1f}s" if s is not None else f"{m}✗{err or 'fail'}")
    return " / ".join(parts)
