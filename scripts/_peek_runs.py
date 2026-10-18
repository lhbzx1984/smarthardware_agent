import sqlite3, time, sys
c = sqlite3.connect("data/agent.db")
c.row_factory = sqlite3.Row
print("tables:", [r[0] for r in c.execute("select name from sqlite_master where type='table'")])
print("runs cols:", [r[1] for r in c.execute("pragma table_info(runs)")])
print("events cols:", [r[1] for r in c.execute("pragma table_info(events)")])
rows = c.execute("select * from runs order by rowid desc limit 3").fetchall()
for r in rows:
    print({k: (str(r[k])[:80]) for k in r.keys()})
if rows:
    rid = rows[0]["id"]
    print("--- events of", rid)
    for e in c.execute(
        "select type, substr(coalesce(payload,''),1,300) p from events where run_id=? "
        "and type in ('error','tool_result','finish') order by rowid desc limit 15", (rid,)):
        print(e["type"], "|", e["p"].replace("\n", " ")[:200])
