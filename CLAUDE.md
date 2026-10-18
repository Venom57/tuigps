# tuigps

Terminal UI GPS monitoring tool for gpsd.

## Tech Stack
- **Python 3.10+** with **Textual** (TUI framework) and **Rich** (text rendering); no other runtime dependencies
- **Native gpsd client** (`gpsd_client.py`, stdlib sockets + JSON): no `python3-gps`, fully pip-installable
- Optional external tools: `ubxtool`/`gpsctl`/`gpsfake` (gpsd-clients), `timedatectl`/`busctl` (systemd), `/dev/pps*`

## Project Structure
- `src/tuigps/`
  - `__init__.py` — `__version__`; library logging (NullHandler, never prints over the TUI)
  - `__main__.py` — `python -m tuigps` → `cli.main()`
  - `cli.py` — argparse entry point (`tuigps [host[:port]] --host --port --config --version`), `parse_server`, server precedence (CLI > GPSD_HOST/GPSD_PORT > config > default)
  - `config.py` — `Config` dataclass, `$XDG_CONFIG_HOME/tuigps/config.json` load (never fails; per-field defaults + `load_warnings`) / atomic save; validators `check_host`, `check_port`, `check_log_dir` shared with CLI and settings
  - `app.py` — `TuiGPS` App: compose, `BINDINGS` (single source for status-bar hints and help), cross-thread delivery and coalesced UI ticks, active-tab refresh, staleness tagging, responsive dashboard layout, actions
  - `app.tcss` — layout CSS (dashboard grid `-wide`/`-medium`/`-narrow`, `.stale`)
  - `gpsd_client.py` — `GPSDClient`: reader thread, `?WATCH`, JSON/NMEA line parsing, snapshot publishing, connection state, reconnect backoff (2 s → 30 s), TOFF computation and armed TOFF
  - `data_model.py` — `GPSData` and nested dataclasses; `staleness()`
  - `constants.py` — GNSS ids/names/keys/colors, mode/status maps, DOP ratings, `STALE_AFTER`
  - `formatting.py` — NaN-safe formatting, units (metric/imperial/nautical), coordinate formats
  - `timeutil.py` — `parse_gps_time()` (ISO 8601 with any fraction length, leap second)
  - `clock_sync.py` — set clock from GPS (busctl → timedatectl → sudo date), `run_command`, NTP `set_ntp` / `ntp_enabled` / `disable_ntp` / `restore_ntp`
  - `gps_logger.py` — GPX 1.1 / CSV logging (thread-safe; never overwrites)
  - `position_hold.py` — Welford averaging, std dev, CEP50/95
  - `screens/` — `settings_screen.py` (validated settings modal), `help_screen.py` (keys + tabs, generated from BINDINGS), `confirm_screen.py` (Cancel-focused confirmation modal)
  - `widgets/` — display panels (`position_panel`, `fix_panel`, `velocity_panel`, `sky_plot`, `signal_chart`, `error_panel`, `device_panel`, `time_panel`, `constellation_panel`, `satellite_table`), `connection_status` (status bar), `nmea_viewer`, `device_config` (ubxtool, clock sync, PPS ioctl, NTP)
- `tests/` — pytest suite; `conftest.py` (safety fixtures, `make_gps_data`), `fake_gpsd.py` (scriptable fake gpsd server)
- `docs/` — user docs per tab, `configuration.md`, `development.md`, `IMPROVEMENT_PLAN.md` (status + hardware checklist), `screenshots/` (real-hardware captures; do not replace with synthetic data)

## Key Patterns
- All GPS data flows through one `GPSData` dataclass. The client publishes **immutable snapshots** (shallow copies; nested objects and lists are always replaced, never mutated) — keep it that way in `_apply_*`.
- Threading: the gpsd reader thread calls `TuiGPS._on_gpsd_update` / `_on_nmea`, which store the snapshot / queue NMEA and schedule one UI tick via `loop.call_soon_threadsafe` — **never `call_from_thread`** (it blocks the reader). Ticks are rate-limited to `UI_REFRESH_HZ` (10 Hz); a 1 s heartbeat handles staleness.
- Per-fix work (armed clock set, logging, position hold) runs only on a new TPV epoch (`GPSData.tpv_seq` changed), never on every message. Fix age uses `tpv_received`, not `last_seen`.
- Only widgets on the active tab are refreshed (`TAB_WIDGETS`); `DeviceConfig.update_gps_data` always runs (it does not render).
- Widgets implement `update_gps_data(data)` (return early if `data is self._data`) and `render() -> Text`; display settings are reactives (`units`, `coord_format`, `hidden_gnss`) that repaint on change.
- NaN means unavailable; check `math.isfinite()` before display. Use `formatting.py` helpers for units.
- Key bindings live only in `TuiGPS.BINDINGS` (`show=True` → status bar hint; `tooltip` → help text).
- Destructive device/clock actions go through `DeviceConfig._confirm()` with text in `CONFIRMATIONS`. Clock setting disables NTP first and restores it if setting fails.
- Background commands use worker threads (`threading.Thread` / `asyncio.to_thread`) and report back to the log; never block the event loop on `subprocess`.
- Errors: no bare `except: pass`; log (`log.exception` / `self.log`) or handle a specific exception. `_ui_guard` contains per-widget failures.
- Source must stay Python 3.10 compatible (no backslashes or reused quotes inside f-string expressions; `tests/test_compat.py` checks).

## Commands
- `pip install -e '.[dev]'` — dev install (pytest, pytest-asyncio, pytest-cov, **pytest-xdist** — required, since `addopts = "-n auto"`)
- `pytest` — full suite in parallel (~20 s); `pytest -n0` — serial (pdb, `-s`)
- `pytest --cov --cov-branch --cov-report=term-missing --cov-report=html` — coverage (HTML in `htmlcov/`)
- `tuigps [host[:port]]` / `python -m tuigps` — run the app

## Testing
- Tests must never touch real gpsd, commands, `/dev/pps*`, the browser or `~/.config`: conftest autouse fixtures enforce this (`subprocess.run` raises, default gpsd port refuses, `XDG_CONFIG_HOME` is temporary).
- Use `tests/fake_gpsd.py` for end-to-end client/app tests (`@pytest.mark.live_gpsd` runs the real client thread in app tests; otherwise it is idle and tests inject snapshots).
- After injecting updates from a thread, wait with `settle()` before asserting; keep timing assertions relative (suite runs in parallel).

## Testing with simulated GPS
```bash
gpsfake -c 0.5 /usr/share/gpsd/sample.nmea
```

## Git Commits
- Do NOT include `Co-Authored-By` lines in commit messages
