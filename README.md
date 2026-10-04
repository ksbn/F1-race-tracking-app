# F1 Tracker

A full-stack Formula 1 race tracker in Python. It shows timing, a track map, race control messages, weather, lap-time charts, standings and the calendar. When live data is unavailable, it replays a real race instead.

Demo: https://f1-race-tracking-app-production.up.railway.app

> **Note:** the demo may show a replay of a past race rather than live data. The header says "Replay of latest race (live feed unavailable)" when that happens. The hosting plan may also expire, so the link can go offline.

## Features

- **Timing tower:** position, gap, interval, last and best lap, tyre compound and age
- **Track map:** cars moving on the circuit outline (needs OpenF1 data)
- **Race control feed and weather:** flags, safety cars, penalties, temperatures, rain (needs OpenF1 data)
- **Lap-time chart:** one line per driver, stored in SQLite
- **Standings and calendar:** current season
- **Replay mode:** play back any finished session at adjustable speed
- **Automatic fallback:** if live data is blocked, the app replays the latest race, or a bundled snapshot if the API is unreachable

## Stack

- **Backend:** FastAPI, httpx, WebSockets, SQLite
- **Frontend:** a single HTML page with vanilla JS and inline SVG
- **Data:** [OpenF1](https://openf1.org) for timing data, [Jolpica-F1](https://github.com/jolpica/jolpica-f1) for standings, the calendar and snapshots
- **Deploy:** Docker, Railway

## How it works

A background task polls OpenF1 and fetches only records newer than the last one seen. It keeps one shared in-memory state and pushes it to every browser over `/ws`. Laps are saved to SQLite for the charts.

OpenF1 restricts all access, including past sessions, to authenticated users while a live F1 session is running, and live data needs a paid account. The app handles this with a fallback chain:

1. Live data from OpenF1
2. If access is refused: replay the latest completed race fetched from OpenF1
3. If that is refused too: replay the bundled `snapshot.json.gz`

Once the app falls back, it stays on the replay until it is restarted.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
POLL_SECONDS=20 uvicorn main:app
```

Open http://localhost:8000.

### Replay a past session

Find a session key, then start in replay mode:

```bash
curl -s "https://api.openf1.org/v1/sessions?year=2025&session_name=Race"
REPLAY_SESSION=<session_key> REPLAY_SPEED=5 uvicorn main:app
```

### Docker

```bash
docker compose up --build
```

## Snapshots

The fallback replays `snapshot.json.gz` from the project root. Create it with one of two scripts:

```bash
python3 snapshot.py              # from OpenF1: tyres, weather, race control, track outline
python3 snapshot_jolpica.py      # from Jolpica: positions, lap times, derived gaps
```

`snapshot.py` only works while OpenF1 allows access. `snapshot_jolpica.py` has no live-session lockouts, but it has no tyres, weather, race control messages or track outline. Both accept a specific race (`python3 snapshot_jolpica.py 2026 15`) or default to the latest one.

On macOS with a python.org install, run `Install Certificates.command` once if you get an SSL certificate error.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `POLL_SECONDS` | `4` | Seconds between polls. Use 20 or more to avoid 429 errors. |
| `OPENF1_TOKEN` | none | Optional OpenF1 token for real-time data |
| `REPLAY_SESSION` | `latest` | A session key forces replay of that OpenF1 session |
| `REPLAY_SPEED` | `5` | Replay speed multiplier |
| `DB_PATH` | `f1.db` | SQLite file location. Falls back to `/tmp` if not writable. |

## API

| Endpoint | Description |
|---|---|
| `GET /health` | Status of the poller |
| `GET /api/state` | Current timing state |
| `GET /api/laps` | Stored lap times for a session |
| `GET /api/outline` | Track outline points |
| `GET /api/sessions` | Stored sessions |
| `GET /api/standings` | Driver standings |
| `GET /api/schedule` | Season calendar |
| `WS /ws` | Live state stream |

## Tests

```bash
pip install pytest
pytest tests
```

## Limitations

- Real-time OpenF1 data needs a paid account. During live sessions the free API is locked, so the app shows a replay.
- Snapshots from Jolpica have no tyres, weather, race control or track map, and their gaps are derived from lap times.
- The replay clock is shared by all viewers and restarts from the race start whenever the app restarts.
- Single process only. Scaling out needs shared state, for example Redis pub/sub.
- Race control messages and weather are not persisted.

## License

MIT. See [LICENSE](LICENSE).
