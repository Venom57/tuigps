"""Pilot tests for the DeviceConfig widget (all external commands mocked)."""

from __future__ import annotations

import subprocess

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, RichLog, Select

from tuigps.widgets import device_config as dc
from tuigps.widgets.device_config import DeviceConfig


class Harness(App):
    def __init__(self):
        super().__init__()
        self._armed_clock_set = False  # DeviceConfig pokes this attribute on the app

    def compose(self) -> ComposeResult:
        yield DeviceConfig(id="dc")


def log_text(widget: DeviceConfig) -> str:
    log = widget.query_one("#config-output", RichLog)
    return "\n".join("".join(seg.text for seg in strip) for strip in log.lines)


async def wait_for(pilot, cond, timeout: float = 3.0) -> bool:
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await pilot.pause(0.02)
    return cond()


@pytest.fixture
def ubx_calls(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(DeviceConfig, "_run_ubxtool", lambda self, args: calls.append(args))
    return calls


async def press(pilot, w, btn_id):
    w.query_one(f"#{btn_id}", Button).press()
    await pilot.pause()


SIMPLE_BUTTONS = [
    ("btn-save", "-p SAVE"),
    ("btn-coldboot", "-p COLDBOOT"),
    ("btn-read", "-p CFG-NAV5"),
    ("btn-read-rate", "-p CFG-RATE"),
    ("btn-gnss-read", "-p CFG-GNSS"),
    ("btn-baud-read", "-p CFG-PRT"),
    ("btn-pps-read", "-p CFG-TP5"),
]

SELECT_BUTTONS = [
    ("sel-model", "btn-model", 2, "-p MODEL,2"),
    ("sel-rate", "btn-rate", 200, "-p CFG-RATE,200"),
    ("sel-power", "btn-power", 1, "-p PMS,1"),
]


async def test_simple_buttons_run_ubxtool(ubx_calls):
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        for btn_id, _ in SIMPLE_BUTTONS:
            await press(pilot, w, btn_id)
    assert ubx_calls == [expected for _, expected in SIMPLE_BUTTONS]


async def test_select_buttons(ubx_calls):
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        for sel_id, btn_id, value, _ in SELECT_BUTTONS:
            w.query_one(f"#{sel_id}", Select).value = value
            await press(pilot, w, btn_id)
    assert ubx_calls == [expected for *_, expected in SELECT_BUTTONS]


def test_select_blank_sentinel_characterization():
    """``Select.BLANK`` no longer exists in Textual 8; it resolves to ``Widget.BLANK``
    (False), so the ``is not Select.BLANK`` guards in DeviceConfig never trigger."""
    assert Select.BLANK is False
    assert Select.BLANK is not Select.NULL


@pytest.mark.xfail(
    strict=True,
    reason="New finding N6 (not in plan): Select.BLANK guard is wrong on Textual 8 "
    "(Select.NULL), so an unselected 'Set' sends '-p MODEL,Select.NULL' to ubxtool",
)
async def test_select_buttons_without_selection_do_nothing(ubx_calls):
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        for btn_id in ("btn-model", "btn-rate", "btn-power"):
            await press(pilot, w, btn_id)
    assert ubx_calls == []


@pytest.mark.xfail(
    strict=True,
    reason="New finding N6 (not in plan): baud 'Set' with no selection does int(Select.NULL) "
    "and the TypeError crashes the app",
)
async def test_baud_button_without_selection_does_not_crash(monkeypatch):
    monkeypatch.setattr(DeviceConfig, "_set_baud_rate", lambda self, speed: None)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await press(pilot, app.query_one(DeviceConfig), "btn-baud")


async def test_pps_apply_and_disable(ubx_calls):
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        await press(pilot, w, "btn-pps-apply")
        await press(pilot, w, "btn-pps-disable")
        out = log_text(w)
    assert ubx_calls[0] == "-c " + w._build_tp5_cmd(1, 100000, True)
    assert ubx_calls[1] == "-c " + w._build_tp5_cmd(0, 0, False)
    assert "PPS ON: freq=1Hz, pulse=100000us" in out
    assert "PPS OFF" in out


async def test_gnss_toggle(ubx_calls):
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        btn = w.query_one("#btn-gnss-galileo", Button)
        await press(pilot, w, "btn-gnss-galileo")
        assert btn.variant == "success"
        await press(pilot, w, "btn-gnss-galileo")
        assert btn.variant == "default"
    assert ubx_calls == ["-e GALILEO", "-d GALILEO"]


async def test_custom_command(ubx_calls):
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        await press(pilot, w, "btn-run-cmd")  # empty input ignored
        w.query_one("#input-cmd", Input).value = "  -p MON-VER  "
        await press(pilot, w, "btn-run-cmd")
    assert ubx_calls == ["-p MON-VER"]


async def test_clock_buttons_without_gps_time():
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        await press(pilot, w, "btn-set-clock")
        await press(pilot, w, "btn-pps-sync")
        out = log_text(w)
    assert "Error: no GPS time available" in out
    assert "Error: no GPS time available for PPS sync" in out


async def test_pps_sync_without_device(make_gps_data, monkeypatch):
    monkeypatch.setattr(dc.glob, "glob", lambda pat: [])
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data())
        await press(pilot, w, "btn-pps-sync")
        assert "no PPS device found" in log_text(w)


async def test_arm_clock_toggle():
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        btn = w.query_one("#btn-arm-clock", Button)
        await press(pilot, w, "btn-arm-clock")
        assert app._armed_clock_set is True
        assert btn.variant == "success"
        await press(pilot, w, "btn-arm-clock")
        assert app._armed_clock_set is False
        assert str(btn.label) == "Arm Clock Sync"
        out = log_text(w)
    assert "Clock sync ARMED" in out and "Clock sync disarmed" in out


async def test_set_clock_now_uses_clock_sync(make_gps_data, monkeypatch):
    calls = []

    def fake_set_clock(gps_time, last_seen):
        calls.append((gps_time, last_seen))
        return "System clock set to: fake"

    monkeypatch.setattr(dc, "set_clock_from_gps", fake_set_clock)
    data = make_gps_data()
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(data)
        await press(pilot, w, "btn-set-clock")
        assert await wait_for(pilot, lambda: "System clock set to: fake" in log_text(w))
    assert calls == [(data.time, data.last_seen)]


async def test_run_ubxtool_runs_subprocess(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "UBX-MON-VER: ok\n", "warn\n")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w._run_ubxtool("-p MON-VER")
        assert await wait_for(pilot, lambda: "UBX-MON-VER: ok" in log_text(w))
        out = log_text(w)
    assert calls == [["ubxtool", "-P", "18", "-p", "MON-VER"]]
    assert "$ ubxtool -P 18 -p MON-VER" in out
    assert "warn" in out


async def test_run_ubxtool_not_installed(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file", "ubxtool")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w._run_ubxtool("-p MON-VER")
        assert await wait_for(pilot, lambda: "ubxtool not found" in log_text(w))


@pytest.mark.xfail(strict=True, reason="F9: ubxtool args are split with str.split() instead of shlex.split()")
async def test_run_ubxtool_respects_quotes(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w._run_ubxtool('-x "A B"')
        assert await wait_for(pilot, lambda: bool(calls))
    assert calls[0] == ["ubxtool", "-P", "18", "-x", "A B"]


async def test_set_baud_rate(make_gps_data, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data())
        w.query_one("#sel-baud", Select).value = 115200
        await press(pilot, w, "btn-baud")
        assert await wait_for(pilot, lambda: "Baud rate set to 115200" in log_text(w))
    assert calls == [
        ["ubxtool", "-P", "18", "-S", "115200"],
        ["gpsctl", "-s", "115200", "/dev/ttyACM0"],
    ]


# ─── PPS clock sync (ioctl and commands mocked) ──────────────────────


PPS_GPS_TIME = "2026-03-14T12:00:00.250Z"
PPS_BASE = 1773489600  # 2026-03-14T12:00:00Z


def _pps_setup(monkeypatch, results):
    """Mock /dev/pps0, the PPS ioctl, the clock and subprocess.run."""
    now = PPS_BASE + 10.0
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        rc, err = results.get(cmd[0], (0, ""))
        return subprocess.CompletedProcess(cmd, rc, "", err)

    monkeypatch.setattr(dc.subprocess, "run", fake_run)
    monkeypatch.setattr(dc.glob, "glob", lambda pat: ["/dev/pps0"])
    monkeypatch.setattr(dc, "time", type("T", (), {"time": staticmethod(lambda: now)}))
    # Pulse marks GPS second PPS_BASE+1; kernel clock is 2 ms ahead at that edge
    monkeypatch.setattr(
        DeviceConfig, "_wait_for_pps",
        staticmethod(lambda path, timeout_sec=3: (PPS_BASE + 1, 2_000_000, 7)),
    )
    # GPS time .250 received 0.7 s ago -> current GPS time ~ .950 -> target = +1 s
    return calls, dict(time=PPS_GPS_TIME, last_seen=now - 0.7)


async def test_pps_sync_applies_relative_offset(make_gps_data, monkeypatch):
    calls, overrides = _pps_setup(monkeypatch, {})
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data(**overrides))
        await press(pilot, w, "btn-pps-sync")
        assert await wait_for(pilot, lambda: "PPS-disciplined" in log_text(w))
        out = log_text(w)
    assert "PPS pulse #7" in out and "Clock offset: -2.000 ms" in out
    assert calls[0] == ["timedatectl", "set-ntp", "false"]
    assert calls[1][-4:] == ["xbb", "-2000", "true", "true"]


async def test_pps_sync_failure_reports_each_command(make_gps_data, monkeypatch):
    """N5: the final error labels busctl's and sudo's stderr correctly."""
    calls, overrides = _pps_setup(
        monkeypatch, {"busctl": (1, "busctl-err"), "sudo": (1, "sudo-err")},
    )
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data(**overrides))
        await press(pilot, w, "btn-pps-sync")
        assert await wait_for(pilot, lambda: "could not apply PPS offset" in log_text(w))
        out = log_text(w)
    assert "busctl: busctl-err" in out
    assert "sudo: sudo-err" in out
    assert calls[-1] == ["sudo", "-n", "date", "-u", "-s", "2026-03-14 12:00:01"]


async def test_pps_sync_unparseable_time(make_gps_data, monkeypatch):
    monkeypatch.setattr(dc.glob, "glob", lambda pat: ["/dev/pps0"])
    app = Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        w = app.query_one(DeviceConfig)
        w.update_gps_data(make_gps_data(time="garbage"))
        await press(pilot, w, "btn-pps-sync")
        assert "cannot parse GPS time 'garbage'" in log_text(w)
