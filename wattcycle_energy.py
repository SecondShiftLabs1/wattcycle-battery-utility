"""Read-only energy analytics from observed samples; never bridge telemetry gaps."""
from datetime import datetime, timedelta
import sqlite3
import math

def energy_summary(path, days=7, gap=30, now=None):
    now=now or datetime.now()
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days-1)
    buckets={}
    for i in range(days):
        buckets[(start+timedelta(days=i)).date()]=dict(used=0.,charged=0.,ah=0.,seconds=0.,active=0.)
    with sqlite3.connect(path,timeout=.2) as db:
        rows=db.execute("SELECT timestamp,current,power FROM battery_log WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp,id",(start.isoformat(),now.isoformat())).fetchall()
    previous=None
    for stamp,amps,watts in rows:
        try:
            t=datetime.fromisoformat(stamp)
            a,p=float(amps),float(watts)
            if not all(map(math.isfinite,(a,p))):raise ValueError()
        except (TypeError,ValueError):
            previous=None
            continue
        if previous:
            old,oa,op=previous
            dt=(t-old).total_seconds()
            if 0<dt<=gap:
                cursor=old
                while cursor<t:
                    end=min(t,cursor.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1))
                    seconds=(end-cursor).total_seconds()
                    b=buckets.get(cursor.date())
                    if b is not None:
                        b["seconds"]+=seconds
                        b["used"]+=max(0,-op)*seconds/3600
                        b["charged"]+=max(0,op)*seconds/3600
                        b["ah"]+=max(0,-oa)*seconds/3600
                        if oa<-.1:b["active"]+=seconds
                    cursor=end
        previous=(t,a,p)
    totals={k:sum(b[k] for b in buckets.values()) for k in ("used","charged","ah","seconds","active")}
    totals["average_w"]=totals["used"]*3600/totals["seconds"] if totals["seconds"] else None
    totals["active_w"]=totals["used"]*3600/totals["active"] if totals["active"] else None
    totals["average_a"]=totals["ah"]*3600/totals["seconds"] if totals["seconds"] else None
    totals["coverage"]=min(1,totals["seconds"]/max(1,(now-start).total_seconds()))
    return buckets,totals

class EventLog:
    def __init__(self,path):
        self.path=path
        with sqlite3.connect(path,timeout=2) as db:
            db.execute("CREATE TABLE IF NOT EXISTS protection_events (id INTEGER PRIMARY KEY, name TEXT NOT NULL, started TEXT NOT NULL, cleared TEXT, soc REAL, voltage REAL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_open_protection ON protection_events(name) WHERE cleared IS NULL")
    def observe(self,flags,d):
        stamp=datetime.now().isoformat(timespec="seconds")
        flags=set(flags)
        with sqlite3.connect(self.path,timeout=.2) as db:
            opened={r[0] for r in db.execute("SELECT name FROM protection_events WHERE cleared IS NULL")}
            for name in flags-opened:
                db.execute("INSERT OR IGNORE INTO protection_events(name,started,soc,voltage) VALUES(?,?,?,?)",(name,stamp,d.soc,d.module_voltage))
            for name in opened-flags:
                db.execute("UPDATE protection_events SET cleared=? WHERE name=? AND cleared IS NULL",(stamp,name))
        return flags-opened
