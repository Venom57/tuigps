"""Tests for tuigps.timeutil.parse_gps_time (F11)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tuigps.timeutil import parse_gps_time

BASE = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-03-14T12:00:00Z", BASE),
        ("2026-03-14T12:00:00", BASE),
        ("2026-03-14 12:00:00Z", BASE),
        ("2026-03-14T12:00:00.250Z", BASE + 0.25),
        ("2026-03-14T12:00:00.5Z", BASE + 0.5),
        ("2026-03-14T12:00:00.123456Z", BASE + 0.123456),
        ("2026-03-14T12:00:00.123456789Z", BASE + 0.123456789),
        ("2026-03-14T12:00:00.250+00:00", BASE + 0.25),
        ("2026-03-14T13:00:00+01:00", BASE),
        ("2026-03-14T11:30:00-0030", BASE),
        ("  2026-03-14T12:00:00Z\n", BASE),
        ("2016-12-31T23:59:60Z", datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp()),
    ],
)
def test_parse_valid(text, expected):
    assert parse_gps_time(text) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize(
    "text",
    ["", "garbage", "2026-13-01T00:00:00Z", "2026-03-14T25:00:00Z", "2026-03-14T12:00Z",
     "2026-03-14T12:00:00.Z", "2026-03-14T12:00:00ZZ", None, float("nan"), 12345],
)
def test_parse_invalid_returns_none(text):
    assert parse_gps_time(text) is None


def test_used_by_all_callers():
    """The three former copy-pasted parsers now share the helper."""
    from tuigps import clock_sync, gpsd_client
    from tuigps.widgets import device_config

    for mod in (clock_sync, gpsd_client, device_config):
        assert mod.parse_gps_time is parse_gps_time
