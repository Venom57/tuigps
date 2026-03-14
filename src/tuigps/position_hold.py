"""Position hold / averaging — accumulate fixes and compute statistics."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


# Approximate meters per degree
M_PER_DEG_LAT = 110540.0


def m_per_deg_lon(lat: float) -> float:
    """Meters per degree longitude at a given latitude."""
    return 111320.0 * math.cos(math.radians(lat))


@dataclass
class HoldResult:
    """Statistics from a position hold session."""

    mean_lat: float = float("nan")
    mean_lon: float = float("nan")
    mean_alt: float = float("nan")
    std_north: float = float("nan")  # meters
    std_east: float = float("nan")   # meters
    std_alt: float = float("nan")    # meters
    cep50: float = float("nan")      # meters
    cep95: float = float("nan")      # meters
    fix_count: int = 0
    duration: float = 0.0


class PositionHold:
    """Accumulates GPS fixes using Welford's online algorithm."""

    def __init__(self):
        self._active = False
        self._count = 0
        self._start_time = 0.0
        # Welford accumulators for lat, lon, alt
        self._mean_lat = 0.0
        self._mean_lon = 0.0
        self._mean_alt = 0.0
        self._m2_lat = 0.0
        self._m2_lon = 0.0
        self._m2_alt = 0.0

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def fix_count(self) -> int:
        return self._count

    def start(self) -> None:
        """Start a new hold session, clearing accumulators."""
        self._active = True
        self._count = 0
        self._start_time = time.time()
        self._mean_lat = 0.0
        self._mean_lon = 0.0
        self._mean_alt = 0.0
        self._m2_lat = 0.0
        self._m2_lon = 0.0
        self._m2_alt = 0.0

    def stop(self) -> HoldResult:
        """Stop the hold session and return final results."""
        self._active = False
        return self.result

    def add_fix(self, lat: float, lon: float, alt: float) -> None:
        """Add a fix to the accumulator."""
        if not self._active:
            return
        if not math.isfinite(lat) or not math.isfinite(lon):
            return

        self._count += 1
        n = self._count

        # Welford's online algorithm
        delta_lat = lat - self._mean_lat
        self._mean_lat += delta_lat / n
        delta2_lat = lat - self._mean_lat
        self._m2_lat += delta_lat * delta2_lat

        delta_lon = lon - self._mean_lon
        self._mean_lon += delta_lon / n
        delta2_lon = lon - self._mean_lon
        self._m2_lon += delta_lon * delta2_lon

        if math.isfinite(alt):
            delta_alt = alt - self._mean_alt
            self._mean_alt += delta_alt / n
            delta2_alt = alt - self._mean_alt
            self._m2_alt += delta_alt * delta2_alt

    @property
    def result(self) -> HoldResult:
        """Compute current statistics."""
        if self._count < 1:
            return HoldResult(
                duration=time.time() - self._start_time if self._active else 0.0
            )

        duration = time.time() - self._start_time if self._active else 0.0

        if self._count < 2:
            return HoldResult(
                mean_lat=self._mean_lat,
                mean_lon=self._mean_lon,
                mean_alt=self._mean_alt,
                fix_count=self._count,
                duration=duration,
            )

        # Variance in degrees
        var_lat = self._m2_lat / (self._count - 1)
        var_lon = self._m2_lon / (self._count - 1)
        var_alt = self._m2_alt / (self._count - 1)

        # Convert to meters
        std_north = math.sqrt(var_lat) * M_PER_DEG_LAT
        std_east = math.sqrt(var_lon) * m_per_deg_lon(self._mean_lat)
        std_alt = math.sqrt(var_alt)

        # CEP (Circular Error Probable) — Rayleigh approximation
        cep50 = 0.5887 * (std_north + std_east)
        cep95 = 2.146 * cep50

        return HoldResult(
            mean_lat=self._mean_lat,
            mean_lon=self._mean_lon,
            mean_alt=self._mean_alt,
            std_north=std_north,
            std_east=std_east,
            std_alt=std_alt,
            cep50=cep50,
            cep95=cep95,
            fix_count=self._count,
            duration=duration,
        )
