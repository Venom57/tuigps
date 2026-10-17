"""Shared test fixtures.

No test may talk to a real gpsd: the client's default port is pointed at a
local port that refuses connections (a bound, non-listening socket), and
end-to-end tests use the scriptable fake gpsd server in ``fake_gpsd.py``.
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.fake_gpsd import FakeGpsd

NAN = float("nan")


# ─── gpsd endpoints ─────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def refused_port():
    """A local TCP port that refuses connections for the whole session."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))  # bound but never listening -> ECONNREFUSED
    yield sock.getsockname()[1]
    sock.close()


@pytest.fixture(autouse=True)
def _no_real_gpsd(monkeypatch, refused_port):
    """Never connect to a real gpsd on 127.0.0.1:2947 from tests."""
    from tuigps import gpsd_client

    monkeypatch.setattr(gpsd_client, "DEFAULT_HOST", "127.0.0.1")
    monkeypatch.setattr(gpsd_client, "DEFAULT_PORT", str(refused_port))


@pytest.fixture
def fake_gpsd():
    """A running fake gpsd server (see tests/fake_gpsd.py)."""
    server = FakeGpsd()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Safety net: never run real commands (timedatectl, busctl, sudo, ubxtool)."""

    def _refuse(cmd, *args, **kwargs):
        raise AssertionError(f"unexpected subprocess.run in tests: {cmd!r}")

    monkeypatch.setattr(subprocess, "run", _refuse)


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
            connection_state="connected",
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
