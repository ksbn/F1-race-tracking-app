import json, os, sys, time, urllib.error, urllib.parse, urllib.request
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
            raise SystemExit(f"OpenF1 answered {e.code}. It is probably locked while a session is live; try later.")
    raise SystemExit("Rate limited too long; try again in a minute.")


if len(sys.argv) < 2:
    raise SystemExit('Usage: python3 circuit_outline.py "Singapore" [session_key]')
name = sys.argv[1].strip()
key = sys.argv[2] if len(sys.argv) > 2 else None
if not key:
    now = datetime.now(timezone.utc).isoformat()
    done = [s for s in get("sessions", circuit_short_name=name, session_name="Race") if s["date_end"] < now]
    if not done:
        raise SystemExit(f"No completed race found for '{name}'. Pass a session key as the second argument.")
    key = max(done, key=lambda s: s["date_end"])["session_key"]
laps = [l for l in get("laps", session_key=key) if l.get("lap_duration") and l.get("date_start") and l["lap_number"] > 1]
if not laps:
    raise SystemExit("No usable laps in that session.")
lap = min(laps, key=lambda l: l["lap_duration"])  # fastest lap = a clean pass round the circuit
s0 = datetime.fromisoformat(lap["date_start"])
pts = get("location", session_key=key, driver_number=lap["driver_number"],
          **{"date>": s0.isoformat(), "date<": (s0 + timedelta(seconds=lap["lap_duration"])).isoformat()})
out = [[p["x"], p["y"]] for p in pts if p["x"] or p["y"]]
if len(out) < 100:
    raise SystemExit(f"Only {len(out)} position points came back; not saving.")
os.makedirs("circuits", exist_ok=True)
slug = name.lower().replace(" ", "-")
with open(f"circuits/{slug}.json", "w") as f:
    json.dump(out, f)
print(f"saved circuits/{slug}.json ({len(out)} points, session {key}, driver {lap['driver_number']})")
