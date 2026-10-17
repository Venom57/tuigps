"""Performance guards (Phase 3): bounded UI refreshes and fast rendering.

Bounds are generous so the tests are not flaky on slow machines; they catch
regressions of an order of magnitude (e.g. one refresh per gpsd message, or a
gpsd thread that waits on the UI).
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from tuigps import app as app_module
from tuigps.app import TuiGPS
from tuigps.gpsd_client import GPSDClient
from tuigps.widgets.nmea_viewer import NMEAViewer

SIZE = (130, 45)
NMEA = ["$GPGGA,1*00", "$GPRMC,2*00", "$GPGSV,3*00", "$GPGSA,4*00"]


@pytest.fixture(autouse=True)
def _idle_gpsd_client(monkeypatch):
    """The app's own gpsd thread stays idle; the tests produce the data."""
    monkeypatch.setattr(GPSDClient, "_run", lambda self, stop=None: stop.wait())


async def _settle(pilot, app, timeout: float = 3.0) -> None:
    await pilot.pause()
    deadline = time.monotonic() + timeout
    while app._tick_pending and time.monotonic() < deadline:
        await pilot.pause(0.02)
    await pilot.pause()


async def test_burst_produces_bounded_refreshes(make_gps_data, monkeypatch):
    """P1/P2/P5: 500 snapshots + 2000 NMEA lines in a burst -> a handful of refreshes."""
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await _settle(pilot, app)
        refreshes = []
        original = app._refresh_ui

        def counting(*args, **kwargs):
            refreshes.append(app._gps_data)
            return original(*args, **kwargs)

        monkeypatch.setattr(app, "_refresh_ui", counting)
        snapshots = [make_gps_data(tpv_seq=i, latitude=10.0 + i / 1000) for i in range(1, 501)]

        def produce() -> float:
            busy = 0.0
            for d in snapshots:
                t = time.perf_counter()
                app._on_gpsd_update(d)
                for s in NMEA:
                    app._on_nmea(s)
                busy += time.perf_counter() - t
            return busy

        start = time.monotonic()
        producer_time = await asyncio.to_thread(produce)
        await _settle(pilot, app)
        elapsed = time.monotonic() - start

        print(f"\nburst: producer {producer_time * 1000:.1f} ms, "
              f"{len(refreshes)} refreshes in {elapsed * 1000:.0f} ms")
        assert producer_time < 0.5  # the gpsd thread never waits for the UI
        assert 1 <= len(refreshes) <= elapsed / app_module.UI_MIN_INTERVAL + 3
        # Trailing edge: the final state is shown
        assert refreshes[-1] is snapshots[-1]
        assert "10.500000" in app.query_one("#w-position").render().plain
        viewer = app.query_one(NMEAViewer)
        assert viewer._total == 2000
        assert viewer._type_counts["GGA"] == 500


async def test_steady_stream_refresh_rate_is_capped(make_gps_data, monkeypatch):
    """A 50 Hz stream of messages is shown at no more than about UI_REFRESH_HZ."""
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await _settle(pilot, app)
        refreshes = []
        original = app._refresh_ui
        monkeypatch.setattr(
            app, "_refresh_ui", lambda *a, **k: (refreshes.append(1), original(*a, **k))
        )
        stop = threading.Event()

        def produce():
            seq = 0
            while not stop.is_set():
                seq += 1
                app._on_gpsd_update(make_gps_data(tpv_seq=seq))
                app._on_nmea(NMEA[seq % 4])
                time.sleep(0.02)

        thread = threading.Thread(target=produce)
        start = time.monotonic()
        thread.start()
        await pilot.pause(1.0)
        stop.set()
        thread.join(2)
        await _settle(pilot, app)
        elapsed = time.monotonic() - start
        print(f"\nsteady 50 Hz: {len(refreshes)} refreshes in {elapsed:.2f} s")
        # <= 10 Hz coalesced ticks + 1 Hz heartbeat + slack
        assert len(refreshes) <= elapsed * (app_module.UI_REFRESH_HZ + 1) + 3
