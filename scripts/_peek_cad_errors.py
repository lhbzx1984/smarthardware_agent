import json, sqlite3, sys
rid = sys.argv[1] if len(sys.argv) > 1 else "b5258537540f"
c = sqlite3.connect("data/agent.db")
rows = c.execute(
    "select seq, payload from events where run_id=? and payload like '%text_to_cad%' order by seq",
    (rid,)).fetchall()
for seq, p in rows:
    try:
        d = json.loads(p)
    except Exception:
        continue
    name = d.get("name") or ""
    detail = d.get("detail") or ""
    if name == "text_to_cad" and ("失败" in detail or "Traceback" in detail):
        print(f"\n######## seq {seq} ########")
        print(detail[:1400])
