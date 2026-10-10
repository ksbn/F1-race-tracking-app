import asyncio, base64, json, logging, time, urllib.parse, zlib

import httpx

BASE = "https://livetiming.formula1.com/signalrcore"
HEADERS = {"User-Agent": "BestHTTP", "Accept-Encoding": "gzip,identity"}
TOPICS = ["Heartbeat", "WeatherData", "TrackStatus", "SessionStatus", "DriverList", "RaceControlMessages",
          "SessionInfo", "LapCount", "TimingData", "TimingAppData", "Position.z"]
log = logging.getLogger("f1stream")


def norm(v):  # lists become {"0": ..} so partial updates (keyed by index) can be merged
    if isinstance(v, list):
        return {str(i): norm(x) for i, x in enumerate(v)}
    return {k: norm(x) for k, x in v.items()} if isinstance(v, dict) else v


def merge(dst, src):
    for k, v in src.items():
        if k == "_deleted":
            continue
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v


def secs(s):
    try:
        m, _, x = str(s).rpartition(":")
        return (int(m) * 60 if m else 0) + float(x)
    except ValueError:
        return None


def num(s):  # "+1.234" -> 1.234 ; "1L" stays text ; "" -> None
    if s in (None, ""):
        return None
    try:
        return float(str(s).lstrip("+"))
    except ValueError:
        return str(s)


class Feed:
    def __init__(self):
        self.s, self.cars, self.outline, self.laps, self._ref = {}, {}, [], {}, None
        self.cur_key = None
        self._seen = {}
        self.seen, self.sample, self.err = {}, None, None  # diagnostics: topics received, a Position.z sample, last decode error

    def apply(self, topic, data):
        self.seen[topic] = self.seen.get(topic, 0) + 1
        if topic.endswith(".z"):
            try:
                data = json.loads(zlib.decompress(base64.b64decode(data), -zlib.MAX_WBITS))
            except Exception as e:  # a bad frame must not drop the whole connection
                self.err = f"{topic}: {type(e).__name__}: {e}"
                return
            if self.sample is None:
                self.sample = json.dumps(data)[:400]
        data = norm(data)
        if topic == "SessionInfo" and data.get("Key") not in (None, self.cur_key):
            if self.cur_key is not None:  # a new session started: drop laps, cars and outline of the previous one
                self.laps, self.cars, self.outline, self._ref = {}, {}, [], None
                self._seen = {}
                for l in self.s.get("TimingData", {}).get("Lines", {}).values():  # stale lap data of the old session
                    for k in ("LastLapTime", "BestLapTime", "BestLapTimes", "NumberOfLaps"):
                        l.pop(k, None)
                for l in self.s.get("TimingAppData", {}).get("Lines", {}).values():
                    l.pop("Stints", None)
            self.cur_key = data["Key"]
        if topic == "Position.z":
            for frame in data.get("Position", {}).values():
                for n, e in frame.get("Entries", {}).items():
                    if e.get("Status") == "OnTrack" and "X" in e:
                        self.cars[n] = [e["X"], e["Y"]]
                        self._ref = self._ref or n  # trace one car to draw the circuit outline
                        if n == self._ref and len(self.outline) < 400:
                            self.outline.append([e["X"], e["Y"]])
        elif isinstance(data, dict):
            merge(self.s.setdefault(topic, {}), data)

    def rows(self):
        drv, app = self.s.get("DriverList", {}), self.s.get("TimingAppData", {}).get("Lines", {})
        out = []
        for n, l in self.s.get("TimingData", {}).get("Lines", {}).items():
            try:
                pos = int(l.get("Position") or 0)
            except ValueError:
                pos = 0
            if not pos:
                continue
            d, stints = drv.get(n, {}), app.get(n, {}).get("Stints", {})
            st = stints[max(stints, key=int)] if stints else {}
            last = secs((l.get("LastLapTime") or {}).get("Value"))
            if last and l.get("NumberOfLaps"):
                mine = self.laps.setdefault(int(n), {})
                if self.s.get("SessionInfo", {}).get("Type") not in ("Practice", "Qualifying"):  # races: F1's own lap counter
                    mine[int(l["NumberOfLaps"])] = last
                elif self._seen.get(n) != (l["NumberOfLaps"], last):  # practice/qualifying: number the timed laps 1, 2, 3...
                    self._seen[n] = (l["NumberOfLaps"], last)
                    mine[len(mine) + 1] = last
            out.append({"pos": pos, "num": int(n), "code": d.get("Tla"), "name": d.get("FullName"), "team": d.get("TeamName"),
                        "colour": "#" + (d.get("TeamColour") or "888888"), "gap": num(l.get("GapToLeader") or l.get("TimeDiffToFastest")),
                        "interval": num((l.get("IntervalToPositionAhead") or {}).get("Value") or l.get("TimeDiffToPositionAhead")),
                        "lap": l.get("NumberOfLaps"), "last": last, "best": secs((l.get("BestLapTime") or {}).get("Value")),
                        "tyre": st.get("Compound"), "tyre_age": st.get("TotalLaps")})
        return sorted(out, key=lambda r: r["pos"])

    def messages(self):
        m = self.s.get("RaceControlMessages", {}).get("Messages", {})
        return [{"date": v.get("Utc"), "flag": v.get("Flag"), "category": v.get("Category"), "message": v.get("Message")}
                for _, v in sorted(m.items(), key=lambda kv: int(kv[0]))][-40:]

    def weather(self):
        w = self.s.get("WeatherData")
        return {"air_temperature": w.get("AirTemp"), "track_temperature": w.get("TrackTemp"),
                "rainfall": int(float(w.get("Rainfall") or 0))} if w else None

    def session(self):
        i = self.s.get("SessionInfo", {})
        mt = i.get("Meeting", {})
        return {"session_name": i.get("Name"), "session_type": i.get("Type"), "date_start": None, "date_end": None,
                "circuit_short_name": mt.get("Circuit", {}).get("ShortName"), "country_name": mt.get("Country", {}).get("Name")}

    def live(self):  # label for the page badge, from F1's own session status
        st = self.s.get("SessionStatus", {}).get("Status")
        return {"Started": "Live", "Finished": "Finished", "Finalized": "Finished", "Ends": "Finished",
                "Inactive": "Waiting for session", "Aborted": "Suspended"}.get(st)

    def key(self):
        return int(self.s.get("SessionInfo", {}).get("Key") or 0)

    def lap_rows(self):
        return {n: [{"lap_number": k, "lap_duration": v} for k, v in sorted(ls.items())] for n, ls in self.laps.items()}


async def stream(feed, push):
    import websockets  # imported here so the rest of the app runs without it
    delay = 2
    while True:
        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=15) as c:
                r = await c.post(f"{BASE}/negotiate", params={"negotiateVersion": 1})
                r.raise_for_status()
                url = f"wss://livetiming.formula1.com/signalrcore?id={urllib.parse.quote(r.json()['connectionToken'])}"
                cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
            async with websockets.connect(url, additional_headers={**HEADERS, "Cookie": cookie}) as ws:
                await ws.send(json.dumps({"protocol": "json", "version": 1}) + "\x1e")
                await ws.recv()
                await ws.send(json.dumps({"arguments": [TOPICS], "invocationId": "1", "target": "Subscribe", "type": 1}) + "\x1e")
                log.info("subscribed to F1 live timing")
                delay, last, started = 2, 0, time.time()
                async for raw in ws:
                    if time.time() - started > 600:  # reconnect every 10 minutes to pick up a new session
                        break
                    force = False
                    text = raw.decode() if isinstance(raw, bytes) else raw  # frames may arrive as bytes or text
                    for part in filter(None, text.split("\x1e")):
                        m = json.loads(part)
                        if m.get("type") == 6:
                            await ws.send('{"type":6}\x1e')
                        elif m.get("type") == 3 and isinstance(m.get("result"), dict):
                            for topic, data in m["result"].items():
                                feed.apply(topic, data)
                            force = True
                        elif m.get("type") == 1 and m.get("target") == "feed":
                            feed.apply(m["arguments"][0], m["arguments"][1])
                    if force or time.time() - last >= 1:  # at most one browser update per second
                        last = time.time()
                        await push()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("F1 stream problem (%s: %s); retrying in %ss", type(e).__name__, e, delay)
            await push(status=f"F1 live stream unavailable ({type(e).__name__}); retrying")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def run(state, raw, broadcast, db, extra_outline=None):
    feed, saved = Feed(), 0

    async def push(status=None):
        nonlocal saved
        rows = feed.rows()
        state.update(rows=rows, cars=feed.cars, messages=feed.messages(), weather=feed.weather(), session=feed.session(),
                     replay=False, fallback=False, live=feed.live(), debug={"topics": feed.seen, "pos_sample": feed.sample, "error": feed.err}, has_outline=len(feed.outline) >= 400 or bool(extra_outline and extra_outline()), updated=time.strftime("%H:%M:%S"),
                     status=status or ("ok" if rows else "Connected to F1 live timing; waiting for a session"))
        raw.update(outline=feed.outline, key=feed.key() or None)
        if raw["key"] and time.time() - saved > 30 and feed.laps:
            saved = time.time()
            await asyncio.to_thread(db.save_laps, raw["key"], feed.lap_rows())
        await broadcast()

    state["status"] = "Connecting to F1 live timing..."
    await stream(feed, push)