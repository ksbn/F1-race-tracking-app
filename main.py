import asyncio, gzip, json, logging, os, time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db

OPENF1 = "https://api.openf1.org/v1"
JOLPICA = "https://api.jolpi.ca/ergast/f1"
TOKEN = os.getenv("OPENF1_TOKEN")  
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "4"))
SESSION = os.getenv("REPLAY_SESSION", "latest")  
SPEED = float(os.getenv("REPLAY_SPEED", "5"))
REPLAY = SESSION != "latest"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("f1")

state = {"session": None, "drivers": {}, "rows": [], "status": "starting", "updated": None, "cars": {},
         "messages": [], "weather": None, "has_outline": False, "replay": False}
_raw = {"pos": {}, "gap": {}, "laps": {}, "stints": {}, "since_pos": None, "since_gap": None, "key": None}
clients: set[WebSocket] = set()
_cache: dict = {}


SNAP: dict | None = None  # bundled snapshot (dict), set when OpenF1 is unreachable


def local_get(path, p):
    """Answer an OpenF1-style query from the bundled snapshot."""
    if path == "location" and "driver_number" not in p:
        return []  # snapshots keep only the track outline, no live car positions
    out = []
    for r in (SNAP or {}).get(path, []):
        if "driver_number" in p and r.get("driver_number") != int(p["driver_number"]):
            continue
        d = r.get("date")
        if d and (("date>" in p and d <= p["date>"]) or ("date<" in p and d >= p["date<"])):
            continue
        out.append(r)
    return out


def use_snapshot():
    global SNAP, SESSION, REPLAY
    try:
        with gzip.open("snapshot.json.gz", "rt") as f:
            SNAP = json.load(f)
    except OSError:
        return False
    SESSION, REPLAY = str(SNAP["session_key"]), True # pyright: ignore[reportOptionalSubscript]
    _raw["key"] = None
    state["fallback"] = True
    log.warning("OpenF1 unavailable; replaying bundled snapshot of session %s", SESSION)
    return True


async def get(client, path, **params):
    if SNAP is not None:
        return local_get(path, params)
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    for attempt in range(4):  
        r = await client.get(f"{OPENF1}/{path}", params=params, headers=headers, timeout=15)
        if r.status_code != 429:
            break
        await asyncio.sleep(2 ** attempt)
    if r.status_code == 404:  
        return []
    r.raise_for_status()
    return r.json()


async def inc(client, path, since_key, cap, default=None):
    """Fetch only records newer than the last one seen."""
    params = {"session_key": _raw["key"], **cap}
    since = _raw[since_key] or default
    if since:
        params["date>"] = since
    rows = await get(client, path, **params)
    if rows:
        _raw[since_key] = max(r["date"] for r in rows)
    return rows


def build_rows():
    rows = []
    for num, d in state["drivers"].items():
        pos = _raw["pos"].get(num)
        if pos is None:
            continue
        gap = _raw["gap"].get(num, {})
        laps = _raw["laps"].get(num, [])
        done = [l for l in laps if l.get("lap_duration")]
        stint = _raw["stints"].get(num)
        rows.append({
            "pos": pos, "num": num, "code": d.get("name_acronym"), "name": d.get("full_name"),
            "team": d.get("team_name"), "colour": "#" + (d.get("team_colour") or "888888"),
            "gap": gap.get("gap_to_leader"), "interval": gap.get("interval"),
            "lap": max((l["lap_number"] for l in laps), default=None),
            "last": done[-1]["lap_duration"] if done else None,
            "best": min((l["lap_duration"] for l in done), default=None),
            "tyre": stint.get("compound") if stint else None,
            "tyre_age": (stint.get("tyre_age_at_start") or 0) + (
                (max((l["lap_number"] for l in laps), default=0) - (stint["lap_start"] or 0)) if stint else 0) if stint else None,
        })
    rows.sort(key=lambda r: r["pos"])
    return rows


async def broadcast():
    dead = []
    for ws in clients:
        try:
            await ws.send_json(state)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def use_fallback(client):
    """Live data is blocked (no token): switch to replaying the latest completed race."""
    global SESSION, REPLAY
    done = await get(client, "sessions", **{"date_end<": datetime.now(timezone.utc).isoformat()})
    races = [s for s in done if s.get("session_name") == "Race"] or done
    if not races:
        return False
    SESSION, REPLAY = str(max(races, key=lambda s: s["date_end"])["session_key"]), True
    _raw["key"] = None  # forces a clean reset and a fresh replay clock
    state["fallback"] = True
    log.warning("live data blocked; replaying session %s", SESSION)
    return True


async def poll_loop():
    last_slow, t0 = 0, time.time()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                sess = (await get(client, "sessions", session_key=SESSION))[-1]
                if sess["session_key"] != _raw["key"]:  
                    _raw.update(pos={}, gap={}, laps={}, stints={}, key=sess["session_key"], outline=None,
                                since_pos=None, since_gap=None, since_loc=None, since_rc=None, since_wx=None)
                    drivers = await get(client, "drivers", session_key=sess["session_key"])
                    state.update(drivers={d["driver_number"]: d for d in drivers}, cars={}, messages=[],
                                 weather=None, has_outline=False)
                    last_slow, t0 = 0, time.time()
                    db.save_session(sess)
                    log.info("session %s %s", sess["session_key"], sess.get("session_name"))
                state["session"] = {k: sess.get(k) for k in
                                    ("session_name", "session_type", "circuit_short_name", "country_name", "date_start", "date_end")}
                state["replay"] = REPLAY
                if REPLAY:  
                    now = min(datetime.fromisoformat(sess["date_start"]) + timedelta(seconds=(time.time() - t0) * SPEED),
                              datetime.fromisoformat(sess["date_end"]))
                else:
                    now = datetime.now(timezone.utc)
                upper = now.isoformat()
                cap = {"date<": upper} if REPLAY else {}

                for p in await inc(client, "position", "since_pos", cap):
                    _raw["pos"][p["driver_number"]] = p["position"]
                for g in await inc(client, "intervals", "since_gap", cap):
                    _raw["gap"][g["driver_number"]] = g
                msgs = await inc(client, "race_control", "since_rc", cap)
                state["messages"] = (state["messages"] + msgs)[-40:]
                wx = await inc(client, "weather", "since_wx", cap)
                state["weather"] = wx[-1] if wx else state["weather"]
                for c in await inc(client, "location", "since_loc", cap, (now - timedelta(seconds=10)).isoformat()):
                    if c["x"] or c["y"]:
                        state["cars"][c["driver_number"]] = [c["x"], c["y"]]

                if time.time() - last_slow > (0 if SNAP is not None else 60): 
                    last_slow = time.time()
                    by_driver: dict = {}
                    for l in await get(client, "laps", session_key=_raw["key"]):
                        if REPLAY:
                            ds = l.get("date_start") or ""
                            if ds > upper:
                                continue
                            if ds and l.get("lap_duration") and datetime.fromisoformat(ds) + timedelta(seconds=l["lap_duration"]) > now:
                                l = {**l, "lap_duration": None}  # lap still in progress at the replay clock
                        by_driver.setdefault(l["driver_number"], []).append(l)
                    _raw["laps"] = {k: sorted(v, key=lambda l: l["lap_number"]) for k, v in by_driver.items()}
                    await asyncio.to_thread(db.save_laps, _raw["key"], _raw["laps"])
                    for s in await get(client, "stints", session_key=_raw["key"]):
                        seen = max((l["lap_number"] for l in _raw["laps"].get(s["driver_number"], [])), default=0)
                        cur = _raw["stints"].get(s["driver_number"])
                        if (s["lap_start"] or 0) <= max(seen, 1) and (not cur or s["stint_number"] >= cur["stint_number"]):
                            _raw["stints"][s["driver_number"]] = s

                if _raw["outline"] is None:  
                    for num, ls in _raw["laps"].items():
                        ok = [l for l in ls if l.get("lap_duration") and l.get("date_start") and l["lap_number"] > 1]
                        if not ok:
                            continue
                        s0 = datetime.fromisoformat(ok[0]["date_start"])
                        s1 = s0 + timedelta(seconds=ok[0]["lap_duration"])
                        if s1 <= now:
                            pts = await get(client, "location", session_key=_raw["key"], driver_number=num,
                                            **{"date>": s0.isoformat(), "date<": s1.isoformat()})
                            _raw["outline"] = [[p["x"], p["y"]] for p in pts[::3]]
                            state["has_outline"] = bool(_raw["outline"])
                        break

                state["rows"] = build_rows()
                state["status"] = "ok" if state["rows"] else "No timing data for this session yet"
                state["updated"] = time.strftime("%H:%M:%S")
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code in (401, 402, 403) and SNAP is None:
                    if not REPLAY:
                        try:
                            if await use_fallback(client):
                                continue
                        except Exception:
                            log.warning("latest-race fallback failed")
                    if use_snapshot():
                        continue
                state["status"] = ("Live data needs an OpenF1 token (set OPENF1_TOKEN). Showing last known data."
                                   if code in (401, 402, 403) else f"OpenF1 error {code}; retrying")
                log.exception(state["status"])
            except Exception as e:  
                state["status"] = f"Connection problem ({type(e).__name__}); retrying"
                log.exception(state["status"])
            await broadcast()
            await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def lifespan(app):
    db.init()
    if os.getenv("USE_SNAPSHOT"):
        use_snapshot()
    if os.getenv("SOURCE") == "f1stream":  # experimental: F1's own live timing stream
        import f1stream
        task = asyncio.create_task(f1stream.run(state, _raw, broadcast, db, lambda: bool(circuit_file()) or (snap_matches() and bool(snap_file().get("location")))))
    else:
        task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(title="F1 Live Tracker", lifespan=lifespan)


async def cached_jolpica(path, ttl=600):
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{JOLPICA}/{path}", timeout=15)
        r.raise_for_status()
        data = r.json()["MRData"]
    _cache[path] = (time.time(), data)
    return data


@app.get("/api/state")
async def get_state():
    return state


@app.get("/api/standings")
async def standings():
    d = await cached_jolpica("current/driverStandings.json")
    lists = d["StandingsTable"]["StandingsLists"]
    rows = lists[0]["DriverStandings"] if lists else []
    return [{"pos": r["position"], "name": f'{r["Driver"]["givenName"]} {r["Driver"]["familyName"]}',
             "team": r["Constructors"][0]["name"], "points": r["points"], "wins": r["wins"]} for r in rows]


@app.get("/api/schedule")
async def schedule():
    d = await cached_jolpica("current.json", ttl=3600)
    return [{"round": r["round"], "name": r["raceName"], "circuit": r["Circuit"]["circuitName"],
             "date": r["date"], "time": r.get("time")} for r in d["RaceTable"]["Races"]]


@app.get("/health")
async def health():
    return {"ok": True, "status": state["status"], "updated": state["updated"], "replay": REPLAY}


_snap_cache: dict = {}


def snap_file():
    if "d" not in _snap_cache:
        try:
            with gzip.open("snapshot.json.gz", "rt") as f:
                _snap_cache["d"] = json.load(f)
        except OSError:
            _snap_cache["d"] = {}
    return _snap_cache["d"]


def snap_matches():
    """True when the bundled snapshot is of the session the F1 stream is currently showing."""
    s = (snap_file().get("sessions") or [{}])[0]
    cur = state.get("session") or {}
    return bool(s) and s.get("country_name") == cur.get("country_name") and s.get("session_name") == cur.get("session_name")


def circuit_file():
    """Saved outline for the current circuit: circuits/<circuit-short-name>.json, e.g. circuits/kuala-lumpur.json."""
    name = ((state.get("session") or {}).get("circuit_short_name") or "").strip().lower().replace(" ", "-")
    path = os.path.join("circuits", f"{name}.json")
    if name and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


@app.get("/api/laps")
async def api_laps(session_key: int | None = None):
    data = await asyncio.to_thread(db.laps, session_key or _raw["key"])
    if os.getenv("SOURCE") == "f1stream" and snap_matches():  # stream keeps no lap history: start from the snapshot
        merged: dict = {}
        for l in snap_file().get("laps", []):
            if l.get("lap_duration"):
                merged.setdefault(int(l["driver_number"]), {})[l["lap_number"]] = l["lap_duration"]
        for d, ls in data.items():  # then add any laps the stream recorded that the snapshot lacks
            for lap, dur in ls:
                merged.setdefault(int(d), {}).setdefault(lap, dur)
        data = {d: [[k, v] for k, v in sorted(laps.items())] for d, laps in merged.items()}
    return data


@app.get("/api/outline")
async def api_outline():
    out = _raw.get("outline") or []
    if len(out) < 400:
        out = circuit_file() or out
    if len(out) < 400 and os.getenv("SOURCE") == "f1stream" and snap_matches():  # same session: reuse its circuit outline
        out = [[p["x"], p["y"]] for p in snap_file().get("location", [])]
    return out


@app.get("/api/sessions")
async def api_sessions():
    return await asyncio.to_thread(db.sessions)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        await websocket.send_json(state)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)


@app.get("/")
async def index():
    return FileResponse("static/index.html")