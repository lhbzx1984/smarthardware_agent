"""诊断 LLM 网关连通性：定位 OpenAIConnectionError 的真实层（DNS / TCP / TLS / HTTP / 代理）。

用法：python scripts/diag_llm_conn.py [base_url] [model]
输出全部脱敏，不打印密钥。
"""
from __future__ import annotations

import os
import socket
import ssl
import sys
import time
from urllib.parse import urlparse

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import config  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else config.DEFAULT_BASE_URL).rstrip("/")
MODEL = sys.argv[2] if len(sys.argv) > 2 else config.MODEL
KEY = config.AGNES_API_KEY if "agnes" in BASE else config.SILICONFLOW_API_KEY


def line(t: str) -> None:
    print(f"[diag] {t}", flush=True)


line(f"target   = {BASE}")
line(f"model    = {MODEL}")
line(f"key      = {config.mask(KEY)}")

u = urlparse(BASE)
host, port = u.hostname, (u.port or (443 if u.scheme == "https" else 80))

# 1) 代理环境变量
proxies = {k: v for k, v in os.environ.items() if "proxy" in k.lower()}
line(f"env proxy= {proxies or '(none)'}")

# 2) DNS
try:
    t0 = time.time()
    ips = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    line(f"dns      = OK {sorted({i[4][0] for i in ips})} ({time.time() - t0:.2f}s)")
except Exception as e:  # noqa: BLE001
    line(f"dns      = FAIL {type(e).__name__}: {e}")

# 3) TCP
try:
    t0 = time.time()
    with socket.create_connection((host, port), timeout=8) as s:
        line(f"tcp      = OK ({time.time() - t0:.2f}s)")
except Exception as e:  # noqa: BLE001
    line(f"tcp      = FAIL {type(e).__name__}: {e}")

# 4) TLS（默认 certifi）
if u.scheme == "https":
    try:
        t0 = time.time()
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=8) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                line(f"tls      = OK {ss.version()} ({time.time() - t0:.2f}s)")
    except Exception as e:  # noqa: BLE001
        line(f"tls      = FAIL {type(e).__name__}: {e}")

# 5) HTTP：三种组合（默认 / 关闭校验 / 不走代理）
for label, verify, trust in (
    ("http[verify=default]", config.ssl_verify(), True),
    ("http[verify=off]    ", False, True),
    ("http[no-proxy]      ", config.ssl_verify(), False),
):
    try:
        t0 = time.time()
        with httpx.Client(verify=verify, trust_env=trust, timeout=20.0) as c:
            r = c.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"} if KEY else {})
        line(f"{label} = {r.status_code} ({time.time() - t0:.2f}s) {r.text[:80]!r}")
    except Exception as e:  # noqa: BLE001
        line(f"{label} = FAIL {type(e).__name__}: {str(e)[:160]}")

# 6) 真实一轮 chat（极小 token）
if KEY:
    try:
        t0 = time.time()
        with httpx.Client(verify=config.ssl_verify(), timeout=60.0) as c:
            r = c.post(
                f"{BASE}/chat/completions",
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
            )
        line(f"chat     = {r.status_code} ({time.time() - t0:.2f}s) {r.text[:160]!r}")
    except Exception as e:  # noqa: BLE001
        line(f"chat     = FAIL {type(e).__name__}: {str(e)[:200]}")
else:
    line("chat     = SKIP (no key)")
