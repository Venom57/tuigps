"""GPS time parsing helpers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# ISO 8601 as sent by gpsd: "2026-03-14T12:00:00.250Z". Fractional seconds may
# have any number of digits (gpsd sends milliseconds or nanoseconds), which
# strptime's %f (max 6 digits) cannot handle.
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d+))?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_gps_time(s: str) -> float | None:
    """Parse an ISO 8601 GPS time string to epoch seconds.

    Accepts a trailing 'Z', a numeric UTC offset ('+00:00') or no zone (taken
    as UTC). Returns None if the string is not a valid timestamp.
    """
    if not isinstance(s, str):
        return None
    m = _ISO_RE.match(s.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, frac, zone = m.groups()

    tz = timezone.utc
    if zone and zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        tz = timezone(sign * offset)

    # A leap second (:60) maps onto the first second of the next minute
    sec = int(second)
    leap = 1 if sec == 60 else 0
    try:
        dt = datetime(
            int(year), int(month), int(day),
            int(hour), int(minute), sec - leap, tzinfo=tz,
        )
    except ValueError:
        return None

    epoch = float(int(dt.timestamp()) + leap)
    if frac:
        epoch += int(frac) / 10 ** len(frac)
    return epoch
