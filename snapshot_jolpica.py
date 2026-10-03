"""Build snapshot.json.gz from Jolpica-F1 (no key, no live-session lockouts) in the format the app replays.
Real positions, lap times and gaps. No tyres, weather, race control or track outline.
Usage: python3 snapshot_jolpica.py [season round]     (default: last completed race)"""
import gzip, json, sys, time, urllib.error, urllib.request, zlib
from datetime import datetime, timedelta

API = "https://api.jolpi.ca/ergast/f1"
COLOURS = {"mercedes": "27F4D2", "ferrari": "E80020", "red_bull": "3671C6", "mclaren": "FF8000", "aston_martin": "229971",
           "alpine": "0093CC", "williams": "64C4FF", "haas": "B6BABD", "rb": "6692FF", "sauber": "52E252"}


def fetch(path):
    for i in range(6):
        try:
            with urllib.request.urlopen(f"{API}/{path}", timeout=60) as r:
                return json.load(r)["MRData"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** i)
                continue
            raise SystemExit(f"Jolpica answered {e.code} for {path}")
    raise SystemExit("Jolpica rate limit; try again in a minute")


def secs(t):
    m, _, s = t.rpartition(":")
    return (int(m) * 60 if m else 0) + float(s)


a = sys.argv[1:]
races = fetch(f"{a[0]}/{a[1]}/results.json" if len(a) == 2 else "current/last/results.json")["RaceTable"]["Races"]
if not races:
    raise SystemExit("No completed race found.")
race = races[0]
season, rnd = race["season"], race["round"]
drivers, num, grid = [], {}, {}
for i, r in enumerate(race["Results"]):
    d, c = r["Driver"], r["Constructor"]
    n = int(r.get("number") or d["permanentNumber"])
    num[d["driverId"]] = n
    grid[n] = int(r.get("grid") or 0) or 20 + i  # pit-lane starts go to the back
    drivers.append({"driver_number": n, "name_acronym": d.get("code") or d["familyName"][:3].upper(),
                    "full_name": f'{d["givenName"]} {d["familyName"]}', "team_name": c["name"],
                    "team_colour": COLOURS.get(c["constructorId"]) or "%06X" % (zlib.crc32(c["constructorId"].encode()) & 0xFFFFFF)})

by_lap, offset, total = {}, 0, 1
while offset < total:
    d = fetch(f"{season}/{rnd}/laps.json?limit=100&offset={offset}")
    total, offset = int(d["total"]), offset + 100
    for lap in (d["RaceTable"]["Races"] or [{}])[0].get("Laps", []):
        by_lap.setdefault(int(lap["number"]), []).extend(lap["Timings"])
    time.sleep(0.4)
if not by_lap:
    raise SystemExit("Jolpica has no lap data for this race.")

start = datetime.fromisoformat(f'{race["date"]}T{(race.get("time") or "13:00:00Z").replace("Z", "+00:00")}')
iso = lambda s: (start + timedelta(seconds=s)).isoformat()
cum, laps, pos, gaps = {}, [], [{"driver_number": n, "position": p, "date": iso(-1)} for n, p in grid.items()], []
for n in sorted(by_lap):
    rows = []
    for t in by_lap[n]:
        d = num.get(t["driverId"])
        if d is None or not t.get("time"):
            continue
        dur = secs(t["time"])
        laps.append({"driver_number": d, "lap_number": n, "lap_duration": dur, "date_start": iso(cum.get(d, 0))})
        cum[d] = cum.get(d, 0) + dur
        rows.append((int(t["position"]), d, cum[d]))
        pos.append({"driver_number": d, "position": int(t["position"]), "date": iso(cum[d])})
    rows.sort()
    for i, (p, d, c) in enumerate(rows):
        gaps.append({"driver_number": d, "date": iso(c), "gap_to_leader": round(c - rows[0][2], 3),
                     "interval": round(c - rows[i - 1][2], 3) if i else 0.0})

key = int(season) * 100 + int(rnd)
snap = {"session_key": key, "drivers": drivers, "position": pos, "intervals": gaps, "race_control": [], "weather": [],
        "laps": laps, "stints": [],
        "sessions": [{"session_key": key, "session_name": "Race", "session_type": "Race",
                      "country_name": race["Circuit"]["Location"].get("country"), "circuit_short_name": race["raceName"],
                      "date_start": start.isoformat(), "date_end": iso(max(cum.values()) + 300)}]}
with gzip.open("snapshot.json.gz", "wt") as f:
    json.dump(snap, f)
print(f'saved snapshot.json.gz: {race["raceName"]} {season}, {len(laps)} laps, {len(drivers)} drivers')
