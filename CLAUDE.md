# tuigps

Terminal UI GPS monitoring tool using gpsd.

## Tech Stack
- **Python 3.10+** with **Textual** (TUI framework) and **Rich** (text rendering)
- **python3-gps** system package (apt) for gpsd connection — not pip-installable
- Threaded gpsd client (`gpsd_client.py`) communicates with Textual via `App.call_from_thread()`

## Project Structure
- `src/tuigps/` — main package
  - `app.py` — Textual App entry point
  - `app.tcss` — CSS layout
  - `gpsd_client.py` — threaded gpsd connection
  - `data_model.py` — dataclasses for GPS state
  - `constants.py` — GNSS names, colors, status maps
  - `formatting.py` — NaN-safe formatting helpers
  - `screens/` — Textual screens (main dashboard, settings modal)
  - `widgets/` — individual display panels

## Key Patterns
- All GPS data flows through a single `GPSData` dataclass
- Widgets implement `update_gps_data(data: GPSData)` and override `render() -> Text`
- NaN values are used for unavailable data; always check `math.isfinite()` before display
- The gpsd client runs in a daemon thread with auto-reconnect (2s delay)
- `sys.path` manipulation may be needed to access system `python3-gps` from a venv

## Commands
- `pip install -e .` — install in dev mode
- `tuigps` — run the app
- `python -m tuigps` — alternative run method

## Testing with simulated GPS
```bash
gpsfake -c 0.5 /usr/share/gpsd/sample.nmea
```

## Git Commits
- Do NOT include `Co-Authored-By` lines in commit messages
