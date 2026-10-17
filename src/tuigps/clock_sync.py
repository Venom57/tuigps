"""System clock synchronization from GPS time."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone

from .timeutil import parse_gps_time


def set_clock_from_gps(
    gps_time_str: str,
    fix_received: float = 0.0,
) -> str:
    """Set system clock from a GPS time string. Returns a status message.

    Runs synchronously — call from a background thread to avoid blocking the UI.

    Args:
        gps_time_str: ISO 8601 GPS time (e.g., "2026-03-14T12:00:00.000Z")
        fix_received: time.time() when the TPV carrying this time was received
            (GPSData.tpv_received), used for fix age compensation

    Raises:
        ValueError: if gps_time_str is not a valid timestamp
    """
    receipt_time = time.time()
    fix_age = receipt_time - fix_received if fix_received > 0 else 0.0

    gps_epoch = parse_gps_time(gps_time_str)
    if gps_epoch is None:
        raise ValueError(f"invalid GPS time: {gps_time_str!r}")

    # Compensate for age of the fix
    dt = datetime.fromtimestamp(gps_epoch + fix_age, tz=timezone.utc)

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
    busctl_err = result.stderr.strip()

    # Fallback: timedatectl (expects local time)
    local_dt = dt.astimezone()
    formatted = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    result = subprocess.run(
        ["timedatectl", "set-time", formatted],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        return f"System clock set to: {formatted} (local, fix age: {fix_age:.3f}s)"
    timedatectl_err = result.stderr.strip()

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
        f"  busctl: {busctl_err}\n"
        f"  timedatectl: {timedatectl_err}\n"
        f"  sudo: {result.stderr.strip()}\n"
        f"  Tip: run 'sudo timedatectl set-ntp false' first,\n"
        f"  or add to sudoers: {os.environ.get('USER', 'user')} "
        f"ALL=(ALL) NOPASSWD: /usr/bin/date"
    )
