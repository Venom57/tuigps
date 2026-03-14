"""System clock synchronization from GPS time."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timedelta, timezone


def set_clock_from_gps(
    gps_time_str: str,
    last_seen: float = 0.0,
) -> str:
    """Set system clock from a GPS time string. Returns a status message.

    Runs synchronously — call from a background thread to avoid blocking the UI.

    Args:
        gps_time_str: ISO 8601 GPS time (e.g., "2026-03-14T12:00:00.000Z")
        last_seen: time.time() when this fix was received (for age compensation)
    """
    receipt_time = time.time()
    fix_age = receipt_time - last_seen if last_seen > 0 else 0.0

    # Parse GPS time
    ts = gps_time_str.replace("T", " ").replace("Z", "")
    if "." in ts:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
    else:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(tzinfo=timezone.utc)

    # Compensate for age of the fix
    dt += timedelta(seconds=fix_age)

    usec = int(dt.timestamp() * 1_000_000)

    # Disable NTP first
    subprocess.run(
        ["timedatectl", "set-ntp", "false"],
        capture_output=True, text=True, timeout=5,
    )

    # Try D-Bus (absolute UTC usec, allow polkit)
    result = subprocess.run(
        [
            "busctl", "call", "org.freedesktop.timedate1",
            "/org/freedesktop/timedate1",
            "org.freedesktop.timedate1",
            "SetTime", "xbb", str(usec), "false", "true",
        ],
        capture_output=True, text=True, timeout=10,
    )

    adjusted = dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    if result.returncode == 0:
        return f"System clock set to: {adjusted} UTC (fix age: {fix_age:.3f}s)"

    # Fallback: timedatectl (expects local time)
    local_dt = dt.astimezone()
    formatted = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    result = subprocess.run(
        ["timedatectl", "set-time", formatted],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        return f"System clock set to: {formatted} (local, fix age: {fix_age:.3f}s)"

    # Last resort: sudo -n date (passwordless only)
    utc_str = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    result = subprocess.run(
        ["sudo", "-n", "date", "-u", "-s", utc_str],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return f"System clock set to: {result.stdout.strip()}"

    return (
        f"Error: could not set time.\n"
        f"  busctl: {result.stderr.strip()}\n"
        f"  Tip: run 'sudo timedatectl set-ntp false' first,\n"
        f"  or add to sudoers: {os.environ.get('USER', 'user')} "
        f"ALL=(ALL) NOPASSWD: /usr/bin/date"
    )
