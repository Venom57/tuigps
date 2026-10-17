"""Confirmation of destructive device/clock actions, and NTP re-enable (U7)."""

from __future__ import annotations

import subprocess

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Button, Label, Select

from tests.test_device_config_ui import Harness, _pps_setup, log_text, press, wait_for
from tuigps import clock_sync
from tuigps.clock_sync import NTP_DISABLED_HINT
from tuigps.screens.confirm_screen import ConfirmScreen
from tuigps.widgets.device_config import DeviceConfig

# The real functions (conftest replaces ntp_enabled for every test)
REAL_NTP_ENABLED = clock_sync.ntp_enabled
UBX = ["ubxtool", "-P", "18"]
NTP_OFF = ["timedatectl", "set-ntp", "false"]


@pytest.fixture
def commands(monkeypatch):
    """Record subprocess.run calls; every command succeeds."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# ─── The dialog itself ────────────────────────────────────────────────


class DialogApp(App):
    def __init__(self):
        super().__init__()
        self.results: list[bool] = []

    def compose(self) -> ComposeResult:
        yield Button("x")

    def ask(self) -> None:
        self.push_screen(ConfirmScreen("Really [b]?[/b]", "Do [red]it[/red]?", "Go"),
                         self.results.append)


@pytest.mark.parametrize("keys, expected", [
    (["escape"], [False]),
    (["enter"], [False]),  # Cancel has the focus: Enter never confirms by accident
    (["tab", "enter"], [True]),
])
async def test_confirm_screen_keys(keys, expected):
    app = DialogApp()
    async with app.run_test() as pilot:
        app.ask()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConfirmScreen)
        assert app.focused.id == "confirm-no"
        # Title and message are shown literally (no markup)
        title = screen.query_one("#confirm-dialog").border_title
        assert Content.from_markup(title).plain == "Really [b]?[/b]"
        assert "Do [red]it[/red]?" in str(screen.query_one("#confirm-message").render())
        assert str(screen.query_one("#confirm-yes", Button).label) == "Go"
        await pilot.press(*keys)
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
    assert app.results == expected


async def test_confirm_screen_buttons():
    app = DialogApp()
    async with app.run_test() as pilot:
        for button, _ in (("#confirm-no", False), ("#confirm-yes", True)):
            app.ask()
            await pilot.pause()
            app.screen.query_one(button, Button).press()
            await pilot.pause()
    assert app.results == [False, True]


# ─── Destructive receiver actions ─────────────────────────────────────


def _tp5_off() -> str:
    return DeviceConfig._build_tp5_cmd(None, freq_hz=0, pulse_us=0, active=False)


RECEIVER_ACTIONS = {
    "coldboot": ("btn-coldboot", [UBX + ["-p", "COLDBOOT"]], "ephemeris"),
    "save": ("btn-save", [UBX + ["-p", "SAVE"]], "non-volatile"),
    "pps-disable": ("btn-pps-disable", [UBX + ["-c", _tp5_off()]], "timepulse"),
    "baud": ("btn-baud", [UBX + ["-S", "115200"], ["gpsctl", "-s", "115200", "/dev/ttyACM0"]],
             "lose contact"),
}


@pytest.mark.parametrize("name", list(RECEIVER_ACTIONS))
@pytest.mark.parametrize("how", ["cancel", "escape", "confirm"])
async def test_receiver_actions_need_confirmation(name, how, commands, make_gps_data):
    btn_id, expected, warning = RECEIVER_ACTIONS[name]
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data())
        w.query_one("#sel-baud", Select).value = 115200
        await press(pilot, w, btn_id)
        screen = app.screen
        assert isinstance(screen, ConfirmScreen)
        assert warning in screen._message
        if name == "baud":
            assert "115200" in screen._title
        assert commands == []  # nothing runs before the answer
        if how == "escape":
            await pilot.press("escape")
        else:
            screen.query_one(f"#confirm-{'yes' if how == 'confirm' else 'no'}", Button).press()
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        if how == "confirm":
            assert await wait_for(pilot, lambda: len(commands) == len(expected))
        else:
            await pilot.pause(0.05)
    assert commands == (expected if how == "confirm" else [])


# ─── Setting the clock ────────────────────────────────────────────────


async def test_set_clock_now_confirmation(commands, make_gps_data):
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data())
        await press(pilot, w, "btn-set-clock")
        assert isinstance(app.screen, ConfirmScreen)
        assert "timedatectl set-ntp false" in app.screen._message
        app.screen.query_one("#confirm-no", Button).press()
        await pilot.pause(0.05)
        assert commands == []
        assert "System clock" not in log_text(w)

        await press(pilot, w, "btn-set-clock")
        app.screen.query_one("#confirm-yes", Button).press()
        assert await wait_for(pilot, lambda: NTP_DISABLED_HINT in log_text(w))
        out = log_text(w)
    assert commands[0] == NTP_OFF
    assert commands[1][:2] == ["busctl", "call"]
    assert out.index("System clock set to:") < out.index(NTP_DISABLED_HINT)


async def test_set_clock_failure_has_no_ntp_hint(monkeypatch, make_gps_data):
    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "denied")

    monkeypatch.setattr(subprocess, "run", failing)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data())
        await press(pilot, w, "btn-set-clock")
        app.screen.query_one("#confirm-yes", Button).press()
        assert await wait_for(pilot, lambda: "could not set time" in log_text(w))
        assert NTP_DISABLED_HINT not in log_text(w)


async def test_pps_sync_confirmation(monkeypatch, make_gps_data):
    calls, overrides = _pps_setup(monkeypatch, {})
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data(**overrides))
        await press(pilot, w, "btn-pps-sync")
        assert isinstance(app.screen, ConfirmScreen)
        assert "timedatectl set-ntp false" in app.screen._message
        await pilot.press("escape")
        await pilot.pause(0.05)
        assert calls == [] and "waiting for pulse" not in log_text(w)

        await press(pilot, w, "btn-pps-sync")
        app.screen.query_one("#confirm-yes", Button).press()
        assert await wait_for(pilot, lambda: NTP_DISABLED_HINT in log_text(w))
    # Confirming runs the sync (its commands: test_device_config_ui.py)
    assert calls[0] == NTP_OFF


async def test_arm_clock_sync_confirmation_only_when_arming():
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        await press(pilot, w, "btn-arm-clock")
        assert "timedatectl set-ntp false" in app.screen._message
        app.screen.query_one("#confirm-no", Button).press()
        await pilot.pause()
        assert app._armed_clock_set is False
        assert "ARMED" not in log_text(w)

        await press(pilot, w, "btn-arm-clock")
        app.screen.query_one("#confirm-yes", Button).press()
        await pilot.pause()
        btn = w.query_one("#btn-arm-clock", Button)
        assert app._armed_clock_set is True
        assert btn.variant == "success" and str(btn.label) == "ARMED (click to cancel)"
        assert "Clock sync ARMED" in log_text(w)

        await press(pilot, w, "btn-arm-clock")  # disarming: no dialog
        assert not isinstance(app.screen, ConfirmScreen)
        assert app._armed_clock_set is False
        assert btn.variant == "warning" and str(btn.label) == "Arm Clock Sync"
        assert "Clock sync disarmed" in log_text(w)


async def test_clock_checks_run_before_the_dialog(monkeypatch, make_gps_data):
    """No GPS time / no PPS device: report it at once instead of asking first."""
    monkeypatch.setattr("tuigps.widgets.device_config.glob.glob", lambda pat: [])
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        await press(pilot, w, "btn-set-clock")
        assert not isinstance(app.screen, ConfirmScreen)
        w.update_gps_data(make_gps_data())
        await press(pilot, w, "btn-pps-sync")
        assert not isinstance(app.screen, ConfirmScreen)
        assert "no PPS device found" in log_text(w)


# ─── NTP re-enable and state ──────────────────────────────────────────


async def test_reenable_ntp(commands, monkeypatch):
    states = iter([None, True])  # on mount, then after re-enabling
    monkeypatch.setattr(clock_sync, "ntp_enabled", lambda: next(states))
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        label = w.query_one("#ntp-state", Label)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert str(label.render()) == "NTP: ?"
        await press(pilot, w, "btn-ntp-on")
        assert not isinstance(app.screen, ConfirmScreen)  # harmless: no confirmation
        assert await wait_for(pilot, lambda: "NTP enabled" in log_text(w))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert str(label.render()) == "NTP: on"
        assert "$ timedatectl set-ntp true" in log_text(w)
    assert commands == [["timedatectl", "set-ntp", "true"]]


async def test_reenable_ntp_failure_is_logged(monkeypatch):
    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "Access denied")

    monkeypatch.setattr(subprocess, "run", failing)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        await press(pilot, w, "btn-ntp-on")
        assert await wait_for(
            pilot, lambda: "Error: timedatectl set-ntp true failed: Access denied" in log_text(w)
        )


async def test_ntp_state_shown_on_mount(monkeypatch):
    monkeypatch.setattr(clock_sync, "ntp_enabled", lambda: False)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert str(app.query_one("#ntp-state", Label).render()) == "NTP: off"


async def test_armed_clock_result_in_app_shows_ntp_hint(monkeypatch, make_gps_data):
    from tuigps.app import TuiGPS

    monkeypatch.setattr("tuigps.gpsd_client.GPSDClient._run", lambda self, stop=None: stop.wait())
    app = TuiGPS()
    async with app.run_test(size=(130, 45)) as pilot:
        await pilot.press("4")  # the Device tab: its log renders once it has a size
        await pilot.pause()
        app._deliver_clock_result("System clock set to: x")
        await pilot.pause()
        w = app.query_one(DeviceConfig)
        assert NTP_DISABLED_HINT in log_text(w)


# ─── clock_sync NTP helpers ───────────────────────────────────────────


@pytest.mark.parametrize("enabled, rc, stderr, expected", [
    (True, 0, "", "NTP enabled: automatic time sync restored"),
    (False, 0, "", "NTP disabled"),
    (True, 1, "Access denied\n", "Error: timedatectl set-ntp true failed: Access denied"),
    (True, 3, "", "Error: timedatectl set-ntp true failed: exit status 3"),
])
def test_set_ntp(enabled, rc, stderr, expected, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, "", stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert clock_sync.set_ntp(enabled) == expected
    assert calls == [["timedatectl", "set-ntp", "true" if enabled else "false"]]


@pytest.mark.parametrize("exc, expected", [
    (FileNotFoundError(2, "No such file"), "Error: timedatectl not found"),
    (subprocess.TimeoutExpired("timedatectl", 10), "Error: timedatectl set-ntp true timed out"),
])
def test_set_ntp_errors(exc, expected, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise exc

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert clock_sync.set_ntp(True) == expected


@pytest.mark.parametrize("rc, stdout, expected", [
    (0, "yes\n", True),
    (0, "no\n", False),
    (0, "maybe\n", None),
    (1, "", None),
])
def test_ntp_enabled(rc, stdout, expected, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert REAL_NTP_ENABLED() is expected
    assert calls == [["timedatectl", "show", "-p", "NTP", "--value"]]


def test_ntp_enabled_without_timedatectl(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file", "timedatectl")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert REAL_NTP_ENABLED() is None
