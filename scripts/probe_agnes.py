"""Agnes 定性探测：分别用两个候选 key 测 文本 / 图像 两条链路。

用法：python scripts/probe_agnes.py
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://apihub.agnes-ai.com/v1"
UA = {"User-Agent": "agent-sdk-probe/1.0"}


def load_env_keys() -> dict[str, str]:
    out = {}
    env = os.getenv("AGNES_API_KEY")
    if env:
        out["环境变量"] = env.strip()
    try:
        for line in open(".env", encoding="utf-8"):
            line = line.strip()
            if line.startswith("AGNES_API_KEY="):
                v = line.split("=", 1)[1].strip()
                if v:
                    out.setdefault(".env", v)
    except OSError:
        pass
    return out


def http(path: str, key: str, payload: dict, timeout: int = 90):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={**UA, "Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400], time.time() - t0
    except Exception as e:  # noqa: BLE001
        return "ERR", f"{type(e).__name__}: {e}", time.time() - t0


def probe_chat(key: str) -> tuple[str, str]:
    code, body, dt = http(
        "/chat/completions",
        key,
        {"model": "agnes-2.5-flash", "messages": [{"role": "user", "content": "回复两个字：可用"}], "max_tokens": 32},
        timeout=60,
    )
    if code == 200:
        try:
            txt = json.loads(body)["choices"][0]["message"]["content"]
            return f"200 OK ({dt:.1f}s)", (txt or "").strip()[:40]
        except Exception:  # noqa: BLE001
            return f"200 OK ({dt:.1f}s)", body[:60]
    return f"{code} ({dt:.1f}s)", body[:160]


def probe_image(key: str) -> tuple[str, str]:
    code, body, dt = http(
        "/images/generations",
        key,
        {"model": os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.5-flash"), "prompt": "一个白色圆柱形桌面小夜灯", "size": "1024x1024"},
        timeout=120,
    )
    if code == 200:
        try:
            d = json.loads(body)
            item = (d.get("data") or [{}])[0]
            url = item.get("url") or ""
            return f"200 OK ({dt:.1f}s)", f"url={url[:70]}" if url else f"无 url，响应={body[:120]}"
        except Exception:  # noqa: BLE001
            return f"200 OK ({dt:.1f}s)", body[:80]
    return f"{code} ({dt:.1f}s)", body[:200]


def main() -> None:
    keys = load_env_keys()
    if not keys:
        print("没有找到任何 Agnes key")
        return
    for label, key in keys.items():
        print(f"\n===== {label}  key= {key[:10]}...{key[-6:]} (len={len(key)}) =====")
        c, cd = probe_chat(key)
        print(f"  文本 /chat/completions : {c}")
        print(f"     -> {cd}")
        i, idetail = probe_image(key)
        print(f"  图像 /images/generations: {i}")
        print(f"     -> {idetail}")


if __name__ == "__main__":
    main()
