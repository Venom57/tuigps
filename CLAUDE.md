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
  - `formatting.py` — NaN-safe formatting and unit-conversion helpers
  - `config.py` — persistent settings (`Config` dataclass, JSON load/save, validators)
  - `screens/` — modal screens (settings, help, `ConfirmScreen` for destructive actions)
  - `widgets/` — individual display panels

## Key Patterns
- All GPS data flows through a single `GPSData` dataclass
- Widgets implement `update_gps_data(data: GPSData)` and override `render() -> Text`
- Only the active tab's widgets (`TAB_WIDGETS` in `app.py`, references cached after mount) plus the status bar are refreshed; a tab is refreshed when activated. Widgets skip `refresh()` when handed the snapshot they already show (`data is self._data`), so display settings must be reactive (or call `refresh()`) to repaint
- NaN values are used for unavailable data; always check `math.isfinite()` before display
- The gpsd client runs in a daemon thread with auto-reconnect (exponential backoff 2s → 30s); `GPSData.connection_state` is "connecting" | "connected" | "disconnected", and `on_error` fires once per outage
- The client mutates a private working `GPSData` and publishes shallow-copy snapshots to `on_update`; it always replaces nested objects/lists, never mutates them in place, so snapshots never change after publication
- UI refreshes are coalesced to at most `UI_REFRESH_HZ` (10 Hz) plus a 1 s heartbeat; clock sync/hold/logging run on the gpsd thread on each new TPV epoch (`tpv_seq`)
- Tests never touch a real gpsd: the default port is patched to a refusing port, and `tests/fake_gpsd.py` provides a scriptable fake gpsd TCP server
- Settings persist in `$XDG_CONFIG_HOME/tuigps/config.json` (`--config PATH` overrides). `config.load()` never raises (bad fields fall back to defaults with a warning); `config.save()` is atomic. The app saves on Settings Apply and on the `t`/`d`/`u` keys. CLI args and `GPSD_HOST`/`GPSD_PORT` override the saved host/port for one session and are not written back unless changed in Settings. Tests get a temp `XDG_CONFIG_HOME` from an autouse fixture
- `TuiGPS.BINDINGS` is the single source of truth for keys: the status bar hints (`show=True`, description = short label) and the help screen (every binding, tooltip = help text) are generated from it. App keys are inert while an `Input` has focus or a modal is open
- Status bar space priority: GPS status (always whole) > REC/HOLD badges > key hints (dropped from the end, `? Help` stands in)
- Dashboard layout is responsive: `TuiGPS._layout_dashboard` sets `-wide`/`-medium`/`-narrow` (3/2/1 columns) on `#dashboard-grid` on resize and reorders panels per `DASHBOARD_ORDER`; text rows auto-size to content, the sky plot row takes the rest, and the dashboard scrolls when too small
- Staleness: `data_model.staleness()` (threshold `constants.STALE_AFTER`) is the one verdict; `_update_staleness` (every refresh + heartbeat) dims all `TAB_WIDGETS` panels (`stale` class) and puts `[STALE 23s]`/`[DISCONNECTED]` in their border title
- Destructive Device-tab actions go through `DeviceConfig._confirm` (texts in `CONFIRMATIONS`). Setting the clock disables NTP; `report_clock_result` adds the re-enable hint. NTP helpers live in `clock_sync` (`set_ntp`, `ntp_enabled`), run off the UI thread via `asyncio.to_thread` workers
- Tests: `subprocess.run` is refused by an autouse fixture (mock it), and `clock_sync.ntp_enabled` answers None unless patched

## Commands
- `pip install -e .[dev]` — install in dev mode (with test dependencies)
- `pytest` — run the tests in parallel (pytest-xdist, `-n auto` in pyproject); `pytest -n0` for one serial process
- `tuigps [host[:port]] [--host H] [--port P]` — run the app (defaults: `$GPSD_HOST`/`$GPSD_PORT`, else 127.0.0.1:2947)
- `python -m tuigps` — alternative run method

## Testing with simulated GPS
```bash
gpsfake -c 0.5 /usr/share/gpsd/sample.nmea
```

## Git Commits
- Do NOT include `Co-Authored-By` lines in commit messages
