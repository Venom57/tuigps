"""NaN-safe formatting helpers for GPS data display."""

from __future__ import annotations

import math


def fmt(value: float, spec: str = ".1f", suffix: str = "", na: str = "---") -> str:
    """Format a float, returning na string if NaN/Inf."""
    if not math.isfinite(value):
        return na
    return f"{value:{spec}}{suffix}"


def fmt_coord(value: float, axis: str = "lat", style: str = "dd") -> str:
    """Format a coordinate value.

    axis: 'lat' or 'lon'
    style: 'dd' (decimal degrees), 'dms' (deg/min/sec), 'ddm' (deg/decimal min)
    """
    if not math.isfinite(value):
        return "---"

    if axis == "lat":
        direction = "N" if value >= 0 else "S"
    else:
        direction = "E" if value >= 0 else "W"

    abs_val = abs(value)

    if style == "dd":
        return f"{abs_val:.6f}\u00b0 {direction}"
    elif style == "dms":
        # Round once, in integer hundredths of a second, so that rounding
        # carries into minutes/degrees instead of showing 60.00 seconds.
        total = round(abs_val * 360_000)
        degrees, rem = divmod(total, 360_000)
        minutes, hundredths = divmod(rem, 6_000)
        return f"{degrees}\u00b0{minutes:02d}'{hundredths / 100:05.2f}\"{direction}"
    elif style == "ddm":
        # Same, in units of 1e-5 minutes (never shows 60.00000 minutes)
        total = round(abs_val * 6_000_000)
        degrees, rem = divmod(total, 6_000_000)
        return f"{degrees}\u00b0{rem / 100_000:08.5f}'{direction}"
    return f"{abs_val:.6f}\u00b0 {direction}"


# Unit systems: "metric" (m, km/h, m/s climb), "imperial" (ft, mph,
# ft/min climb) and "nautical" (ft, knots, ft/min climb).
M_TO_FT = 3.28084
MPS_TO_KMH = 3.6
MPS_TO_MPH = 2.23694
MPS_TO_KN = 1.94384
MPS_TO_FPM = M_TO_FT * 60.0


def distance_unit(unit: str = "metric") -> str:
    """The distance/altitude unit label for a unit system ("m" or "ft")."""
    return "ft" if unit in ("imperial", "nautical") else "m"


def fmt_distance(meters: float, unit: str = "metric", spec: str = ".1f") -> str:
    """Format a distance (or altitude, error estimate) in m or ft."""
    if not math.isfinite(meters):
        return "---"
    if distance_unit(unit) == "ft":
        return f"{meters * M_TO_FT:{spec}} ft"
    return f"{meters:{spec}} m"


def fmt_speed(mps: float, unit: str = "metric", spec: str = ".1f") -> str:
    """Format speed from m/s to the chosen unit system."""
    if not math.isfinite(mps):
        return "---"
    if unit == "metric":
        return f"{mps * MPS_TO_KMH:{spec}} km/h"
    elif unit == "imperial":
        return f"{mps * MPS_TO_MPH:{spec}} mph"
    elif unit == "nautical":
        return f"{mps * MPS_TO_KN:{spec}} kn"
    return f"{mps:{spec}} m/s"


def fmt_climb(mps: float, unit: str = "metric") -> str:
    """Format a vertical speed: m/s (metric) or ft/min (imperial, nautical)."""
    if not math.isfinite(mps):
        return "---"
    if distance_unit(unit) == "ft":
        return f"{mps * MPS_TO_FPM:.0f} ft/min"
    return f"{mps:.2f} m/s"


def fmt_altitude(meters: float, unit: str = "metric") -> str:
    """Format altitude from meters to the chosen unit system."""
    return fmt_distance(meters, unit, ".1f")


def fmt_time_iso(iso_str: str) -> tuple[str, str]:
    """Split an ISO 8601 timestamp into date and time parts."""
    if not iso_str:
        return "---", "---"
    parts = iso_str.replace("T", " ").replace("Z", "").split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], "---"


def trim(txt):
    """Strip trailing whitespace (newlines) from a rich Text, in place, and return it.

    Panels build their text line by line ending in "\\n"; without trimming,
    the last newline adds a blank line to the panel's content height.
    """
    txt.rstrip()
    return txt
