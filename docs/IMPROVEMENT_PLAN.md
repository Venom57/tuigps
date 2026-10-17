# tuigps Improvement Plan

Branch: `improvements/phased-overhaul` (from `master` @ `01c9bab`)

This plan comes from a full read of the codebase (about 3.2k lines of Python and no tests) and a headless smoke run against Textual 8.2.8. The work is split into phases. Each phase is one or more work blocks, and each block is committed only after the full test suite passes.

---

## 1. Analysis summary

### Performance

| # | Finding | Where | Impact |
|---|---------|-------|--------|
| P1 | Every gpsd message, including each raw NMEA sentence, triggers a full `_refresh_ui()` of 14 widgets. At 1 Hz that is about 20–30 full refreshes per second. At 10 Hz nav rate it is several hundred. | `gpsd_client._poll_loop`, `app._on_gpsd_update` | High CPU use, UI lag |
| P2 | `App.call_from_thread()` **blocks** until the UI callback finishes. The gpsd reader thread therefore waits on rendering for every message. When the socket backs up, `_receipt_time` is captured late, which **skews the TOFF / clock sync measurements**. | `app._on_gpsd_update`, `_on_nmea` | Wrong timing results, backpressure |
| P3 | Widgets on hidden tabs (Satellites, Timing, Device, and so on) are re-rendered on every update. | `app._refresh_ui` | Wasted work |
| P4 | `SkyPlot.render()` builds a separate `Style` object and `append()` call for every cell (w×h), and redraws the static rings and crosshairs every frame. | `widgets/sky_plot.py` | Most expensive render |
| P5 | NMEA sentences are sent to the UI one at a time, with one blocking cross-thread call each. | `app._on_nmea` | Thread churn |

### Functionality and correctness

| # | Finding | Where |
|---|---------|-------|
| F1 | **Position hold adds the same fix many times.** `add_fix()` runs on *every* message (SKY, PPS, NMEA, and so on) while a fix exists, so one TPV is counted 10–30×. This distorts the counts, standard deviation and CEP. | `app._on_gpsd_update` |
| F2 | **Fix age is wrong.** `last_seen` is updated by every message class, not only TPV. Clock sync "fix age" compensation and the PPS sync target-second prediction are therefore based on the wrong reference, and PPS sync can choose the wrong second. | `gpsd_client._extract_data`, `clock_sync`, `device_config._pps_sync_clock` |
| F3 | **An exception in the update callback (for example a logger write after `stop()` on the UI thread) is caught by `_run()` as a connection error**, which makes the client drop the gpsd connection and reconnect. | `gpsd_client._run` |
| F4 | **Reconnect starts a second polling thread.** `stop()` doesn't join, and `start()` immediately sets `_running=True` again, so the old thread keeps running and shares `_session`. `action_reconnect` also resets the app's copy of the data but not the client's. | `gpsd_client.stop/start`, `app.action_reconnect` |
| F5 | The shared mutable `GPSData` is mutated on the gpsd thread while widgets read it, and the `data` property's lock protects nothing. | `gpsd_client` |
| F6 | GPX output is not valid GPX 1.1: `<speed>` is not allowed in `trkpt`, and elements must follow the schema order. | `gps_logger._write_gpx_point` |
| F7 | NMEA viewer: **resuming after a pause re-writes the whole 1000-line buffer**, which duplicates lines already shown. Changing the filter doesn't re-filter existing lines. The `#nmea-stats` widget is never updated. | `widgets/nmea_viewer.py` |
| F8 | The settings dialog's **Constellations checkboxes do nothing**. The values are stored but never used. | `screens/settings_screen.py`, `app.py` |
| F9 | The ubxtool command input is split with `str.split()` instead of `shlex.split()`. | `device_config._run_ubxtool` |
| F10 | Unit system isn't applied consistently: climb, geoid separation and error estimates are always metric. | widgets |
| F11 | GPS time parsing is copy-pasted in 3 places. | `gpsd_client`, `clock_sync`, `device_config` |
| F12 | Hold altitude mean divides by the total fix count even when altitude is missing. | `position_hold.add_fix` |
| F13 | The signal chart's height budget counts satellites that are skipped (SNR 0), so rows are wasted. | `widgets/signal_chart.py` |
| F14 | A missing `python3-gps` causes a raw `ImportError` traceback at startup. The package can't be installed from PyPI, which forces the `sys.path` workaround. | `gpsd_client` |
| F15 | Errors are silently swallowed (`except Exception: pass`) about 30 times, which hides bugs. | everywhere |

### Additional findings from Phase 0 testing

| # | Finding | Where |
|---|---------|-------|
| N1 | Backslashes inside f-string expressions are a **SyntaxError on Python 3.10 and 3.11**, so the app can't start on Python versions it claims to support. | `widgets/satellite_table.py` |
| N2 | `PPS_FETCH` is encoded with `sizeof(struct pps_fdata)` instead of the pointer size used by `<linux/pps.h>`, so the ioctl fails with ENOTTY. **"Set Clock (PPS)" can never work.** | `widgets/device_config.py` |
| N3 | `timeout.flags = 0x01` is `PPS_TIME_INVALID`, so the kernel ignores the timeout and waits forever when no pulse arrives. | `widgets/device_config.py` |
| N4 | `fmt_coord` in DMS and DDM formats can display 60 seconds or 60 minutes (`10°59'60.00"N`). | `formatting.py` |
| N5 | The final clock-sync error message labels sudo's stderr as "busctl". | `clock_sync.py`, `device_config.py` |
| N6 | `Select.BLANK` doesn't exist in Textual 8 (the code should use `Select.NULL`). As a result, Set Model, Rate or Power sends `Select.NULL` to ubxtool, and **Set baud with nothing selected crashes the app**. | `widgets/device_config.py` |
| N7 | TOFF computed from TPV messages overwrites real gpsd TOFF messages. Starting a log twice in the same second overwrites the first file. A fix with an empty timestamp is never logged. Before the first message arrives, the status bar shows "STALE (999s)". | `gpsd_client`, `gps_logger`, `connection_status` |

### User experience

| # | Finding |
|---|---------|
| U1 | Settings (host, port, units, coordinate format, theme) are **not saved** between runs. There are no CLI arguments (`tuigps --host …`). |
| U2 | While gpsd is down, an error **notification is shown every 2 s** (one per reconnect attempt). |
| U3 | Stale or disconnected data still looks live in every panel. Only the status bar shows it. |
| U4 | The status bar's key hints are hard-coded and duplicate `BINDINGS`. On narrow terminals the GPS status (the important part) is cut off. |
| U5 | The dashboard grid is always 3 columns, so panels are truncated on terminals narrower than about 110 columns. |
| U6 | There is no help screen and no keys for switching tabs (`1`–`5`). |
| U7 | Destructive device actions (Cold Boot, Save Config, Disable PPS, baud change, setting the clock) run **without confirmation**. Clock sync silently turns NTP off and never offers to turn it back on. |
| U8 | The log format (GPX or CSV) and directory can't be chosen. Logs always go to the current directory. |

### Testing and quality

* There are no tests, no test dependencies and no coverage tooling.
* The python3-gps dependency makes the client hard to test on machines without gpsd.

---

## 2. Phased plan

Each phase ends with a green test suite and at least one commit. Later phases build on the tests from earlier ones.

### Phase 0: Test foundation
* Add the `[project.optional-dependencies] dev` extras (pytest, pytest-asyncio, pytest-cov) and the pytest config in `pyproject.toml`.
* Add a `tests/` package with a fake `gps` module fixture so the app imports without the system package.
* Add characterization tests for `formatting`, `constants`, `data_model`, `position_hold`, `gps_logger`, `clock_sync` (subprocess mocked) and the GPSDClient extraction paths (fake session).
* Add a headless Textual Pilot smoke test: the app mounts, every tab renders, keys work, and the settings modal opens and closes.
* Record a baseline coverage report.

### Phase 1: Correctness fixes (F1–F4, F6, F7, F9, F11–F13, F15, N1–N7)
* Fix Python 3.10 compatibility (N1), the PPS ioctl encoding and timeout (N2, N3), DMS/DDM rounding (N4), error labels (N5), `Select.NULL` handling (N6), and the minor issues in N7.
* Add a shared `parse_gps_time()` helper (F11).
* Track the TPV receipt time separately from `last_seen` and use it for fix age and PPS sync (F2).
* Feed position hold and the logger only on **new TPV** epochs (F1). Track an altitude-only count (F12).
* Isolate callback exceptions from connection handling (F3). Give each client run its own stop `Event` and join on stop, so reconnect never runs two threads (F4).
* Make GPX 1.1 output valid (F6).
* NMEA viewer: buffer while paused without duplicates, re-filter from the buffer, and show live stats (F7).
* Use `shlex.split` for ubxtool (F9). Fix the signal chart budget (F13). Replace silent `except: pass` with targeted handling or `self.log` (F15).
* Add a regression test for every fix.

### Phase 2: Native gpsd client and connection robustness (F5, F14, U2)
* Replace the python3-gps dependency with a small **native gpsd JSON protocol client** (TCP and `?WATCH`, newline-delimited JSON plus raw NMEA). This removes the `sys.path` workaround, makes tuigps fully pip-installable, and allows end-to-end tests against a fake gpsd TCP server.
* Publish **immutable snapshots** of `GPSData` to the UI (F5).
* Notify only when the connection state changes, use exponential reconnect backoff (2 s up to 30 s), and show the retry status in the status bar (U2).
* Add CLI arguments: `tuigps [host[:port]] --host --port --version` (U1, partial).

### Phase 3: Performance (P1–P5)
* Replace the blocking `call_from_thread` per message with a **non-blocking, coalesced** update: the reader marks the data dirty and the UI refreshes at most N Hz (about 10 Hz by default), plus a 1 s heartbeat for staleness.
* Deliver NMEA in batches.
* Refresh only the widgets on the **active tab**, plus the status bar.
* SkyPlot: cache the static background for each size and render with run-length style spans.
* Add micro-benchmarks and tests: render time limits, and confirm a burst of messages produces bounded refreshes.

### Phase 4: UX enhancements (U1, U3–U8, F8, F10)
* Save config to `~/.config/tuigps/config.json`: host, port, units, coordinate format, theme, log format and directory, and the constellation display filter.
* Settings: validate host and port. Add log format and directory. **Make the constellation checkboxes filter** the sky plot, signal chart and satellite table (F8).
* Apply the unit system consistently, including climb, geoid separation and error estimates (F10).
* Add a help screen (`?`) and number keys `1`–`5` to switch tabs. Make the status bar responsive so status takes priority over key hints.
* Make the dashboard responsive (3, 2 or 1 columns depending on width).
* Dim panels and show a "STALE" or "DISCONNECTED" tag when data is old.
* Add a confirmation modal for destructive device and clock actions, and a "Re-enable NTP" action after clock sync.

### Phase 5: Test completion, coverage and documentation
* Fill in the remaining unit and Pilot tests (widgets render with no data, partial data and full data; settings round trip; device config actions with mocked subprocesses).
* Produce a coverage report (terminal and HTML in `htmlcov/`). The target is ≥ 85% line coverage of the code that doesn't need hardware.
* Update `README.md`, `docs/*.md` and `CLAUDE.md` for the new behavior (native client, CLI arguments, config file, keys, testing instructions).

---

## 3. Process

* Each work block is implemented by a dedicated subagent. Blocks run **sequentially** on this branch because each phase depends on the previous one.
* Before each commit: the full test suite (`.venv/bin/pytest`) must pass and a headless Pilot smoke run must succeed.
* Commit messages follow repository convention, with no `Co-Authored-By` lines (per `CLAUDE.md`).
* Hardware-only paths (the real `/dev/pps*` ioctl, ubxtool against a real receiver, and actually setting the clock) are tested with mocks. They still need a manual check on real hardware before merging.
