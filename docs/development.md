# Development

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

The `dev` extra installs pytest, pytest-asyncio, pytest-cov and
**pytest-xdist**. pytest-xdist is required: `pyproject.toml` sets
`addopts = "-n auto"`, so without it every pytest run fails with
`pytest: error: unrecognized arguments: -n`. Install the dev extra (or
`pip install pytest-xdist`) to fix it.

tuigps itself needs only Textual and Rich. Since the native gpsd client
replaced `python3-gps`, no system Python packages are involved, and
everything installs into a plain venv.

## Running the tests

```bash
pytest                       # whole suite, in parallel on every CPU (about 20 s)
pytest -n0                   # one serial process: use with --pdb, -s or print debugging
pytest tests/test_cli.py     # one file
pytest -k "nmea and pause"   # by name
```

The tests never touch your machine. They do not connect to a real gpsd, run
commands (timedatectl, busctl, sudo, ubxtool, gpsctl), set the clock, open
`/dev/pps*`, open a browser, or read or write `~/.config`. A test that
accidentally runs a command fails with `unexpected subprocess.run in tests`.

## Coverage

```bash
pytest --cov --cov-branch --cov-report=term-missing --cov-report=html
```

This prints a per-module table with the missing lines and branches, and
writes an HTML report to `htmlcov/index.html` (git-ignored). Branch coverage
is also enabled in `[tool.coverage.run]`, so plain `pytest --cov` measures
branches too. Coverage works with xdist: pytest-cov combines the data from
the workers.

The few lines that remain uncovered are defensive guards that cannot be
reached through the UI. Examples are a button without an id, and a
`NoMatches` from a widget that is always mounted. Hardware paths (the real
PPS ioctl, ubxtool against a receiver, actually setting the clock) are
covered with mocks. They still need the
[manual checks on real hardware](IMPROVEMENT_PLAN.md#manual-verification-on-real-hardware).

## Test architecture

| Piece | What it does |
|-------|--------------|
| `tests/conftest.py` | Autouse safety fixtures. (1) The gpsd client's default address is pointed at a local port that refuses connections. (2) `XDG_CONFIG_HOME` is a temp directory. (3) `subprocess.run` raises. (4) `clock_sync.ntp_enabled` answers "unknown". It also provides the `make_gps_data()` factory (a full 3D fix with seven satellites), `fake_gpsd`, `config_file` and `refused_port`. |
| `tests/fake_gpsd.py` | A scriptable fake gpsd TCP server. It sends a VERSION banner, records what the client sends, streams scripted reports after `?WATCH` (CRLF-terminated, like gpsd), and can drop clients to simulate an outage. The end-to-end client and app tests use it, including over IPv6 loopback. |
| Pilot tests | `App.run_test()` runs the real app headless. In `test_app_smoke.py`, `test_layout.py` and `test_performance.py` the app's gpsd thread is kept idle, and tests inject `GPSData` snapshots directly. Tests marked `live_gpsd` run the real client thread against the fake server instead. |
| Mocked hardware | `subprocess.run` is replaced per test with a fake that records commands and returns scripted results. The PPS ioctl (`_wait_for_pps`), `glob("/dev/pps*")` and `time.time()` are patched for PPS sync. |
| `test_compat.py` | Checks that the source parses as Python 3.10 and contains no f-string expressions that only Python 3.12+ accepts. |
| `test_performance.py` | Regression guards with generous limits: bursts of updates produce a bounded number of refreshes, the gpsd thread never waits for the UI, and the sky plot renders quickly. |

Test files map to modules: `test_gpsd_client.py` (protocol parsing, reader
thread, reconnect), `test_cli.py`, `test_config.py`, `test_clock_sync.py`,
`test_gps_logger.py`, `test_position_hold.py`, `test_formatting.py`,
`test_timeutil.py`, `test_data_model.py`, `test_constants.py`,
`test_widgets.py` and `test_sky_plot.py` (rendering),
`test_device_config_*.py` and `test_confirm.py` (Device tab, confirmations,
NTP), `test_navigation.py` (keys, help, status bar), `test_layout.py`
(responsive dashboard), `test_stale.py` (stale/disconnected indication).

### Writing tests

- Build data with `make_gps_data(**overrides)`. Snapshots are treated as
  immutable: build a new one for each update instead of mutating one that
  was already delivered, because widgets skip repainting when they are
  handed the same object again.
- Updates reach the widgets asynchronously, in coalesced UI ticks. After
  calling `_on_gpsd_update` from a thread, wait with the `settle()` helper
  (in `test_app_smoke.py`) before asserting on rendered text.
- A `RichLog` on a hidden tab buffers its writes until it has a size. Switch
  to the tab before reading the Device or NMEA log.
- Keep timing assertions relative: derive bounds from measured elapsed time,
  or widen the interval with `monkeypatch`. The suite runs in parallel and
  must pass on a loaded machine.

## Running against simulated or real data

```bash
gpsfake -c 0.5 /usr/share/gpsd/sample.nmea   # gpsd-clients; serves on 127.0.0.1:2947
tuigps
```

gpsfake runs its own gpsd on the default port, so stop a system gpsd first,
or run gpsfake with `-P PORT` and start `tuigps :PORT`.

## Project layout

See [CLAUDE.md](../CLAUDE.md) for the module map and the main code patterns,
and the README's [architecture](../README.md#architecture) section for the
threading model.
