"""GNSS constellation identifiers, display names, colors, and status maps."""

GNSS_NAMES: dict[int, str] = {
    0: "GPS",
    1: "SBAS",
    2: "Galileo",
    3: "BeiDou",
    4: "IMES",
    5: "QZSS",
    6: "GLONASS",
    7: "NavIC",
}

GNSS_SHORT: dict[int, str] = {
    0: "GP",
    1: "SB",
    2: "GA",
    3: "BD",
    4: "IM",
    5: "QZ",
    6: "GL",
    7: "IR",
}

GNSS_COLORS: dict[int, str] = {
    0: "green",
    1: "yellow",
    2: "dodger_blue2",
    3: "red",
    4: "magenta",
    5: "cyan",
    6: "orange1",
    7: "bright_yellow",
}

MODE_NAMES: dict[int, str] = {
    0: "Unknown",
    1: "No Fix",
    2: "2D Fix",
    3: "3D Fix",
}

MODE_COLORS: dict[int, str] = {
    0: "bright_black",
    1: "red",
    2: "yellow",
    3: "green",
}

STATUS_NAMES: dict[int, str] = {
    0: "Unknown",
    1: "GPS",
    2: "DGPS",
    3: "RTK Fix",
    4: "RTK Float",
    5: "DR",
    6: "GNSS+DR",
    7: "Time Only",
    8: "Simulated",
    9: "PPS Fix",
}

STATUS_COLORS: dict[int, str] = {
    0: "bright_black",
    1: "green",
    2: "bright_green",
    3: "bright_cyan",
    4: "cyan",
    5: "yellow",
    6: "yellow",
    7: "blue",
    8: "magenta",
    9: "bright_green",
}

DOP_RATINGS: list[tuple[float, str, str]] = [
    (1.0, "Ideal", "bright_green"),
    (2.0, "Excellent", "green"),
    (5.0, "Good", "green"),
    (10.0, "Moderate", "yellow"),
    (20.0, "Fair", "orange1"),
    (float("inf"), "Poor", "red"),
]

COMPASS_POINTS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def bearing_to_compass(degrees: float) -> str:
    """Convert a bearing in degrees to a compass direction string."""
    idx = int((degrees + 11.25) / 22.5) % 16
    return COMPASS_POINTS[idx]


def dop_rating(value: float) -> tuple[str, str]:
    """Return (label, color) for a DOP value."""
    for threshold, label, color in DOP_RATINGS:
        if value < threshold:
            return label, color
    return "Poor", "red"
