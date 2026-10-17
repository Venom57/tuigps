"""Headless Pilot smoke tests for the full TuiGPS app.

The fake ``gps`` module refuses connections by default, so the real
GPSDClient thread starts, fails to connect and reports errors through the
normal callback path. Data is then injected directly.
"""

from __future__ import annotations

import asyncio
import math

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


async def test_key_reconnect(fake_gps):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        old_data = app._gps_data
        await pilot.press("r")
        await pilot.pause()
        assert app._gps_data is not old_data
        assert app._gpsd._running


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

    def fake_set_clock(gps_time, last_seen):
        calls.append((gps_time, last_seen))
        return "System clock set to: fake"

    monkeypatch.setattr(app_module, "set_clock_from_gps", fake_set_clock)
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app.query_one(TabbedContent).active = "tab-device"
        await pilot.pause()
        app.query_one("#btn-arm-clock", Button).press()
        await pilot.pause()
        assert app._armed_clock_set
        data = make_gps_data()
        await asyncio.to_thread(app._on_gpsd_update, data)
        await pilot.pause()
        assert not app._armed_clock_set
        btn = app.query_one("#btn-arm-clock", Button)
        assert str(btn.label) == "Arm Clock Sync"
    assert calls == [(data.time, data.last_seen)]


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


@pytest.mark.xfail(strict=True, reason="F1: position hold adds the same fix on every message")
def test_hold_counts_each_fix_once(make_gps_data):
    app = TuiGPS()
    app._hold.start()
    data = make_gps_data()
    for _ in range(3):  # e.g. TPV followed by SKY and PPS with the same fix
        app._on_gpsd_update(data)
    assert app._hold.fix_count == 1
