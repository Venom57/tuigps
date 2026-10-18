"""Tests for tuigps.constants."""

from __future__ import annotations

import pytest

from tuigps.constants import (
    COMPASS_POINTS,
    DOP_RATINGS,
    GNSS_COLORS,
    GNSS_NAMES,
    GNSS_SHORT,
    MODE_COLORS,
    MODE_NAMES,
    STATUS_COLORS,
    STATUS_NAMES,
    bearing_to_compass,
    dop_rating,
)
from tuigps.data_model import FixStatus


def test_gnss_tables_share_keys():
    assert set(GNSS_NAMES) == set(GNSS_SHORT) == set(GNSS_COLORS) == set(range(8))


def test_gnss_names():
    assert GNSS_NAMES[0] == "GPS"
    assert GNSS_NAMES[2] == "Galileo"
    assert GNSS_NAMES[6] == "GLONASS"
    assert all(len(v) == 2 for v in GNSS_SHORT.values())


def test_mode_tables():
    assert set(MODE_NAMES) == set(MODE_COLORS) == {0, 1, 2, 3}
    assert MODE_NAMES[3] == "3D Fix"


def test_status_tables_cover_fix_status_enum():
    assert set(STATUS_NAMES) == set(STATUS_COLORS)
    assert {int(s) for s in FixStatus} == set(STATUS_NAMES)


def test_compass_points():
    assert len(COMPASS_POINTS) == 16
    assert len(set(COMPASS_POINTS)) == 16


@pytest.mark.parametrize(
    "degrees, expected",
    [
        (0.0, "N"),
        (11.24, "N"),
        (11.25, "NNE"),
        (22.5, "NNE"),
        (45.0, "NE"),
        (90.0, "E"),
        (135.0, "SE"),
        (180.0, "S"),
        (225.0, "SW"),
        (270.0, "W"),
        (315.0, "NW"),
        (348.74, "NNW"),
        (348.75, "N"),
        (359.9, "N"),
        (360.0, "N"),
        (720.0, "N"),
    ],
)
def test_bearing_to_compass(degrees, expected):
    assert bearing_to_compass(degrees) == expected


@pytest.mark.parametrize("i", range(16))
def test_bearing_to_compass_every_point(i):
    assert bearing_to_compass(i * 22.5) == COMPASS_POINTS[i]


def test_dop_ratings_are_ascending():
    thresholds = [t for t, _, _ in DOP_RATINGS]
    assert thresholds == sorted(thresholds)


@pytest.mark.parametrize(
    "value, label, color",
    [
        (0.5, "Ideal", "bright_green"),
        (0.99, "Ideal", "bright_green"),
        (1.0, "Excellent", "green"),
        (1.9, "Excellent", "green"),
        (2.0, "Good", "green"),
        (4.99, "Good", "green"),
        (5.0, "Moderate", "yellow"),
        (10.0, "Fair", "orange1"),
        (19.9, "Fair", "orange1"),
        (20.0, "Poor", "red"),
        (99.9, "Poor", "red"),
        (float("inf"), "Poor", "red"),
        (float("nan"), "Poor", "red"),  # callers must check isfinite first
    ],
)
def test_dop_rating(value, label, color):
    assert dop_rating(value) == (label, color)
