"""端到端冒烟：发需求 -> 读 SSE -> 打印事件摘要 -> 列出产物。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8001"
REQ = sys.argv[1] if len(sys.argv) > 1 else "设计一个桌面式温湿度监测小夜灯，OLED 显示，USB 供电"
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 420.0


def post_run() -> str:
    body = json.dumps({"requirement": REQ}, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(
        BASE + "/api/run", data=body, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)["run_id"]


def main() -> int:
    rid = post_run()
    print(f"run_id={rid}\nrequirement={REQ}\n" + "-" * 60)
    counts: dict[str, int] = {}
    started = time.time()
    with urllib.request.urlopen(BASE + f"/api/stream/{rid}", timeout=TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            t = ev.get("type")
            counts[t] = counts.get(t, 0) + 1
            if t == "token":
                print(f"[{ev.get('agent')}] {ev.get('text','')[:160]}", end="")
            elif t == "tool_start":
                print(f"\n>> tool {ev.get('name')} ({ev.get('agent')}) {str(ev.get('args',''))[:120]}")
            elif t == "tool_end":
                print(f"<< {str(ev.get('result',''))[:200]}")
            elif t == "artifact":
                print(f"\n** artifact {ev.get('path')} ({ev.get('size')}B)")
            elif t == "todo":
                print(f"\n## todos: " + " | ".join(f"{i['content']}:{i['status']}" for i in ev["items"]))
            elif t in ("error", "done", "closed"):
                if t == "error":
                    print(f"\n!! ERROR {ev.get('message','')[:800]}")
                elif t == "done":
                    print(f"\n== DONE ==\n{ev.get('summary','')[:2000]}")
                break
            if time.time() - started > TIMEOUT:
                print("\n!! 超时中断")
                break
    print("-" * 60)
    print("事件统计:", counts)
    with urllib.request.urlopen(BASE + f"/api/runs/{rid}/files", timeout=30) as resp:
        data = json.load(resp)
    print(f"产物 {len(data['files'])} 个：")
    for f in data["files"]:
        print(f"  {f['path']}  {f['size']}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
