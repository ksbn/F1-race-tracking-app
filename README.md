# F1 Live Tracker

A full-stack Formula 1 race tracker in Python. It shows live timing, a track map, race control messages, weather, lap-time charts, standings and the calendar.

Live demo: https://f1-race-tracking-app.fly.dev

## Features

- **Timing tower:** position, gap, interval, last and best lap, tyre compound and age
- **Track map:** cars moving on the circuit outline
- **Race control feed and weather:** flags, safety cars, penalties, temperatures, rain
- **Lap-time chart:** one line per driver, stored in SQLite
- **Standings and calendar:** current season
- **Replay mode:** play back any finished session at adjustable speed

## Stack

- **Backend:** FastAPI, httpx, WebSockets, SQLite
- **Frontend:** a single HTML page with vanilla JS and inline SVG
- **Data:** [OpenF1](https://openf1.org) for timing data, [Jolpica-F1](https://github.com/jolpica/jolpica-f1) for standings and the calendar. Neither needs a key for historical data.
- **Deploy:** Docker, Fly.io

## How it works

A background task polls OpenF1 and fetches only records newer than the last one seen. It keeps one shared in-memory state and pushes it to every browser over `/ws`. Laps are saved to SQLite for the charts.

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
curl -s "https://api.openf1.org/v1/sessions?year=2024&session_name=Race"
REPLAY_SESSION=<session_key> REPLAY_SPEED=5 uvicorn main:app
```

### Docker

```bash
docker compose up --build
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `POLL_SECONDS` | `4` | Seconds between polls. Use 20 or more on the free OpenF1 tier to avoid 429 errors. |
| `OPENF1_TOKEN` | none | Optional OpenF1 token for real-time data |
| `REPLAY_SESSION` | `latest` | A session key enables replay mode |
| `REPLAY_SPEED` | `5` | Replay speed multiplier |
| `DB_PATH` | `f1.db` | SQLite file location |

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

## Deploy to Fly.io

```bash
fly volumes create f1data --size 1 --region cdg
fly deploy --ha=false
```

Keep a single instance. State lives in memory, so a second instance would double the request rate to OpenF1.

## Limitations

- Real-time OpenF1 data may require a paid token. Without one, the app shows the most recent session it can read.
- Single process only. Scaling out needs shared state, for example Redis pub/sub.
- Race control messages and weather are not persisted.

## License

MIT
