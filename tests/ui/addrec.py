import sqlite3, sys
from datetime import datetime, timedelta, timezone
c = sqlite3.connect(sys.argv[1])
cols = [r[1] for r in c.execute("pragma table_info(workrecord)")]
now = datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)
row = {"user_id": 1, "job_id": int(sys.argv[2]), "clock_in": (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S.000000")}
for k in cols:
    if k not in row and k != "id":
        row[k] = None
row.update({k: v for k, v in {"gps": 0, "void_reason": "", "note": ""}.items() if k in cols})
c.execute(f"insert into workrecord({','.join(row)}) values({','.join('?'*len(row))})", list(row.values()))
c.commit()
