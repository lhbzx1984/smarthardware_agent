"""观察一次运行状态与产物落盘，直到终态或超时。

用法: python scripts/watch_run.py <run_id> [timeout_sec]
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8001"


def get(path: str) -> dict:
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"_err": str(e)}


def main() -> int:
    rid = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    t0 = time.time()
    last = 0
    seen: set[str] = set()
    print(f"watch run={rid} timeout={timeout}s", flush=True)
    while time.time() - t0 < timeout:
        d = get(f"/api/runs/{rid}")
        st = d.get("status")
        files = [f.get("path") if isinstance(f, dict) else f for f in (d.get("files") or [])]
        new = [f for f in files if f not in seen]
        for f in new:
            print(f"  + {f}", flush=True)
            seen.update(new)
        if time.time() - last > 30:
            print(f"  [{int(time.time() - t0)}s] status={st} files={len(files)}", flush=True)
            last = time.time()
        if st in {"done", "failed", "aborted", "interrupted", "cancelled"}:
            print(f"terminal status={st} elapsed={int(time.time() - t0)}s files={len(files)}", flush=True)
            man = get(f"/api/runs/{rid}/manifest")
            for g in man.get("stages", []):
                if g.get("files"):
                    print(f"  {g['id']:<14} {g.get('plan_title','')[:40]:<42} {g['n_files']} 文件", flush=True)
            return 0
        time.sleep(5)
    print(f"timeout after {timeout}s, last status unknown", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
