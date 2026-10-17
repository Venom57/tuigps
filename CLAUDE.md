# tuigps

Terminal UI GPS monitoring tool using gpsd.

## Tech Stack
- **Python 3.10+** with **Textual** (TUI framework) and **Rich** (text rendering)
- Native gpsd JSON protocol client (`gpsd_client.py`, stdlib sockets only — no python3-gps dependency)
- Threaded gpsd client hands data to Textual without blocking: the app stores the latest snapshot, queues NMEA, and schedules a coalesced UI tick via `loop.call_soon_threadsafe` (never `App.call_from_thread`, which waits for the UI)

## Project Structure
- `src/tuigps/` — main package
  - `app.py` — Textual App
  - `cli.py` — command-line entry point (`tuigps`, `python -m tuigps`)
  - `app.tcss` — CSS layout
  - `gpsd_client.py` — threaded native gpsd client (TCP, `?WATCH`, JSON + raw NMEA)
  - `data_model.py` — dataclasses for GPS state
  - `constants.py` — GNSS names, colors, status maps
  - `formatting.py` — NaN-safe formatting helpers
  - `screens/` — Textual screens (main dashboard, settings modal)
  - `widgets/` — individual display panels

## Key Patterns
- All GPS data flows through a single `GPSData` dataclass
- Widgets implement `update_gps_data(data: GPSData)` and override `render() -> Text`
- NaN values are used for unavailable data; always check `math.isfinite()` before display
- The gpsd client runs in a daemon thread with auto-reconnect (exponential backoff 2s → 30s); `GPSData.connection_state` is "connecting" | "connected" | "disconnected", and `on_error` fires once per outage
- The client mutates a private working `GPSData` and publishes shallow-copy snapshots to `on_update`; it always replaces nested objects/lists, never mutates them in place, so snapshots never change after publication
- UI refreshes are coalesced to at most `UI_REFRESH_HZ` (10 Hz) plus a 1 s heartbeat; clock sync/hold/logging run on the gpsd thread on each new TPV epoch (`tpv_seq`)
- Tests never touch a real gpsd: the default port is patched to a refusing port, and `tests/fake_gpsd.py` provides a scriptable fake gpsd TCP server

## Commands
- `pip install -e .` — install in dev mode
- `tuigps [host[:port]] [--host H] [--port P]` — run the app (defaults: `$GPSD_HOST`/`$GPSD_PORT`, else 127.0.0.1:2947)
- `python -m tuigps` — alternative run method

## Testing with simulated GPS
```bash
gpsfake -c 0.5 /usr/share/gpsd/sample.nmea
```

## Git Commits
- Do NOT include `Co-Authored-By` lines in commit messages
