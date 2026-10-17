"""Headless Pilot smoke tests for the full TuiGPS app.

In tests the client's default gpsd port refuses connections (see conftest),
so the real GPSDClient thread starts, fails to connect and reports errors
through the normal callback path. Data is then injected directly, or streamed
by the fake gpsd server.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time

import pytest
from textual.widgets import Button, Input, RichLog, Select, TabbedContent

from tuigps import app as app_module
from tuigps.app import TuiGPS
from tuigps.data_model import GPSData
from tuigps.gpsd_client import GPSDClient
from tuigps.screens.settings_screen import SettingsScreen
from tuigps.widgets.connection_status import ConnectionStatus
from tuigps.widgets.device_config import DeviceConfig
from tuigps.widgets.nmea_viewer import NMEAViewer
from tuigps.widgets.position_panel import PositionPanel

SIZE = (130, 45)
TABS = ["tab-dashboard", "tab-satellites", "tab-timing", "tab-device", "tab-nmea"]
WIDGET_IDS = [
    "w-position", "w-fix", "w-velocity", "w-skyplot", "w-signal", "w-errors", "w-device",
    "w-time", "w-constellations", "w-sattable", "w-time-detail", "w-device-detail",
    "w-connection",
]


@pytest.fixture(autouse=True)
def _idle_gpsd_client(request, monkeypatch):
    """Keep the app's gpsd thread idle so tests can inject data without races.

    The real client publishes connection-state snapshots (which replace the
    app's data) as soon as it starts; tests marked ``live_gpsd`` run it.
    """
    if request.node.get_closest_marker("live_gpsd"):
        return
    monkeypatch.setattr(GPSDClient, "_run", lambda self, stop=None: stop.wait())


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(app_module.webbrowser, "open", lambda url: opened.append(url))
    return opened


@pytest.fixture
def opened_urls(_no_browser):
    return _no_browser


async def settle(pilot, app: TuiGPS, timeout: float = 2.0) -> None:
    """Wait until updates posted from other threads have reached the widgets.

    gpsd updates are delivered without blocking and coalesced into rate-limited
    UI ticks, so they land a little after the producer call returns.
    """
    await pilot.pause()
    for _ in range(int(timeout / 0.02)):
        if not app._tick_pending:
            break
        await pilot.pause(0.02)
    await pilot.pause()


def render_all(app: TuiGPS) -> dict[str, str]:
    return {wid: app.query_one(f"#{wid}").render().plain for wid in WIDGET_IDS}


@pytest.mark.live_gpsd
async def test_app_mounts_with_failing_gpsd():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert app._gpsd._thread is not None and app._gpsd._thread.is_alive()
        # The client thread attempted to connect and failed
        for _ in range(100):
            if app._gpsd.data.error_message:
                break
            await pilot.pause(0.02)
        assert "Connection refused" in app._gpsd.data.error_message
        assert not app._gpsd.data.connected
        assert app.theme == "textual-dark"
        await pilot.pause(0.05)
        status = app.query_one("#w-connection", ConnectionStatus).render().plain
        assert "DISCONNECTED" in status
    # on_unmount stops the client
    assert app._gpsd._running is False


@pytest.mark.live_gpsd
async def test_app_shows_data_from_fake_gpsd(fake_gpsd):
    """End to end: fake gpsd -> native client -> dashboard widgets."""
    from tests.test_gpsd_client import DEVICES_325, SKY_325, TPV_325

    fake_gpsd.add(DEVICES_325, TPV_325, SKY_325)
    app = TuiGPS()
    app._gpsd = GPSDClient("127.0.0.1", fake_gpsd.port)
    async with app.run_test(size=SIZE) as pilot:
        for _ in range(150):
            if app._gps_data.satellites and app._gps_data.has_fix:
                break
            await pilot.pause(0.02)
        await pilot.pause(0.05)
        assert fake_gpsd.received.startswith(b"?WATCH=")
        assert "47.606200" in app.query_one("#w-position").render().plain
        status = app.query_one("#w-connection", ConnectionStatus).render().plain
        assert "3D Fix" in status and "DGPS" in status
        assert "/dev/ttyACM0" in status


@pytest.mark.live_gpsd
async def test_outage_notifies_once_and_reconnect_is_announced(fake_gpsd, monkeypatch):
    """U2: one error notification per outage, and a notice on reconnect."""
    from tests.fake_gpsd import FakeGpsd
    from tests.test_gpsd_client import TPV_325

    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 0.02)
    monkeypatch.setattr(GPSDClient, "RECONNECT_MAX", 0.05)
    port = fake_gpsd.port
    fake_gpsd.add(TPV_325)
    app = TuiGPS()
    app._gpsd = GPSDClient("127.0.0.1", port)
    notes: list[str] = []
    app.notify = lambda message, **kw: notes.append(message)

    async def wait_for(predicate):
        for _ in range(200):
            if predicate():
                return True
            await pilot.pause(0.02)
        return False

    server = None
    async with app.run_test(size=SIZE) as pilot:
        assert await wait_for(lambda: app._gps_data.has_fix)
        assert await wait_for(lambda: notes)
        assert notes == [f"Connected to gpsd 127.0.0.1:{port}"]
        fake_gpsd.close()
        assert await wait_for(lambda: app._gps_data.reconnect_attempts >= 3)
        await pilot.pause(0.05)
        assert notes[1:] == ["gpsd: gpsd disconnected"]  # once, not per attempt
        app._refresh_ui()
        status = app.query_one("#w-connection", ConnectionStatus).render().plain
        assert "DISCONNECTED" in status or "CONNECTING" in status
        server = FakeGpsd(port=port)
        try:
            assert await wait_for(lambda: len(notes) == 3)
            assert notes[2] == f"Connected to gpsd 127.0.0.1:{port}"
        finally:
            app._gpsd.stop()
            server.close()


async def test_every_tab_activates_and_renders(make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app._gps_data = make_gps_data()
        app._refresh_ui()
        tabs = app.query_one(TabbedContent)
        for tab in TABS:
            tabs.active = tab
            await pilot.pause()
            assert tabs.active == tab
        rendered = render_all(app)
        assert "47.606200" in rendered["w-position"]
        assert "3D Fix" in rendered["w-fix"]
        assert "Total:" in rendered["w-constellations"]
        assert "PPS" in rendered["w-time-detail"]
        assert "/dev/ttyACM0" in rendered["w-device-detail"]
        assert "2GP+1GA" in rendered["w-connection"]
        assert app.query_one("#w-device-config", DeviceConfig)._device_path == "/dev/ttyACM0"


async def test_refresh_ui_with_empty_and_partial_data():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        for data in (GPSData(), GPSData(connected=True, mode=1, time="2026-03-14T12:00:00Z")):
            app._gps_data = data
            app._refresh_ui()
            await pilot.pause()
            render_all(app)


async def test_gpsd_update_from_thread(make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        data = make_gps_data()
        await asyncio.to_thread(app._on_gpsd_update, data)
        await settle(pilot, app)
        assert app._gps_data is data
        assert "47.606200" in app.query_one("#w-position").render().plain


async def test_nmea_and_error_from_thread():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await asyncio.to_thread(app._on_nmea, "$GPGGA,123*00")
        await asyncio.to_thread(app._on_gpsd_error, "boom")
        await settle(pilot, app)
        app.query_one(TabbedContent).active = "tab-nmea"
        await pilot.pause()
        log = app.query_one(NMEAViewer).query_one("#nmea-log", RichLog)
        assert len(log.lines) == 1


async def test_keys_units_theme_dark():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        assert app._units == "metric"
        await pilot.press("u")
        assert app._units == "imperial"
        assert app.query_one("#w-position", PositionPanel).units == "imperial"
        await pilot.press("u", "u")
        assert app._units == "metric"

        start = app.theme
        await pilot.press("t")
        assert app.theme != start
        assert app.theme == app._theme_list[app._theme_index]
        before = app.theme
        await pilot.press("d")
        assert app.theme == ("textual-dark" if before == "textual-light" else "textual-light")


async def test_key_hold(make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("h")
        assert app._hold.is_active
        data = make_gps_data()
        await asyncio.to_thread(app._on_gpsd_update, data)
        await settle(pilot, app)
        assert app._hold.fix_count >= 1
        assert "Hold (" in app.query_one("#w-position").render().plain
        assert "HOLD" in app.query_one("#w-connection").render().plain
        await pilot.press("h")
        assert not app._hold.is_active


async def test_key_log_writes_into_cwd(tmp_path, monkeypatch, make_gps_data):
    monkeypatch.chdir(tmp_path)
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("l")
        assert app._gps_logger.is_active
        path = app._gps_logger.filepath
        await asyncio.to_thread(app._on_gpsd_update, make_gps_data())
        await settle(pilot, app)
        assert app._gps_logger.fix_count == 1
        assert "REC" in app.query_one("#w-connection").render().plain
        await pilot.press("l")
        assert not app._gps_logger.is_active
    files = list(tmp_path.glob("tuigps_*.gpx"))
    assert len(files) == 1 and files[0].name == path.name
    text = files[0].read_text()
    assert "<trkpt" in text and text.rstrip().endswith("</gpx>")


async def test_unmount_stops_active_logger(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("l")
    assert not app._gps_logger.is_active
    (f,) = tmp_path.glob("tuigps_*.gpx")
    assert f.read_text().rstrip().endswith("</gpx>")


async def test_key_maps(make_gps_data, opened_urls):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("m")  # no fix -> warning, no browser
        assert opened_urls == []
        app._gps_data = make_gps_data()
        await pilot.press("m")
    assert opened_urls == ["https://maps.google.com/?q=47.606200,-122.332100"]


async def test_key_reconnect(make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        old_thread = app._gpsd._thread
        app._gpsd._data = make_gps_data(tpv_seq=5)  # client has a fix from the old session
        app._on_gpsd_update(app._gpsd.data)
        assert app._last_tpv_seq == 5
        old_data = app._gps_data
        await pilot.press("r")
        await pilot.pause()
        assert app._gps_data is not old_data
        assert app._gpsd._running
        # F4: client data was reset too, so the UI and the client agree
        assert app._gps_data.tpv_seq == 0 == app._gpsd.data.tpv_seq
        assert not app._gps_data.has_fix and not app._gpsd.data.has_fix
        assert app._last_tpv_seq == 0  # the new session's first TPV (seq 1) is new
        # ...and only the new polling thread is alive
        assert not old_thread.is_alive()
        alive = [t for t in threading.enumerate() if t.name == "gpsd-client" and t.is_alive()]
        assert alive == [app._gpsd._thread]


async def test_settings_open_escape_and_cancel():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        assert app.screen.query_one("#input-host", Input).value == "127.0.0.1"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)

        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#select-units", Select).value = "imperial"
        app.screen.query_one("#btn-cancel", Button).press()
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert app._units == "metric"


async def test_settings_apply_same_host_keeps_client():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        client = app._gpsd
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#select-units", Select).value = "nautical"
        screen.query_one("#select-coord", Select).value = "ddm"
        screen.query_one("#chk-gnss-galileo").value = True
        screen.query_one("#btn-apply", Button).press()
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert app._units == "nautical"
        assert app._coord_format == "ddm"
        assert app._enabled_gnss == {"gps", "galileo"}
        assert app._gpsd is client


@pytest.mark.live_gpsd
async def test_settings_apply_new_host_reconnects(monkeypatch):
    attempts = []

    def fake_open(self, stop):
        attempts.append((self.host, self.port))
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(GPSDClient, "_open_socket", fake_open)
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        old = app._gpsd
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#input-host", Input).value = "10.0.0.5"
        app.screen.query_one("#input-port", Input).value = "3000"
        app.screen.query_one("#btn-apply", Button).press()
        await pilot.pause()
        assert app._gpsd is not old
        assert (app._gpsd.host, app._gpsd.port) == ("10.0.0.5", "3000")
        assert old._running is False
        for _ in range(50):
            if ("10.0.0.5", "3000") in attempts:
                break
            await pilot.pause(0.02)
        assert ("10.0.0.5", "3000") in attempts


async def test_arm_and_clear_toff(make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app.query_one(TabbedContent).active = "tab-timing"
        await pilot.pause()
        btn = app.query_one("#btn-arm-toff", Button)
        btn.press()
        await pilot.pause()
        assert app._gpsd.toff_armed
        assert btn.variant == "success"

        # Simulate the client firing the armed measurement
        data = make_gps_data(toff_armed_offset=0.0123)
        app._gpsd.toff_armed = False
        app._gps_data = data
        app._refresh_ui()
        await pilot.pause()
        assert btn.variant == "warning"
        assert "+12.3 ms" in str(btn.label)

        app.query_one("#btn-clear-toff", Button).press()
        await pilot.pause()
        assert str(btn.label) == "Arm TOFF"
        assert app._gps_data.toff_samples == []
        assert math.isnan(app._gps_data.toff_armed_offset)


async def test_armed_clock_sync_fires_on_update(make_gps_data, monkeypatch):
    calls = []

    def fake_set_clock(gps_time, fix_received):
        calls.append((gps_time, fix_received))
        return "System clock set to: fake"

    monkeypatch.setattr(app_module, "set_clock_from_gps", fake_set_clock)
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app.query_one(TabbedContent).active = "tab-device"
        await pilot.pause()
        app.query_one("#btn-arm-clock", Button).press()
        await pilot.pause()
        assert app._armed_clock_set
        # A non-TPV message does not fire the armed sync (F1)...
        await asyncio.to_thread(app._on_gpsd_update, make_gps_data(tpv_seq=0))
        assert app._armed_clock_set
        # ...the next TPV does, with the TPV receipt time as fix age reference (F2)
        data = make_gps_data(tpv_seq=1)
        data.tpv_received = data.last_seen - 0.5
        await asyncio.to_thread(app._on_gpsd_update, data)
        await settle(pilot, app)
        assert not app._armed_clock_set
        btn = app.query_one("#btn-arm-clock", Button)
        assert str(btn.label) == "Arm Clock Sync"
    assert calls == [(data.time, data.tpv_received)]


async def test_armed_clock_sync_error_is_reported(make_gps_data, monkeypatch):
    def fake_set_clock(gps_time, fix_received):
        raise ValueError("bad time")

    monkeypatch.setattr(app_module, "set_clock_from_gps", fake_set_clock)
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app.query_one(TabbedContent).active = "tab-device"
        await pilot.pause()
        app._armed_clock_set = True
        await asyncio.to_thread(app._on_gpsd_update, make_gps_data())
        await settle(pilot, app)
        out = app.query_one(DeviceConfig).query_one("#config-output", RichLog)
        text = "\n".join("".join(seg.text for seg in strip) for strip in out.lines)
        assert "Clock sync error: bad time" in text


async def test_deliver_clock_error_result():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app._deliver_clock_result("Error: could not set time.\n  details")
        await pilot.pause()


# ─── App logic without a running event loop ───────────────────────────


def test_update_without_running_app_is_safe(make_gps_data):
    app = TuiGPS()
    app._on_gpsd_update(make_gps_data())
    app._on_nmea("$GPGGA")
    app._on_gpsd_error("x")


def test_logger_dedupes_repeated_snapshot(make_gps_data, tmp_path):
    app = TuiGPS()
    app._gps_logger._directory = tmp_path
    app._gps_logger.start()
    data = make_gps_data()
    for _ in range(3):
        app._on_gpsd_update(data)
    assert app._gps_logger.fix_count == 1
    app._gps_logger.stop()


def test_hold_counts_each_fix_once(make_gps_data):
    """F1: SKY/PPS/NMEA updates re-deliver the same TPV and are not new fixes."""
    app = TuiGPS()
    app._hold.start()
    data = make_gps_data()
    for _ in range(3):  # e.g. TPV followed by SKY and PPS with the same fix
        app._on_gpsd_update(data)
    assert app._hold.fix_count == 1
    data.tpv_seq += 1  # next TPV epoch
    app._on_gpsd_update(data)
    assert app._hold.fix_count == 2


def test_hold_and_log_follow_client_tpv_epochs(tmp_path):
    """End to end through GPSDClient: one hold/log entry per TPV message."""
    from tests.test_gpsd_client import GGA, PPS_325, SKY_325, WATCH_325, tpv

    app = TuiGPS()
    app._hold.start()
    app._gps_logger._directory = tmp_path
    app._gps_logger._format = "csv"
    app._gps_logger.start()
    client = GPSDClient()
    client._on_update = app._on_gpsd_update
    for t in ["2026-03-14T12:00:00Z", "2026-03-14T12:00:01Z"]:
        client._handle_line(tpv(time=t))
        for line in (SKY_325, PPS_325, WATCH_325, GGA):  # same fix, other messages
            client._handle_line(line)
    app._gps_logger.stop()
    assert client.data.tpv_seq == 2
    assert app._hold.fix_count == 2
    assert app._gps_logger.fix_count == 2


async def test_failing_widget_update_is_logged_not_fatal(make_gps_data, monkeypatch, caplog):
    """F15: one broken widget is logged and does not stop the other updates."""
    from tuigps.widgets.fix_panel import FixPanel

    def boom(self, data):
        raise RuntimeError("fix panel exploded")

    monkeypatch.setattr(FixPanel, "update_gps_data", boom)
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app._gps_data = make_gps_data()
        app._refresh_ui()
        await pilot.pause()
        assert app.is_running
        # Widgets after the failing one were still updated
        assert "/dev/ttyACM0" in app.query_one("#w-device-detail").render().plain
    assert any("#w-fix" in r.getMessage() and r.exc_info for r in caplog.records)


async def test_refresh_with_modal_open_is_silent(make_gps_data, caplog):
    """Widgets are not queryable while a modal is active: NoMatches is expected, not logged."""
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        app._gps_data = make_gps_data()
        app._refresh_ui()
        app._deliver_nmea_batch(["$GPGGA,1*00"])
        app._deliver_clock_result("System clock set to: x")
        await pilot.pause()
        assert app.is_running
    assert not [r for r in caplog.records if r.name.startswith("tuigps")]


# ─── Non-blocking, coalesced delivery (P1/P2) ─────────────────────────


async def test_gpsd_thread_never_waits_for_the_ui(make_gps_data, monkeypatch):
    """P2: a slow UI refresh must not block the gpsd reader thread."""
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        refreshes = []
        original = app._refresh_ui

        def slow_refresh(*args, **kwargs):
            refreshes.append(app._gps_data)
            time.sleep(0.2)  # a pathologically slow UI
            original(*args, **kwargs)

        monkeypatch.setattr(app, "_refresh_ui", slow_refresh)
        snapshots = [make_gps_data(tpv_seq=i, latitude=10.0 + i) for i in range(1, 51)]

        def produce():
            start = time.perf_counter()
            for d in snapshots:
                app._on_gpsd_update(d)
                app._on_nmea("$GPGGA,x*00")
            return time.perf_counter() - start

        elapsed = await asyncio.to_thread(produce)
        assert elapsed < 0.15  # 50 updates + 50 sentences, never waiting on a 0.2 s refresh
        await settle(pilot, app, timeout=3.0)
        # Coalesced: far fewer refreshes than updates, and the last one shows the final state
        assert 1 <= len(refreshes) <= 4
        assert refreshes[-1] is snapshots[-1]
        assert "60.000000" in app.query_one("#w-position").render().plain


async def test_rapid_updates_are_coalesced_with_trailing_refresh(make_gps_data, monkeypatch):
    """P1: updates within one refresh interval produce one more refresh, with the last data."""
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, app)
        shown = []
        original = app._refresh_ui

        def counting(*args, **kwargs):
            shown.append(app._gps_data)
            original(*args, **kwargs)

        monkeypatch.setattr(app, "_refresh_ui", counting)
        first, second, third = (make_gps_data(tpv_seq=i) for i in (1, 2, 3))
        await asyncio.to_thread(app._on_gpsd_update, first)  # leading edge: immediate
        await pilot.pause()
        await asyncio.to_thread(app._on_gpsd_update, second)  # within the interval:
        await asyncio.to_thread(app._on_gpsd_update, third)  # coalesced, trailing edge
        await settle(pilot, app)
        assert shown[0] is first
        assert shown[-1] is third
        assert len(shown) <= 3  # first, one coalesced trailing refresh (+ maybe a heartbeat)


async def test_delivery_errors_during_shutdown_are_swallowed(make_gps_data, caplog):
    """A closed event loop (app shutting down) must not raise on the gpsd thread."""

    class ClosedLoop:
        def call_soon_threadsafe(self, *args):
            raise RuntimeError("Event loop is closed")

    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot, app)
        loop = app._loop
        app._loop = ClosedLoop()
        try:
            app._conn_state = ""
            await asyncio.to_thread(app._on_gpsd_update, make_gps_data())
            await asyncio.to_thread(app._on_nmea, "$GPGGA,1*00")
            await asyncio.to_thread(app._on_gpsd_error, "boom")
            assert not app._tick_pending  # a later update can schedule again
        finally:
            app._loop = loop


async def test_producer_running_across_shutdown_is_safe(make_gps_data):
    errors: list[BaseException] = []
    stop = threading.Event()
    app = TuiGPS()

    def produce():
        data = make_gps_data()
        try:
            while not stop.is_set():
                app._on_gpsd_update(data)
                app._on_nmea("$GPGGA,1*00")
                time.sleep(0.001)
        except BaseException as e:  # pragma: no cover - asserted below
            errors.append(e)

    thread = threading.Thread(target=produce)
    async with app.run_test(size=SIZE) as pilot:
        thread.start()
        await pilot.pause(0.15)
    await asyncio.sleep(0.1)  # keep producing after the app has stopped
    stop.set()
    thread.join(2)
    assert errors == []
    assert app._tick_handle is None
