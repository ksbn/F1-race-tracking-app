import asyncio, os, time
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

OPENF1 = "https://api.openf1.org/v1"
JOLPICA = "https://api.jolpi.ca/ergast/f1"
TOKEN = os.getenv("OPENF1_TOKEN")  # optional: paid OpenF1 token enables real-time
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "4"))

state = {"session": None, "drivers": {}, "rows": [], "status": "starting", "updated": None}
_raw = {"pos": {}, "gap": {}, "laps": {}, "stints": {}, "since_pos": None, "since_gap": None, "key": None}
clients: set[WebSocket] = set()
_cache: dict = {}


async def get(client, path, **params):
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    r = await client.get(f"{OPENF1}/{path}", params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


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
                (max((l["lap_number"] for l in laps), default=0) - stint["lap_start"]) if stint else 0) if stint else None,
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


async def poll_loop():
    last_slow = 0
    async with httpx.AsyncClient() as client:
        while True:
            try:
                sess = (await get(client, "sessions", session_key="latest"))[-1]
                if sess["session_key"] != _raw["key"]:  # new session: reset everything
                    _raw.update(pos={}, gap={}, laps={}, stints={}, since_pos=None, since_gap=None,
                                key=sess["session_key"])
                    drivers = await get(client, "drivers", session_key="latest")
                    state["drivers"] = {d["driver_number"]: d for d in drivers}
                    last_slow = 0
                state["session"] = {k: sess.get(k) for k in
                                    ("session_name", "session_type", "circuit_short_name", "country_name", "date_start", "date_end")}

                kw = {"session_key": "latest"}
                positions = await get(client, "position", **kw, **({"date>": _raw["since_pos"]} if _raw["since_pos"] else {}))
                for p in positions:
                    _raw["pos"][p["driver_number"]] = p["position"]
                    _raw["since_pos"] = max(_raw["since_pos"] or "", p["date"])

                gaps = await get(client, "intervals", **kw, **({"date>": _raw["since_gap"]} if _raw["since_gap"] else {}))
                for g in gaps:
                    _raw["gap"][g["driver_number"]] = g
                    _raw["since_gap"] = max(_raw["since_gap"] or "", g["date"])

                if time.time() - last_slow > 15:  # laps + tyres change slowly
                    last_slow = time.time()
                    by_driver: dict = {}
                    for l in await get(client, "laps", **kw):
                        by_driver.setdefault(l["driver_number"], []).append(l)
                    _raw["laps"] = {k: sorted(v, key=lambda l: l["lap_number"]) for k, v in by_driver.items()}
                    for s in await get(client, "stints", **kw):
                        cur = _raw["stints"].get(s["driver_number"])
                        if not cur or s["stint_number"] >= cur["stint_number"]:
                            _raw["stints"][s["driver_number"]] = s

                state["rows"] = build_rows()
                state["status"] = "ok" if state["rows"] else "No timing data for this session yet"
                state["updated"] = time.strftime("%H:%M:%S")
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                state["status"] = ("Live data needs an OpenF1 token (set OPENF1_TOKEN). Showing last known data."
                                   if code in (401, 402, 403) else f"OpenF1 error {code}; retrying")
            except Exception as e:  # network blips must never kill the loop
                state["status"] = f"Connection problem ({type(e).__name__}); retrying"
            await broadcast()
            await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def lifespan(app):
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
