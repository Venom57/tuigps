"""Tests for tuigps.data_model."""

from __future__ import annotations

import dataclasses
import math

import pytest

from tuigps.data_model import (
    DeviceInfo,
    DOPValues,
    ErrorEstimates,
    FixMode,
    FixStatus,
    GPSData,
    PPSData,
    SatelliteInfo,
    TOFFData,
    VersionInfo,
)


def _all_nan(obj) -> bool:
    return all(
        math.isnan(getattr(obj, f.name))
        for f in dataclasses.fields(obj)
        if isinstance(getattr(obj, f.name), float)
    )


def test_enums():
    assert FixMode.NO_FIX == 1 and FixMode.FIX_2D == 2 and FixMode.FIX_3D == 3
    assert FixStatus.UNKNOWN == 0 and FixStatus.PPS_FIX == 9
    assert len(FixStatus) == 10


@pytest.mark.parametrize("cls", [DOPValues, ErrorEstimates, TOFFData])
def test_composite_defaults_are_nan(cls):
    assert _all_nan(cls())


def test_pps_defaults():
    p = PPSData()
    assert math.isnan(p.real_sec) and math.isnan(p.qerr)
    assert p.precision == 0


def test_device_and_version_defaults():
    dev = DeviceInfo()
    assert dev.path == "" and dev.bps == 0 and math.isnan(dev.cycle)
    v = VersionInfo()
    assert v.release == "" and v.proto_major == 0


def test_satellite_defaults():
    s = SatelliteInfo()
    assert s.prn == 0 and s.freqid == -1 and not s.used
    assert math.isnan(s.elevation) and math.isnan(s.snr)


def test_gpsdata_defaults():
    d = GPSData()
    assert not d.connected
    assert d.last_seen == 0.0
    assert d.mode == 0
    assert math.isnan(d.latitude) and math.isnan(d.alt_msl)
    assert d.satellites == [] and d.toff_samples == []
    assert math.isnan(d.toff_armed_offset)
    assert not d.has_fix
    assert d.constellation_counts == {}


def test_gpsdata_default_factories_are_independent():
    a, b = GPSData(), GPSData()
    a.satellites.append(SatelliteInfo())
    a.toff_samples.append(1.0)
    a.dop.hdop = 1.0
    assert b.satellites == [] and b.toff_samples == []
    assert math.isnan(b.dop.hdop)


@pytest.mark.parametrize(
    "mode, lat, lon, expected",
    [
        (3, 1.0, 2.0, True),
        (2, 1.0, 2.0, True),
        (1, 1.0, 2.0, False),
        (0, 1.0, 2.0, False),
        (3, float("nan"), 2.0, False),
        (3, 1.0, float("nan"), False),
        (3, 0.0, 0.0, True),
    ],
)
def test_has_fix(mode, lat, lon, expected):
    assert GPSData(mode=mode, latitude=lat, longitude=lon).has_fix is expected


def test_constellation_counts():
    sats = [
        SatelliteInfo(gnssid=0, used=True),
        SatelliteInfo(gnssid=0, used=False),
        SatelliteInfo(gnssid=0, used=True),
        SatelliteInfo(gnssid=2, used=False),
        SatelliteInfo(gnssid=6, used=True),
    ]
    assert GPSData(satellites=sats).constellation_counts == {0: (3, 2), 2: (1, 0), 6: (1, 1)}


def test_constellation_counts_fixture(make_gps_data):
    counts = make_gps_data().constellation_counts
    assert counts[0] == (3, 2)
    assert counts[2] == (1, 1)
    assert sum(v for v, _ in counts.values()) == 7


def test_pps_offset_us():
    d = GPSData(pps=PPSData(real_sec=100.0, real_nsec=500.0, clock_sec=100.0, clock_nsec=0.0))
    assert d.pps_offset_us == pytest.approx(0.5)
    d = GPSData(pps=PPSData(real_sec=101.0, real_nsec=0.0, clock_sec=100.0, clock_nsec=999_000_000.0))
    assert d.pps_offset_us == pytest.approx(1000.0)
    d = GPSData(pps=PPSData(real_sec=100.0, real_nsec=0.0, clock_sec=100.0, clock_nsec=2000.0))
    assert d.pps_offset_us == pytest.approx(-2.0)


def test_pps_offset_us_nan_when_missing():
    assert math.isnan(GPSData().pps_offset_us)
    assert math.isnan(GPSData(pps=PPSData(real_sec=1.0)).pps_offset_us)
    assert math.isnan(GPSData(pps=PPSData(clock_sec=1.0)).pps_offset_us)
