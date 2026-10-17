"""Pure (non-UI) tests for tuigps.widgets.device_config helpers."""

from __future__ import annotations

import ctypes
import struct

import pytest

from tuigps.widgets import device_config as dc
from tuigps.widgets.device_config import DeviceConfig, _iowr, _PPSFData

TP5_FMT = "<BBHhhIIIIiI"


def _decode_tp5(cmd: str):
    parts = cmd.split(",")
    assert parts[:2] == ["06", "31"]
    assert all(len(p) == 2 for p in parts)
    payload = bytes(int(p, 16) for p in parts[2:])
    assert len(payload) == 32 == struct.calcsize(TP5_FMT)
    return struct.unpack(TP5_FMT, payload)


def test_tp5_defaults_active():
    fields = _decode_tp5(DeviceConfig()._build_tp5_cmd())
    tp_idx, version, _res, ant_delay, rf_delay, freq, freq_lock, pulse, pulse_lock, user_delay, flags = fields
    assert (tp_idx, version, ant_delay, rf_delay, user_delay) == (0, 1, 0, 0, 0)
    assert (freq, freq_lock) == (1, 1)
    assert (pulse, pulse_lock) == (100000, 100000)
    assert flags == 0x7F


def test_tp5_custom_values_little_endian():
    cmd = DeviceConfig()._build_tp5_cmd(freq_hz=10000, pulse_us=50, active=True)
    fields = _decode_tp5(cmd)
    assert fields[5] == fields[6] == 10000
    assert fields[7] == fields[8] == 50
    parts = cmd.split(",")
    # freqPeriod starts at payload offset 8 -> parts index 10; 10000 = 0x2710 LE
    assert parts[10:14] == ["10", "27", "00", "00"]


def test_tp5_inactive_clears_active_bit():
    fields = _decode_tp5(DeviceConfig()._build_tp5_cmd(freq_hz=0, pulse_us=0, active=False))
    assert fields[-1] == 0x7E
    assert fields[5] == 0 and fields[7] == 0


def test_tp5_exact_string():
    assert DeviceConfig()._build_tp5_cmd(1, 100000, True) == (
        "06,31,00,01,00,00,00,00,00,00,01,00,00,00,01,00,00,00,"
        "a0,86,01,00,a0,86,01,00,00,00,00,00,7f,00,00,00"
    )


def test_iowr_encoding():
    # dir=3 (read|write) << 30 | size << 16 | type << 8 | nr
    assert _iowr("p", 0xA4, 64) == 0xC04070A4
    assert _iowr("p", 0xA4, 8) == 0xC00870A4
    assert _iowr("a", 1, 0) == (3 << 30) | (ord("a") << 8) | 1


def test_pps_struct_layout():
    assert ctypes.sizeof(dc._PPSKTime) == 16
    assert ctypes.sizeof(dc._PPSKInfo) == 48
    assert ctypes.sizeof(_PPSFData) == 64


def test_pps_fetch_current_value():
    """Characterization: current code encodes sizeof(struct pps_fdata)."""
    assert dc._PPS_FETCH == _iowr("p", 0xA4, ctypes.sizeof(_PPSFData))


@pytest.mark.xfail(
    strict=True,
    reason="New finding N2 (not in plan): linux/pps.h defines PPS_FETCH as "
    "_IOWR('p', 0xa4, struct pps_fdata *) i.e. pointer size, so the ioctl number is wrong",
)
def test_pps_fetch_matches_kernel_header():
    assert dc._PPS_FETCH == _iowr("p", 0xA4, ctypes.sizeof(ctypes.c_void_p))


def test_find_pps_device(monkeypatch):
    monkeypatch.setattr(dc.glob, "glob", lambda pat: ["/dev/pps1", "/dev/pps0"])
    assert DeviceConfig._find_pps_device() == "/dev/pps0"
    monkeypatch.setattr(dc.glob, "glob", lambda pat: [])
    assert DeviceConfig._find_pps_device() is None


class _FakeOS:
    O_RDONLY = 0

    def __init__(self, open_error=None):
        self.open_error = open_error
        self.closed = []

    def open(self, path, flags):
        if self.open_error:
            raise self.open_error
        return 42

    def close(self, fd):
        self.closed.append(fd)


def test_wait_for_pps_success(monkeypatch):
    fake_os = _FakeOS()
    captured = {}

    def fake_ioctl(fd, req, fdata):
        captured.update(fd=fd, req=req, timeout=(fdata.timeout.sec, fdata.timeout.flags))
        fdata.info.assert_tu.sec = 1773489600
        fdata.info.assert_tu.nsec = 123
        fdata.info.assert_sequence = 7
        return 0

    monkeypatch.setattr(dc, "os", fake_os)
    monkeypatch.setattr(dc.fcntl, "ioctl", fake_ioctl)
    assert DeviceConfig._wait_for_pps("/dev/pps0", timeout_sec=3) == (1773489600, 123, 7)
    assert captured["fd"] == 42 and captured["req"] == dc._PPS_FETCH
    assert captured["timeout"][0] == 3
    assert fake_os.closed == [42]


def test_wait_for_pps_ioctl_error(monkeypatch):
    fake_os = _FakeOS()

    def fake_ioctl(fd, req, fdata):
        raise OSError(25, "Inappropriate ioctl for device")

    monkeypatch.setattr(dc, "os", fake_os)
    monkeypatch.setattr(dc.fcntl, "ioctl", fake_ioctl)
    assert DeviceConfig._wait_for_pps("/dev/pps0") is None
    assert fake_os.closed == [42]


def test_wait_for_pps_open_error(monkeypatch):
    fake_os = _FakeOS(open_error=PermissionError(13, "denied"))
    monkeypatch.setattr(dc, "os", fake_os)
    assert DeviceConfig._wait_for_pps("/dev/pps0") is None
    assert fake_os.closed == []


@pytest.mark.xfail(
    strict=True,
    reason="New finding N3 (not in plan): timeout.flags=0x01 is PPS_TIME_INVALID, which makes "
    "the kernel ignore the timeout and block forever when no pulse arrives",
)
def test_wait_for_pps_timeout_is_honoured(monkeypatch):
    seen = {}

    def fake_ioctl(fd, req, fdata):
        seen["flags"] = fdata.timeout.flags
        raise OSError(110, "timeout")

    monkeypatch.setattr(dc, "os", _FakeOS())
    monkeypatch.setattr(dc.fcntl, "ioctl", fake_ioctl)
    DeviceConfig._wait_for_pps("/dev/pps0", timeout_sec=3)
    PPS_TIME_INVALID = 1 << 0
    assert not seen["flags"] & PPS_TIME_INVALID


def test_option_tables_are_sane():
    for table in (dc.PLATFORM_MODELS, dc.NAV_RATES, dc.POWER_MODES, dc.PPS_FREQUENCIES,
                  dc.PPS_DURATIONS, dc.BAUD_RATES):
        values = [v for v, _ in table]
        assert len(values) == len(set(values))
    assert dc.CONSTELLATIONS == ["GPS", "GLONASS", "GALILEO", "BEIDOU", "SBAS", "QZSS"]


def test_update_gps_data_tracks_device_path(make_gps_data):
    w = DeviceConfig()
    w.update_gps_data(make_gps_data())
    assert w._device_path == "/dev/ttyACM0"
    d = make_gps_data()
    d.device.path = ""
    w.update_gps_data(d)
    assert w._device_path == "/dev/ttyACM0"  # keeps last known path
