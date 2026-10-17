"""Tests for tuigps.position_hold (Welford running statistics + CEP)."""

from __future__ import annotations

import math
import random
import statistics

import pytest

from tuigps import position_hold as ph
from tuigps.position_hold import M_PER_DEG_LAT, HoldResult, PositionHold, m_per_deg_lon

NAN = float("nan")


@pytest.fixture
def clock(monkeypatch):
    """Controllable time.time() for the position_hold module."""
    now = {"t": 1000.0}

    class _T:
        @staticmethod
        def time():
            return now["t"]

    monkeypatch.setattr(ph, "time", _T)
    return now


def _samples(n=50, seed=1):
    rng = random.Random(seed)
    lats = [47.6062 + rng.gauss(0, 2e-5) for _ in range(n)]
    lons = [-122.3321 + rng.gauss(0, 3e-5) for _ in range(n)]
    alts = [50.0 + rng.gauss(0, 2.0) for _ in range(n)]
    return lats, lons, alts


def test_m_per_deg_lon():
    assert m_per_deg_lon(0.0) == pytest.approx(111320.0)
    assert m_per_deg_lon(60.0) == pytest.approx(55660.0)
    assert m_per_deg_lon(90.0) == pytest.approx(0.0, abs=1e-9)


def test_initial_state():
    h = PositionHold()
    assert not h.is_active
    assert h.fix_count == 0
    r = h.result
    assert isinstance(r, HoldResult)
    assert r.fix_count == 0 and r.duration == 0.0
    assert math.isnan(r.mean_lat) and math.isnan(r.cep50)


def test_add_fix_ignored_when_inactive():
    h = PositionHold()
    h.add_fix(1.0, 2.0, 3.0)
    assert h.fix_count == 0


@pytest.mark.parametrize("lat, lon", [(NAN, 1.0), (1.0, NAN), (float("inf"), 1.0)])
def test_add_fix_ignores_non_finite_position(lat, lon):
    h = PositionHold()
    h.start()
    h.add_fix(lat, lon, 10.0)
    assert h.fix_count == 0


def test_single_fix(clock):
    h = PositionHold()
    h.start()
    h.add_fix(10.0, 20.0, 30.0)
    clock["t"] += 5
    r = h.result
    assert r.fix_count == 1
    assert (r.mean_lat, r.mean_lon, r.mean_alt) == (10.0, 20.0, 30.0)
    assert math.isnan(r.std_north) and math.isnan(r.cep50)
    assert r.duration == pytest.approx(5.0)


def test_statistics_match_reference(clock):
    lats, lons, alts = _samples()
    h = PositionHold()
    h.start()
    for la, lo, al in zip(lats, lons, alts):
        h.add_fix(la, lo, al)
    clock["t"] += 42
    r = h.result

    assert r.fix_count == len(lats)
    assert r.duration == pytest.approx(42.0)
    assert r.mean_lat == pytest.approx(statistics.mean(lats), abs=1e-12)
    assert r.mean_lon == pytest.approx(statistics.mean(lons), abs=1e-12)
    assert r.mean_alt == pytest.approx(statistics.mean(alts), rel=1e-12)

    std_n = statistics.stdev(lats) * M_PER_DEG_LAT
    std_e = statistics.stdev(lons) * m_per_deg_lon(statistics.mean(lats))
    assert r.std_north == pytest.approx(std_n, rel=1e-9)
    assert r.std_east == pytest.approx(std_e, rel=1e-9)
    assert r.std_alt == pytest.approx(statistics.stdev(alts), rel=1e-9)

    cep50 = 0.5887 * (std_n + std_e)
    assert r.cep50 == pytest.approx(cep50, rel=1e-9)
    assert r.cep95 == pytest.approx(2.146 * cep50, rel=1e-9)


def test_identical_fixes_have_zero_spread():
    h = PositionHold()
    h.start()
    for _ in range(5):
        h.add_fix(1.0, 2.0, 3.0)
    r = h.result
    assert r.std_north == 0.0 and r.std_east == 0.0 and r.std_alt == 0.0 and r.cep50 == 0.0


def test_stop_returns_result_and_deactivates(clock):
    h = PositionHold()
    h.start()
    h.add_fix(1.0, 2.0, 3.0)
    h.add_fix(1.0001, 2.0001, 4.0)
    r = h.stop()
    assert not h.is_active
    assert r.fix_count == 2
    assert r.duration == 0.0  # duration is only reported while active
    h.add_fix(5.0, 5.0, 5.0)
    assert h.fix_count == 2


def test_start_resets_accumulators():
    h = PositionHold()
    h.start()
    h.add_fix(1.0, 2.0, 3.0)
    h.add_fix(3.0, 4.0, 5.0)
    h.start()
    assert h.fix_count == 0
    h.add_fix(10.0, 20.0, 30.0)
    assert h.result.mean_lat == 10.0


def test_missing_altitude_does_not_affect_horizontal_stats():
    h = PositionHold()
    h.start()
    h.add_fix(1.0, 2.0, NAN)
    h.add_fix(3.0, 4.0, NAN)
    r = h.result
    assert r.fix_count == 2
    assert r.mean_lat == pytest.approx(2.0)
    assert r.mean_lon == pytest.approx(3.0)


@pytest.mark.xfail(strict=True, reason="F12: altitude mean divides by total fix count, not altitude count")
def test_altitude_mean_ignores_fixes_without_altitude():
    h = PositionHold()
    h.start()
    h.add_fix(1.0, 2.0, NAN)
    h.add_fix(1.0, 2.0, 100.0)
    assert h.result.mean_alt == pytest.approx(100.0)
