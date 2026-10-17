"""Tests for tuigps.clock_sync (subprocess fully mocked)."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import pytest

from tuigps import clock_sync
from tuigps.clock_sync import set_clock_from_gps

GPS_TIME = "2026-03-14T12:00:00.250Z"
GPS_EPOCH = datetime(2026, 3, 14, 12, 0, 0, 250000, tzinfo=timezone.utc).timestamp()


class FakeRun:
    """Records subprocess.run calls; returncode/stdout/stderr per program name."""

    def __init__(self, results=None):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.results = results or {}

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        self.kwargs.append(kwargs)
        key = cmd[0]
        if cmd[0] == "timedatectl":
            key = f"timedatectl {cmd[1]}"
        rc, out, err = self.results.get(key, (0, "", ""))
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def programs(self):
        return [c[0] if c[0] != "timedatectl" else f"timedatectl {c[1]}" for c in self.calls]


@pytest.fixture
def now(monkeypatch):
    """Freeze clock_sync's time.time() at GPS time + 0.5 s."""
    t = {"now": GPS_EPOCH + 0.5}

    class _T:
        @staticmethod
        def time():
            return t["now"]

    monkeypatch.setattr(clock_sync, "time", _T)
    return t


def _install(monkeypatch, **results):
    fake = FakeRun({k.replace("_", " ", 1).replace("_", "-"): v for k, v in results.items()})
    monkeypatch.setattr(clock_sync.subprocess, "run", fake)
    return fake


def test_dbus_success_path(monkeypatch, now):
    fake = _install(monkeypatch)
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)

    assert fake.programs() == ["timedatectl set-ntp", "busctl"]
    assert fake.calls[0] == ["timedatectl", "set-ntp", "false"]
    busctl = fake.calls[1]
    assert busctl[:7] == [
        "busctl", "call", "org.freedesktop.timedate1", "/org/freedesktop/timedate1",
        "org.freedesktop.timedate1", "SetTime", "xbb",
    ]
    # Absolute (relative=false), interactive=true
    assert busctl[8:] == ["false", "true"]
    # Fix age (0.5 s) is added to the GPS time
    assert int(busctl[7]) == int((GPS_EPOCH + 0.5) * 1_000_000)
    assert msg == "System clock set to: 2026-03-14 12:00:00.750000 UTC (fix age: 0.500s)"
    for kw in fake.kwargs:
        assert kw["capture_output"] is True and kw["text"] is True and kw["timeout"] > 0


def test_no_last_seen_means_zero_age(monkeypatch, now):
    fake = _install(monkeypatch)
    msg = set_clock_from_gps(GPS_TIME)
    assert int(fake.calls[1][7]) == int(GPS_EPOCH * 1_000_000)
    assert "fix age: 0.000s" in msg


def test_fix_age_compensation_large(monkeypatch, now):
    now["now"] = GPS_EPOCH + 3.0
    fake = _install(monkeypatch)
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH + 0.75)
    # age = 3.0 - 0.75 = 2.25 s
    assert int(fake.calls[1][7]) == int((GPS_EPOCH + 2.25) * 1_000_000)
    assert "12:00:02.500000" in msg
    assert "fix age: 2.250s" in msg


def test_time_without_fraction(monkeypatch, now):
    fake = _install(monkeypatch)
    set_clock_from_gps("2026-03-14T12:00:00Z")
    expected = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    assert int(fake.calls[1][7]) == int(expected * 1_000_000)


def test_invalid_time_raises(monkeypatch, now):
    fake = _install(monkeypatch)
    with pytest.raises(ValueError):
        set_clock_from_gps("not a time")
    assert fake.calls == []


def test_timedatectl_fallback(monkeypatch, now):
    fake = _install(monkeypatch, busctl=(1, "", "Access denied"))
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert fake.programs() == ["timedatectl set-ntp", "busctl", "timedatectl set-time"]
    local = datetime.fromtimestamp(GPS_EPOCH + 0.5, tz=timezone.utc).astimezone()
    formatted = local.strftime("%Y-%m-%d %H:%M:%S")
    assert fake.calls[2] == ["timedatectl", "set-time", formatted]
    assert msg == f"System clock set to: {formatted} (local, fix age: 0.500s)"


def test_sudo_fallback(monkeypatch, now):
    fake = _install(
        monkeypatch,
        busctl=(1, "", "Access denied"),
        timedatectl_set_time=(1, "", "Failed to set time"),
        sudo=(0, "Sat Mar 14 12:00:00 UTC 2026\n", ""),
    )
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert fake.programs() == ["timedatectl set-ntp", "busctl", "timedatectl set-time", "sudo"]
    assert fake.calls[3] == ["sudo", "-n", "date", "-u", "-s", "2026-03-14 12:00:00.750000"]
    assert msg == "System clock set to: Sat Mar 14 12:00:00 UTC 2026"


def test_total_failure_message(monkeypatch, now):
    monkeypatch.setenv("USER", "alice")
    _install(
        monkeypatch,
        busctl=(1, "", "busctl-err"),
        timedatectl_set_time=(1, "", "timedatectl-err"),
        sudo=(1, "", "sudo-err"),
    )
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert msg.startswith("Error: could not set time.")
    assert "set-ntp false" in msg
    assert "alice ALL=(ALL) NOPASSWD: /usr/bin/date" in msg


def test_total_failure_reports_each_methods_stderr(monkeypatch, now):
    """N5: each fallback's own stderr is shown under its own label."""
    _install(
        monkeypatch,
        busctl=(1, "", "busctl-err"),
        timedatectl_set_time=(1, "", "timedatectl-err"),
        sudo=(1, "", "sudo-err"),
    )
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert "busctl: busctl-err" in msg
    assert "timedatectl: timedatectl-err" in msg
    assert "sudo: sudo-err" in msg


def test_nanosecond_gps_time(monkeypatch, now):
    """F11: gpsd may send 9 fractional digits, which strptime's %f rejects."""
    fake = _install(monkeypatch)
    set_clock_from_gps("2026-03-14T12:00:00.250000000Z")
    assert int(fake.calls[1][7]) == int(GPS_EPOCH * 1_000_000)


# ─── NTP is restored when setting the clock fails ───────────────────────


ALL_FAIL = dict(
    busctl=(1, "", "busctl-err"),
    timedatectl_set_time=(1, "", "timedatectl-err"),
    sudo=(1, "", "sudo-err"),
)


@pytest.fixture
def ntp_was(monkeypatch):
    """Set what ``ntp_enabled()`` reports before the clock is set."""

    def _set(state):
        monkeypatch.setattr(clock_sync, "ntp_enabled", lambda: state)

    return _set


def test_failed_set_restores_ntp_that_was_on(monkeypatch, now, ntp_was):
    ntp_was(True)
    fake = _install(monkeypatch, **ALL_FAIL)
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert fake.calls[0] == ["timedatectl", "set-ntp", "false"]
    assert fake.calls[-1] == ["timedatectl", "set-ntp", "true"]
    assert msg.startswith("Error: could not set time.")
    assert msg.endswith("NTP was re-enabled (it was on before the attempt)")


@pytest.mark.parametrize("state", [False, None])
def test_failed_set_leaves_ntp_off_when_it_was_not_on(monkeypatch, now, ntp_was, state):
    """NTP that was off (or in an unknown state) is not turned on by a failure."""
    ntp_was(state)
    fake = _install(monkeypatch, **ALL_FAIL)
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert ["timedatectl", "set-ntp", "true"] not in fake.calls
    assert "NTP was" not in msg


def test_successful_set_keeps_ntp_off(monkeypatch, now, ntp_was):
    """After a successful set NTP stays off (it would undo the change)."""
    ntp_was(True)
    fake = _install(monkeypatch)
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert fake.programs() == ["timedatectl set-ntp", "busctl"]
    assert fake.calls[0][-1] == "false"
    assert msg.startswith("System clock set to:")


def test_failed_restore_is_reported(monkeypatch, now, ntp_was):
    ntp_was(True)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["timedatectl", "set-ntp", "false"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "denied")

    monkeypatch.setattr(clock_sync.subprocess, "run", run)
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert calls[-1] == ["timedatectl", "set-ntp", "true"]
    assert msg.endswith(
        "NTP was on before and could not be re-enabled: "
        "Error: timedatectl set-ntp true failed: denied"
    )


# ─── A missing program or a timeout is a failed method, not a crash ─────


def _raising_run(monkeypatch, raises: dict):
    """subprocess.run that raises ``raises[program]`` and otherwise succeeds."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        exc = raises.get(cmd[0] if cmd[0] != "timedatectl" else " ".join(cmd[:2]))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(clock_sync.subprocess, "run", run)
    return calls


def test_missing_busctl_falls_back_to_timedatectl(monkeypatch, now):
    calls = _raising_run(monkeypatch, {"busctl": FileNotFoundError(2, "No such file")})
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert [c[0] for c in calls] == ["timedatectl", "busctl", "timedatectl"]
    assert calls[2][1] == "set-time"
    assert "(local, fix age: 0.500s)" in msg


def test_every_method_missing_or_hanging(monkeypatch, now, ntp_was):
    ntp_was(True)
    _raising_run(monkeypatch, {
        "busctl": FileNotFoundError(2, "No such file"),
        "timedatectl set-time": subprocess.TimeoutExpired(["timedatectl"], 10),
        "sudo": FileNotFoundError(2, "No such file"),
    })
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert "busctl: busctl: command not found" in msg
    assert "timedatectl: timedatectl: timed out after 10s" in msg
    assert "sudo: sudo: command not found" in msg
    assert msg.endswith("NTP was re-enabled (it was on before the attempt)")


def test_missing_timedatectl_does_not_prevent_setting_the_clock(monkeypatch, now):
    calls = _raising_run(
        monkeypatch, {"timedatectl set-ntp": FileNotFoundError(2, "No such file")}
    )
    msg = set_clock_from_gps(GPS_TIME, fix_received=GPS_EPOCH)
    assert calls[1][0] == "busctl"
    assert msg.startswith("System clock set to:")


@pytest.mark.parametrize("state, expected", [(True, True), (False, False), (None, False)])
def test_disable_ntp_reports_prior_state(monkeypatch, ntp_was, state, expected):
    ntp_was(state)
    fake = _install(monkeypatch)
    assert clock_sync.disable_ntp() is expected
    assert fake.calls == [["timedatectl", "set-ntp", "false"]]


def test_restore_ntp_does_nothing_when_it_was_off(monkeypatch):
    fake = _install(monkeypatch)
    assert clock_sync.restore_ntp(False) == ""
    assert fake.calls == []
