"""Pilot tests for keys, the help screen and the binding-driven status bar (U4, U6)."""

from __future__ import annotations

import pytest
from rich.console import Console
from textual.widgets import Input, TabbedContent

from tests.test_app_smoke import _idle_gpsd_client, settle  # noqa: F401 (autouse fixture)
from tuigps.app import TABS, TuiGPS
from tuigps.screens.help_screen import HelpScreen
from tuigps.screens.settings_screen import SettingsScreen
from tuigps.widgets.connection_status import ConnectionStatus

SIZE = (130, 45)


def plain(renderable, width: int = 200) -> str:
    console = Console(width=width, record=True, file=None, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


async def test_number_keys_switch_tabs(make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        app._gps_data = make_gps_data()
        tabs = app.query_one(TabbedContent)
        for key, pane in [("2", "tab-satellites"), ("3", "tab-timing"), ("4", "tab-device"),
                          ("5", "tab-nmea"), ("1", "tab-dashboard"), ("3", "tab-timing")]:
            await pilot.press(key)
            await pilot.pause()
            assert tabs.active == pane
        # The newly shown tab got the current data
        assert "/dev/ttyACM0" in app.query_one("#w-device-detail").render().plain


async def test_help_lists_every_binding_and_tab():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, HelpScreen)
        keys = plain(screen.keys_table())
        lines = keys.splitlines()
        for binding in TuiGPS.BINDINGS:
            key = binding.key_display or app.get_key_display(binding)
            row = f" {key} "
            assert any(row in line and binding.description in line and binding.tooltip in line
                       for line in lines), f"binding {binding.key!r} missing from help"
        tabs = plain(screen.tabs_table())
        for n, (_, title, description) in enumerate(TABS, start=1):
            assert f" {n} " in tabs and title in tabs
            assert description.split()[0] in tabs


async def test_help_opens_and_closes():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        for open_key in ("question_mark", "f1"):
            for close_key in ("escape", "question_mark", "q"):
                await pilot.press(open_key)
                await pilot.pause()
                assert isinstance(app.screen, HelpScreen), open_key
                await pilot.press("u")  # app keys are inactive under the modal
                assert app._units == "metric"
                await pilot.press(close_key)
                await pilot.pause()
                assert not isinstance(app.screen, HelpScreen), close_key
                assert app.is_running  # "q" closed the help, it did not quit


async def test_keys_are_typed_into_device_command_input():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("4")
        await pilot.pause()
        inp = app.query_one("#input-cmd", Input)
        inp.focus()
        await pilot.pause()
        await pilot.press("1", "u", "s", "h", "l", "question_mark", "q")
        await pilot.pause()
        assert inp.value == "1ushl?q"
        assert app.query_one(TabbedContent).active == "tab-device"
        assert app._units == "metric"
        assert not app._hold.is_active and not app._gps_logger.is_active
        assert not isinstance(app.screen, (HelpScreen, SettingsScreen))
        assert app.is_running


async def test_keys_are_typed_into_settings_inputs():
    app = TuiGPS()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        host = screen.query_one("#input-host", Input)
        host.focus()
        await pilot.press("end", "2", "u", "q", "question_mark", "s")
        await pilot.pause()
        assert host.value == "127.0.0.12uq?s"
        assert app.screen is screen and app.is_running
        assert app._units == "metric"
        assert app.query_one(TabbedContent).active == "tab-dashboard"


# ─── Status bar (U4) ──────────────────────────────────────────────────


def test_status_hints_come_from_bindings():
    app = TuiGPS()
    shown = [b for b in TuiGPS.BINDINGS if b.show]
    assert [label for _, label in app.key_hints()] == [b.description for b in shown]
    assert ("?", "Help") in app.key_hints()
    assert app.help_hint() == ("?", "Help")
    assert not hasattr(ConnectionStatus, "BINDINGS_DISPLAY")  # no hard-coded copy


@pytest.mark.parametrize("badges", [False, True])
async def test_status_bar_fits_and_keeps_status(badges, make_gps_data, monkeypatch):
    # No heartbeat refresh: it would reset the badges set below from the app state
    monkeypatch.setattr(TuiGPS, "_heartbeat", lambda self: None)
    app = TuiGPS()
    async with app.run_test(size=(200, 24)) as pilot:
        bar = app.query_one("#w-connection", ConnectionStatus)
        bar.logging_active, bar.log_count = badges, 12
        bar.hold_active, bar.hold_count = badges, 3
        app._gps_data = make_gps_data()
        bar.update_gps_data(app._gps_data)
        labels = [label for _, label in app.key_hints()]
        for width in (200, 120, 80, 40):
            await pilot.resize_terminal(width, 24)
            await pilot.pause()
            assert bar.size.width == width
            text = bar.render()
            line = text.plain
            assert text.cell_len <= width, width
            assert line.rstrip().endswith("3D Fix  DGPS"), width  # the status, intact, right
            if badges:
                assert " REC " in line and " HOLD " in line, width  # badges beat hints
            if width == 200:
                assert "2GP+1GA/7sv /dev/ttyACM0" in line
                for key, label in app.key_hints():
                    assert f" {key}  {label} " in line
            if width == 120:
                assert "/dev/ttyACM0" in line
            shown = [label for label in labels if f" {label} " in line]
            if shown and shown != labels:
                # Hints are dropped from the end, and Help stands in for them
                assert shown[-1] == "Help", width
                assert shown[:-1] == labels[: len(shown) - 1], width
            if width == 80:
                assert shown and shown[-1] == "Help" and len(shown) < len(labels)


async def test_status_bar_drops_badge_counts_before_status():
    bar = ConnectionStatus(hints=[("q", "Quit")], help_hint=("?", "Help"))
    from tests.test_widgets import Harness
    from tuigps.data_model import GPSData

    app = Harness(bar)
    async with app.run_test(size=(30, 5)) as pilot:
        bar.logging_active, bar.log_count = True, 123456
        bar.hold_active, bar.hold_count = True, 7
        bar.update_gps_data(GPSData())
        await pilot.pause()
        text = bar.render()
        assert text.cell_len <= bar.size.width
        assert "DISCONNECTED" in text.plain and "REC" in text.plain
        assert "123456" not in text.plain  # counts went first
        await pilot.resize_terminal(18, 5)  # then the badges themselves
        await pilot.pause()
        text = bar.render()
        assert text.plain.strip() == "DISCONNECTED" and text.cell_len <= 18
