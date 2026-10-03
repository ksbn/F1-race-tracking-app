"""Save one finished session to snapshot.json.gz so the app can replay it when OpenF1 blocks access.
Usage: python3 snapshot.py [session_key]     (default: latest completed Race)"""
import gzip, json, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.openf1.org/v1"


def get(path, **p):
    url = f"{API}/{path}?" + urllib.parse.urlencode(p)
    for i in range(6):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code == 429:
                time.sleep(2 ** i)
                continue
            raise SystemExit(f"OpenF1 answered {e.code} for {path}. It is still blocked; try again later.")
    raise SystemExit("Rate limited too long; try again in a minute.")


key = sys.argv[1] if len(sys.argv) > 1 else None
if not key:
    done = get("sessions", **{"date_end<": datetime.now(timezone.utc).isoformat()})
    races = [s for s in done if s.get("session_name") == "Race"] or done
    key = max(races, key=lambda s: s["date_end"])["session_key"]
snap = {"session_key": int(key)}
for ep in ("sessions", "drivers", "position", "intervals", "race_control", "weather", "laps", "stints"):
    snap[ep] = get(ep, session_key=key)
    print(ep, len(snap[ep]))
    time.sleep(1)
for l in snap["laps"]:  # track outline = one driver's complete lap
    if l.get("lap_duration") and l.get("date_start") and l["lap_number"] > 1:
        s0 = datetime.fromisoformat(l["date_start"])
        snap["location"] = get("location", session_key=key, driver_number=l["driver_number"],
                               **{"date>": s0.isoformat(), "date<": (s0 + timedelta(seconds=l["lap_duration"])).isoformat()})
        print("location", len(snap["location"]))
        break
with gzip.open("snapshot.json.gz", "wt") as f:
    json.dump(snap, f)
print("saved snapshot.json.gz for session", key)
