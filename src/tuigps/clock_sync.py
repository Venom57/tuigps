"""System clock synchronization from GPS time."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone

from .timeutil import parse_gps_time


def run_command(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run a command; a missing program or a timeout is a failed result, not an exception."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: command not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]}: timed out after {timeout:g}s")


def disable_ntp() -> bool:
    """Turn NTP sync off before stepping the clock (NTP would undo the change).

    Returns whether NTP was on beforehand, so a failed clock set can turn it
    back on (see :func:`restore_ntp`). Runs synchronously: call from a thread.
    """
    was_on = ntp_enabled() is True
    set_ntp(False)
    return was_on


def restore_ntp(was_on: bool) -> str:
    """After a *failed* clock set, turn NTP back on if :func:`disable_ntp` found it on.

    Returns a line for the user ("" when NTP was off and nothing was done).
    """
    if not was_on:
        return ""
    result = set_ntp(True)
    if result.startswith("Error"):
        return f"NTP was on before and could not be re-enabled: {result}"
    return "NTP was re-enabled (it was on before the attempt)"


def set_clock_from_gps(
    gps_time_str: str,
    fix_received: float = 0.0,
) -> str:
    """Set system clock from a GPS time string. Returns a status message.

    Runs synchronously — call from a background thread to avoid blocking the UI.

    NTP is turned off first. If every method of setting the clock fails and
    NTP was on, it is turned back on (and the message says so).

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

    # Disable NTP first (remembering whether it was on)
    ntp_was_on = disable_ntp()

    # Try D-Bus (absolute UTC usec, allow polkit)
    result = run_command(
        [
            "busctl", "call", "org.freedesktop.timedate1",
            "/org/freedesktop/timedate1",
            "org.freedesktop.timedate1",
            "SetTime", "xbb", str(usec), "false", "true",
        ],
        timeout=10,
    )

    adjusted = dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    if result.returncode == 0:
        return f"System clock set to: {adjusted} UTC (fix age: {fix_age:.3f}s)"
    busctl_err = result.stderr.strip()

    # Fallback: timedatectl (expects local time)
    local_dt = dt.astimezone()
    formatted = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    result = run_command(["timedatectl", "set-time", formatted], timeout=10)
    if result.returncode == 0:
        return f"System clock set to: {formatted} (local, fix age: {fix_age:.3f}s)"
    timedatectl_err = result.stderr.strip()

    # Last resort: sudo -n date (passwordless only)
    utc_str = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    result = run_command(["sudo", "-n", "date", "-u", "-s", utc_str], timeout=5)
    if result.returncode == 0:
        return f"System clock set to: {result.stdout.strip()}"

    message = (
        f"Error: could not set time.\n"
        f"  busctl: {busctl_err}\n"
        f"  timedatectl: {timedatectl_err}\n"
        f"  sudo: {result.stderr.strip()}\n"
        f"  Tip: run 'sudo timedatectl set-ntp false' first,\n"
        f"  or add to sudoers: {os.environ.get('USER', 'user')} "
        f"ALL=(ALL) NOPASSWD: /usr/bin/date"
    )
    restored = restore_ntp(ntp_was_on)
    return f"{message}\n  {restored}" if restored else message


# Shown after the clock has been set: setting it turns NTP off.
NTP_DISABLED_HINT = "NTP is disabled — press Re-enable NTP to restore automatic time sync"


def set_ntp(enabled: bool) -> str:
    """Turn systemd NTP time sync on or off; returns a status message.

    Runs synchronously (``timedatectl set-ntp``): call from a worker thread.
    """
    value = "true" if enabled else "false"
    try:
        result = subprocess.run(
            ["timedatectl", "set-ntp", value],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return "Error: timedatectl not found"
    except subprocess.TimeoutExpired:
        return f"Error: timedatectl set-ntp {value} timed out"
    if result.returncode == 0:
        return "NTP enabled: automatic time sync restored" if enabled else "NTP disabled"
    err = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
    return f"Error: timedatectl set-ntp {value} failed: {err}"


def ntp_enabled() -> bool | None:
    """Whether systemd NTP time sync is on; None when it cannot be determined.

    Runs synchronously (``timedatectl show -p NTP --value``): call from a
    worker thread.
    """
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTP", "--value"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {"yes": True, "no": False}.get(result.stdout.strip().lower())
