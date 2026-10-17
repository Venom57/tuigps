"""Tests for tuigps.formatting (NaN-safe display helpers)."""

from __future__ import annotations

import pytest

from tuigps.formatting import fmt, fmt_altitude, fmt_coord, fmt_speed, fmt_time_iso

NAN = float("nan")
INF = float("inf")


class TestFmt:
    def test_default_spec(self):
        assert fmt(1.234) == "1.2"

    def test_spec_and_suffix(self):
        assert fmt(3.14159, ".3f", " rad") == "3.142 rad"

    @pytest.mark.parametrize("value", [NAN, INF, -INF])
    def test_non_finite_returns_na(self, value):
        assert fmt(value) == "---"
        assert fmt(value, na="n/a") == "n/a"

    def test_negative_and_zero(self):
        assert fmt(-0.05, ".2f") == "-0.05"
        assert fmt(0.0, ".0f", "m") == "0m"


class TestFmtCoord:
    @pytest.mark.parametrize(
        "value, axis, expected",
        [
            (47.6062, "lat", "47.606200° N"),
            (-33.8688, "lat", "33.868800° S"),
            (-122.3321, "lon", "122.332100° W"),
            (151.2093, "lon", "151.209300° E"),
            (0.0, "lat", "0.000000° N"),
            (0.0, "lon", "0.000000° E"),
        ],
    )
    def test_decimal_degrees(self, value, axis, expected):
        assert fmt_coord(value, axis, "dd") == expected

    def test_default_style_is_dd(self):
        assert fmt_coord(10.5) == "10.500000° N"

    def test_dms(self):
        # 47.6062 = 47 deg, 36.372 min = 36 min 22.32 sec
        assert fmt_coord(47.6062, "lat", "dms") == "47°36'22.32\"N"
        assert fmt_coord(-122.3321, "lon", "dms") == "122°19'55.56\"W"

    def test_ddm(self):
        assert fmt_coord(47.6062, "lat", "ddm") == "47°36.37200'N"
        assert fmt_coord(-0.5, "lon", "ddm") == "0°30.00000'W"

    def test_unknown_style_falls_back_to_dd(self):
        assert fmt_coord(1.25, "lat", "bogus") == "1.250000° N"

    @pytest.mark.parametrize("style", ["dd", "dms", "ddm"])
    @pytest.mark.parametrize("value", [NAN, INF])
    def test_non_finite(self, value, style):
        assert fmt_coord(value, "lat", style) == "---"

    def test_dms_rounding_never_shows_60_seconds(self):
        """N4: rounding carries into minutes and degrees."""
        assert fmt_coord(10.9999999, "lat", "dms") == "11\u00b000'00.00\"N"
        assert fmt_coord(10.99999999, "lat", "ddm") == "11\u00b000.00000'N"
        # 10°59'59.999" -> carries into the minute
        assert fmt_coord(10 + 30 / 60 + 59.999 / 3600, "lat", "dms") == "10\u00b031'00.00\"N"
        assert fmt_coord(-(10 + 59.9999999 / 60), "lon", "ddm") == "11\u00b000.00000'W"

    def test_dms_ddm_fields_in_range(self):
        for i in range(2000):
            value = i * 0.0123457 + 1e-9 * (i % 7)
            dms = fmt_coord(value, "lat", "dms")
            minutes, seconds = dms.split("\u00b0")[1].split("'")
            assert 0 <= int(minutes) < 60 and 0 <= float(seconds.rstrip('"N')) < 60, dms
            ddm = fmt_coord(value, "lat", "ddm")
            assert 0 <= float(ddm.split("\u00b0")[1].rstrip("'N")) < 60, ddm


class TestFmtSpeed:
    @pytest.mark.parametrize(
        "unit, expected",
        [
            ("metric", "36.0 km/h"),
            ("imperial", "22.4 mph"),
            ("nautical", "19.4 kn"),
            ("other", "10.0 m/s"),
        ],
    )
    def test_units(self, unit, expected):
        assert fmt_speed(10.0, unit) == expected

    def test_default_metric(self):
        assert fmt_speed(1.0) == "3.6 km/h"

    def test_nan(self):
        assert fmt_speed(NAN, "imperial") == "---"


class TestFmtAltitude:
    @pytest.mark.parametrize(
        "unit, expected",
        [("metric", "100.0 m"), ("imperial", "328.1 ft"), ("nautical", "328.1 ft"), ("x", "100.0 m")],
    )
    def test_units(self, unit, expected):
        assert fmt_altitude(100.0, unit) == expected

    def test_nan(self):
        assert fmt_altitude(NAN) == "---"


class TestFmtTimeIso:
    def test_full_timestamp(self):
        assert fmt_time_iso("2026-03-14T12:00:00.000Z") == ("2026-03-14", "12:00:00.000")

    def test_space_separated(self):
        assert fmt_time_iso("2026-03-14 01:02:03") == ("2026-03-14", "01:02:03")

    def test_empty(self):
        assert fmt_time_iso("") == ("---", "---")

    def test_date_only(self):
        assert fmt_time_iso("2026-03-14") == ("2026-03-14", "---")

    def test_without_fraction(self):
        assert fmt_time_iso("2026-03-14T23:59:59Z") == ("2026-03-14", "23:59:59")
