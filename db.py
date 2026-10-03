import os, sqlite3
from contextlib import closing

PATH = os.getenv("DB_PATH", "f1.db")


def _q(sql, args=(), many=False, read=False):
    with closing(sqlite3.connect(PATH)) as c:
        cur = c.executemany(sql, args) if many else c.execute(sql, args)
        rows = cur.fetchall() if read else None
        c.commit()
        return rows


def init():
    os.makedirs(os.path.dirname(os.path.abspath(PATH)), exist_ok=True)
    with closing(sqlite3.connect(PATH)) as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS sessions(session_key INTEGER PRIMARY KEY, name TEXT, type TEXT, circuit TEXT, country TEXT, date_start TEXT);
CREATE TABLE IF NOT EXISTS laps(session_key INTEGER, driver INTEGER, lap INTEGER, duration REAL, PRIMARY KEY(session_key, driver, lap));""")


def save_session(s):
    _q("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?)", (s["session_key"], s.get("session_name"),
       s.get("session_type"), s.get("circuit_short_name"), s.get("country_name"), s.get("date_start")))


def save_laps(sk, by_driver):
    rows = [(sk, n, l["lap_number"], l["lap_duration"]) for n, ls in by_driver.items() for l in ls if l.get("lap_duration")]
    if rows:
        _q("INSERT OR REPLACE INTO laps VALUES(?,?,?,?)", rows, many=True)


def laps(sk):
    out = {}
    for d, lap, dur in _q("SELECT driver,lap,duration FROM laps WHERE session_key=? ORDER BY lap", (sk,), read=True):
        out.setdefault(d, []).append([lap, dur])
    return out


def sessions():
    cols = ("session_key", "name", "type", "circuit", "country", "date_start")
    return [dict(zip(cols, r)) for r in _q("SELECT * FROM sessions ORDER BY date_start DESC LIMIT 50", read=True)]

