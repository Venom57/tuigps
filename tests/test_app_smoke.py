"""Headless Pilot smoke tests for the full TuiGPS app.

The fake ``gps`` module refuses connections by default, so the real
GPSDClient thread starts, fails to connect and reports errors through the
normal callback path. Data is then injected directly.
"""

from __future__ import annotations

import asyncio
import math
import threading

import pytest
from textual.widgets import Button, Input, RichLog, Select, TabbedContent

from tuigps import app as app_module
from tuigps.app import TuiGPS
from tuigps.data_model import GPSData
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
def _no_browser(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(app_module.webbrowser, "open", lambda url: opened.append(url))
    return opened


@pytest.fixture
def opened_urls(_no_browser):
    return _no_browser


def render_all(app: TuiGPS) -> dict[str, str]:
    return {wid: app.query_one(f"#{wid}").render().plain for wid in WIDGET_IDS}


async def test_app_mounts_with_failing_gpsd(fake_gps):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        assert app._gpsd._thread is not None and app._gpsd._thread.is_alive()
        # The client thread attempted to connect and failed
        for _ in range(50):
            if fake_gps.gps.calls and app._gpsd.data.error_message:
                break
            await pilot.pause(0.02)
        assert fake_gps.gps.calls
        assert "Connection refused" in app._gpsd.data.error_message
        assert not app._gpsd.data.connected
        assert app.theme == "textual-dark"
        status = app.query_one("#w-connection", ConnectionStatus).render().plain
        assert "DISCONNECTED" in status
    # on_unmount stops the client
    assert app._gpsd._running is False


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
        await pilot.pause()
        assert app._gps_data is data
        assert "47.606200" in app.query_one("#w-position").render().plain


async def test_nmea_and_error_from_thread():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await asyncio.to_thread(app._on_nmea, "$GPGGA,123*00")
        await asyncio.to_thread(app._on_gpsd_error, "boom")
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
        await pilot.pause()
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
        await pilot.pause()
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


async def test_key_reconnect(fake_gps, make_gps_data):
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
        assert app._gps_data is app._gpsd.data
        assert not app._gps_data.has_fix
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


async def test_settings_apply_new_host_reconnects(fake_gps):
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
            if any(c["host"] == "10.0.0.5" for c in fake_gps.gps.calls):
                break
            await pilot.pause(0.02)
        assert any(c["host"] == "10.0.0.5" and c["port"] == "3000" for c in fake_gps.gps.calls)


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
        await pilot.pause()
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
        await pilot.pause()
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


def test_hold_and_log_follow_client_tpv_epochs(fake_session, tpv_session, tmp_path):
    """End to end through GPSDClient: one hold/log entry per TPV message."""
    from tuigps.gpsd_client import GPSDClient

    app = TuiGPS()
    app._hold.start()
    app._gps_logger._directory = tmp_path
    app._gps_logger._format = "csv"
    app._gps_logger.start()
    client = GPSDClient()
    client._on_update = app._on_gpsd_update
    tpv = tpv_session()
    times = ["2026-03-14T12:00:00Z", "2026-03-14T12:00:01Z"]
    for t in times:
        tpv.fix.time = t
        tpv.data = {"class": "TPV"}
        client._extract_data(tpv)
        client._notify_update()
        for cls in ("SKY", "PPS", "WATCH"):  # same fix, other message classes
            client._extract_data(fake_session(data={"class": cls}))
            client._notify_update()
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
        app._deliver_nmea("$GPGGA,1*00")
        app._deliver_clock_result("System clock set to: x")
        await pilot.pause()
        assert app.is_running
    assert not [r for r in caplog.records if r.name.startswith("tuigps")]
