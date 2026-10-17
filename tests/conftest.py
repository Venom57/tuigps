"""Shared test fixtures.

The system ``python3-gps`` package is not pip-installable and is usually not
present in a dev venv, but ``tuigps.gpsd_client`` does ``import gps`` at import
time. We install a small fake ``gps`` module into ``sys.modules`` *before* any
``tuigps`` module is imported so the whole package (and the app) imports
cleanly. The fake module's ``gps`` constructor refuses to connect by default;
tests can install a session factory via the ``fake_gps`` fixture.
"""

from __future__ import annotations

import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

import pytest

NAN = float("nan")


# ─── Fake python-gps session ────────────────────────────────────────────


def make_fix(**overrides: Any) -> types.SimpleNamespace:
    """Build an object mimicking ``gps.gpsfix`` (NaN for unknown values)."""
    fields: dict[str, Any] = {
        "latitude": NAN,
        "longitude": NAN,
        "altHAE": NAN,
        "altMSL": NAN,
        "geoidSep": NAN,
        "speed": NAN,
        "track": NAN,
        "climb": NAN,
        "mode": 0,
        "status": 0,
        "time": NAN,  # python-gps uses NaN until a time string is received
        "magtrack": NAN,
        "magvar": NAN,
        "eph": NAN,
        "epv": NAN,
        "ept": NAN,
        "eps": NAN,
        "epd": NAN,
        "epc": NAN,
        "epx": NAN,
        "epy": NAN,
        "sep": NAN,
        "ecefx": NAN,
        "ecefy": NAN,
        "ecefz": NAN,
        "ecefvx": NAN,
        "ecefvy": NAN,
        "ecefvz": NAN,
    }
    fields.update(overrides)
    return types.SimpleNamespace(**fields)


class FakeGpsSession:
    """Mimics the attributes of a python-gps ``gps.gps`` session object.

    ``messages`` is a list of dicts. Each ``read()`` pops one dict and applies
    it to the session: the ``"fix"`` key (a dict) updates ``self.fix``, every
    other key sets the attribute of the same name (``data``, ``response``,
    ``utc``, ``hdop`` ...). When the script is exhausted ``read()`` returns -1
    (gpsd disconnected), like the real library.

    ``waiting_script`` optionally provides the sequence of ``waiting()``
    results; once exhausted ``waiting()`` returns True.
    """

    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        waiting_script: list[bool] | None = None,
        **attrs: Any,
    ) -> None:
        self.fix = make_fix()
        self.data: dict[str, Any] = {}
        self.utc = ""
        self.response = ""
        # DOPs (set by SKY)
        self.hdop = NAN
        self.vdop = NAN
        self.pdop = NAN
        self.gdop = NAN
        self.tdop = NAN
        self.xdop = NAN
        self.ydop = NAN
        # PPS
        self.real_sec = NAN
        self.real_nsec = NAN
        self.clock_sec = NAN
        self.clock_nsec = NAN
        self.precision = 0
        # DEVICE
        self.path = ""
        self.gps_id = ""
        self.baudrate = 0
        self.cycle = NAN
        self.mincycle = NAN
        # VERSION
        self.version = None

        self.closed = False
        self.read_count = 0
        self._messages = list(messages or [])
        self._waiting = list(waiting_script or [])
        self.apply(attrs)

    def apply(self, msg: dict[str, Any]) -> None:
        for key, value in msg.items():
            if key == "fix":
                for fk, fv in value.items():
                    setattr(self.fix, fk, fv)
            else:
                setattr(self, key, value)

    def waiting(self, timeout: float = 0) -> bool:
        if self._waiting:
            return self._waiting.pop(0)
        return True

    def read(self) -> int:
        self.read_count += 1
        if not self._messages:
            return -1
        self.apply(self._messages.pop(0))
        return 0

    def close(self) -> None:
        self.closed = True


# ─── Fake ``gps`` module ────────────────────────────────────────────────


class _FakeGpsConstructor:
    """Stand-in for ``gps.gps``.

    Calling it records the arguments and delegates to ``factory``. When no
    factory is installed it raises ``ConnectionRefusedError`` like the real
    library does when gpsd is not running.
    """

    factory: Callable[..., Any] | None = None
    calls: list[dict[str, Any]] = []

    def __new__(cls, host: str = "127.0.0.1", port: str = "2947", mode: int = 0, **kwargs: Any):
        cls.calls.append({"host": host, "port": port, "mode": mode, **kwargs})
        if cls.factory is None:
            raise ConnectionRefusedError(111, "Connection refused (fake gps)")
        return cls.factory(host=host, port=port, mode=mode, **kwargs)


def _build_fake_gps_module() -> types.ModuleType:
    mod = types.ModuleType("gps")
    mod.__doc__ = "Fake python3-gps module for tests"
    # Values match gps/watch_options.py in gpsd.
    mod.WATCH_ENABLE = 0x000001
    mod.WATCH_DISABLE = 0x000002
    mod.WATCH_JSON = 0x000010
    mod.WATCH_NMEA = 0x000020
    mod.WATCH_RARE = 0x000040
    mod.WATCH_RAW = 0x000080
    mod.WATCH_SCALED = 0x000100
    mod.WATCH_TIMING = 0x000200
    mod.WATCH_DEVICE = 0x000800
    mod.WATCH_PPS = 0x002000
    mod.gps = _FakeGpsConstructor
    mod.FakeGpsSession = FakeGpsSession
    mod.__fake__ = True
    return mod


FAKE_GPS_MODULE = _build_fake_gps_module()
# Always override, even if a real python3-gps happens to be importable.
sys.modules["gps"] = FAKE_GPS_MODULE


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_fake_gps():
    """Reset the fake gps constructor state between tests."""
    _FakeGpsConstructor.factory = None
    _FakeGpsConstructor.calls = []
    yield
    _FakeGpsConstructor.factory = None
    _FakeGpsConstructor.calls = []


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Safety net: never run real commands (timedatectl, busctl, sudo, ubxtool)."""

    def _refuse(cmd, *args, **kwargs):
        raise AssertionError(f"unexpected subprocess.run in tests: {cmd!r}")

    monkeypatch.setattr(subprocess, "run", _refuse)


@pytest.fixture
def fake_gps():
    """The fake ``gps`` module. Set ``fake_gps.gps.factory`` to allow connects."""
    return FAKE_GPS_MODULE


@pytest.fixture
def fake_session() -> Callable[..., FakeGpsSession]:
    """Factory for FakeGpsSession objects."""
    return FakeGpsSession


@pytest.fixture
def tpv_session() -> Callable[..., FakeGpsSession]:
    """Factory for a session positioned on a full 3D-fix TPV message."""

    def _make(**fix_overrides: Any) -> FakeGpsSession:
        fix = dict(
            latitude=47.6062,
            longitude=-122.3321,
            altHAE=30.5,
            altMSL=50.25,
            geoidSep=-19.75,
            speed=1.5,
            track=271.0,
            climb=0.25,
            mode=3,
            status=2,
            time="2026-03-14T12:00:00.250Z",
            magtrack=255.0,
            magvar=15.3,
            eph=3.1,
            epv=4.2,
            ept=0.005,
            eps=0.6,
            epd=12.0,
            epc=0.8,
            epx=2.1,
            epy=2.2,
            sep=5.3,
            ecefx=-2304000.1,
            ecefy=-3638000.2,
            ecefz=4688000.3,
            ecefvx=0.1,
            ecefvy=0.2,
            ecefvz=0.3,
        )
        fix.update(fix_overrides)
        return FakeGpsSession(
            fix=fix,
            data={"class": "TPV", "leapseconds": 18},
            utc="2026-03-14T12:00:00.250Z",
        )

    return _make


def sample_satellites():
    from tuigps.data_model import SatelliteInfo

    return [
        SatelliteInfo(prn=5, gnssid=0, svid=5, elevation=65.0, azimuth=45.0, snr=42.0, used=True),
        SatelliteInfo(prn=12, gnssid=0, svid=12, elevation=30.0, azimuth=180.0, snr=35.0, used=True),
        SatelliteInfo(prn=25, gnssid=0, svid=25, elevation=10.0, azimuth=300.0, snr=18.0, used=False),
        SatelliteInfo(prn=211, gnssid=2, svid=11, elevation=50.0, azimuth=90.0, snr=38.0, used=True),
        SatelliteInfo(prn=72, gnssid=6, svid=8, elevation=20.0, azimuth=270.0, snr=25.0, used=False,
                      health=2, freqid=3),
        SatelliteInfo(prn=133, gnssid=1, svid=133, elevation=-5.0, azimuth=200.0, snr=0.0, used=False),
        SatelliteInfo(prn=99, gnssid=3, svid=19),  # no el/az/snr (NaN)
    ]


@pytest.fixture
def make_gps_data():
    """Factory for a fully populated GPSData with a fresh 3D fix + satellites."""
    from tuigps.data_model import (
        DeviceInfo,
        DOPValues,
        ErrorEstimates,
        GPSData,
        PPSData,
        TOFFData,
        VersionInfo,
    )

    def _make(**overrides: Any) -> GPSData:
        sats = sample_satellites()
        now = time.time()
        d = GPSData(
            connected=True,
            last_seen=now,
            tpv_seq=1,
            tpv_received=now,
            latitude=47.6062,
            longitude=-122.3321,
            alt_hae=30.5,
            alt_msl=50.25,
            geoid_sep=-19.75,
            speed=1.5,
            track=271.0,
            climb=0.25,
            mode=3,
            status=2,
            time="2026-03-14T12:00:00.250Z",
            magtrack=255.0,
            magvar=-15.3,
            leapseconds=18,
            ecefx=-2304000.1,
            ecefy=-3638000.2,
            ecefz=4688000.3,
            dop=DOPValues(hdop=0.9, vdop=1.4, pdop=1.7, gdop=2.1, tdop=0.8, xdop=0.6, ydop=0.7),
            errors=ErrorEstimates(eph=3.1, epv=4.2, ept=0.005, eps=0.6, epd=12.0, epc=0.8,
                                  epx=2.1, epy=2.2, sep=5.3),
            pps=PPSData(real_sec=1773489600.0, real_nsec=0.0, clock_sec=1773489600.0,
                        clock_nsec=1500.0, precision=-20, qerr=12.5),
            toff=TOFFData(real_sec=1773489600.0, real_nsec=250e6, clock_sec=1773489600.0,
                          clock_nsec=262e6),
            device=DeviceInfo(path="/dev/ttyACM0", driver="u-blox", subtype="SW ROM CORE 3.01",
                              bps=9600, cycle=1.0, mincycle=0.25, activated="2026-03-14T11:00:00Z",
                              native=1),
            version=VersionInfo(release="3.25", proto_major=3, proto_minor=15),
            toff_samples=[-0.012, -0.010, -0.011, -0.013],
            satellites=sats,
            satellites_used=sum(1 for s in sats if s.used),
        )
        for key, value in overrides.items():
            setattr(d, key, value)
        return d

    return _make


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent
