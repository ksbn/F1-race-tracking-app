# F1Tracker

A full-stack Formula 1 race tracker in Python. It shows timing, a track map, race control messages, weather, lap-time charts, standings and the calendar. It has three data sources, so it keeps working when one of them is locked.

Demo: https://f1-race-tracking-app.onrender.com

> The demo runs on a free instance that sleeps when idle, so the first load can take up to a minute. A badge in the header says **Live**, **Finished** or **Waiting for session**, because outside a session the page shows the last state F1 sent.

## Features

- **Timing tower:** position, gap, interval, last and best lap, tyre compound and age
- **Track map:** cars on a circuit outline built from live positions
- **Race control feed and weather:** flags, safety cars, penalties, temperatures, rain
- **Lap-time chart:** one line per driver, stored in SQLite
- **Standings and calendar:** current season, from Jolpica-F1
- **Status badge:** shows whether the session is live, finished, waiting or a replay
- **Replay mode:** play back a finished race from OpenF1 or a bundled snapshot

## Data sources

| Source | How to enable | Notes |
|---|---|---|
| F1 live timing stream (**experimental**) | `SOURCE=f1stream` | Free, no key. Unofficial and undocumented. It can break without notice, and F1's terms of use for a public site have not been checked. |
| OpenF1 | default | Historical data is free. Live data needs a paid account. While a session is live, the free API is locked. |
| Snapshot replay | `USE_SNAPSHOT=1` or automatic fallback | Replays the bundled `snapshot.json.gz`. No network needed. |

Standings and the calendar come from [Jolpica-F1](https://github.com/jolpica/jolpica-f1), which needs no key.

## How it works

A background task reads the chosen source and keeps one shared in-memory state. It pushes that state to every browser over `/ws`. Laps are saved to SQLite for the charts.

With the default source, if OpenF1 refuses access, the app replays the latest completed race from OpenF1, and if that is refused too, the bundled snapshot. Once the app falls back, it stays on the replay until it restarts. `SOURCE=f1stream` has no fallback.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
SOURCE=f1stream uvicorn main:app
```

Open http://localhost:8000. Without `SOURCE`, the app uses OpenF1 (`POLL_SECONDS=20` avoids rate limits).

### Replay a past session

```bash
curl -s "https://api.openf1.org/v1/sessions?year=2025&session_name=Race"
REPLAY_SESSION=<session_key> REPLAY_SPEED=5 uvicorn main:app
```

## Snapshots

```bash
python3 snapshot.py              # from OpenF1: tyres, weather, race control, track outline
python3 snapshot_jolpica.py      # from Jolpica: positions, lap times, derived gaps
```

Both write `snapshot.json.gz` and overwrite the existing one. `snapshot.py` only works while OpenF1 allows access. On macOS with a python.org install, run `Install Certificates.command` once if you get an SSL certificate error.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SOURCE` | none | `f1stream` uses F1's live timing stream instead of OpenF1 |
| `USE_SNAPSHOT` | none | Replay the bundled snapshot without calling OpenF1 |
| `REPLAY_SESSION` | `latest` | Force replay of a specific OpenF1 session |
| `REPLAY_SPEED` | `5` | Replay speed multiplier |
| `REPLAY_FINAL` | none | Freeze the replay at the end of the race |
| `POLL_SECONDS` | `4` | Seconds between OpenF1 polls. Use 20 or more to avoid 429 errors. |
| `OPENF1_TOKEN` | none | Optional OpenF1 token for real-time data |
| `DB_PATH` | `f1.db` | SQLite file location. Falls back to `/tmp` if not writable. |

## API

| Endpoint | Description |
|---|---|
| `GET /health` | Status of the data source |
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

## Deploy

The repo includes a `Dockerfile` that reads the `PORT` variable, so it runs on Render, Railway and similar hosts.

**Render (free tier):**
1. New Web Service, connect the repo, language **Docker**, instance type **Free**.
2. Health check path `/health`.
3. Environment variable `SOURCE=f1stream`.

Keep a single instance. State lives in memory, and free instances have no disk, so lap history resets on every restart.

## Limitations

- The F1 live stream is unofficial and untested during a live race weekend. Its field names follow open-source projects and may need fixes.
- Real-time OpenF1 data needs a paid account, and its free API locks while a session is live.
- Snapshots from Jolpica have no tyres, weather, race control or track map, and their gaps are derived from lap times.
- The replay clock is shared by all viewers and restarts when the app restarts.
- Single process only. Scaling out needs shared state, for example Redis pub/sub.

## License

MIT. See [LICENSE](LICENSE).
