"""Device configuration panel — u-blox 8 settings via ubxtool."""

from __future__ import annotations

import asyncio
import ctypes
import fcntl
import glob
import os
import shlex
import struct
import subprocess
import threading
import time
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, RichLog, Select

from .. import clock_sync
from ..clock_sync import NTP_DISABLED_HINT, set_clock_from_gps
from ..data_model import GPSData
from ..screens.confirm_screen import ConfirmScreen
from ..timeutil import parse_gps_time


# ── Linux kernel PPS ioctl structures (from <linux/pps.h>) ──

class _PPSKTime(ctypes.Structure):
    _fields_ = [
        ("sec", ctypes.c_int64),
        ("nsec", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
    ]


class _PPSKInfo(ctypes.Structure):
    _fields_ = [
        ("assert_sequence", ctypes.c_uint32),
        ("clear_sequence", ctypes.c_uint32),
        ("assert_tu", _PPSKTime),
        ("clear_tu", _PPSKTime),
        ("current_mode", ctypes.c_int32),
    ]


class _PPSFData(ctypes.Structure):
    _fields_ = [
        ("info", _PPSKInfo),
        ("timeout", _PPSKTime),
    ]


# pps_ktime.flags: timeout is invalid, i.e. wait forever. Leave it clear so the
# kernel honours our timeout.
_PPS_TIME_INVALID = 1 << 0


def _iowr(type_char: str, nr: int, size: int) -> int:
    """Compute _IOWR ioctl number (asm-generic encoding: x86, arm)."""
    return (3 << 30) | (size << 16) | (ord(type_char) << 8) | nr


# <linux/pps.h>: #define PPS_FETCH _IOWR('p', 0xa4, struct pps_fdata *)
# The size field is the size of the *pointer*, not of struct pps_fdata.
_PPS_FETCH = _iowr("p", 0xA4, ctypes.sizeof(ctypes.c_void_p))


# u-blox 8 dynamic platform models
PLATFORM_MODELS = [
    (0, "Portable"),
    (2, "Stationary"),
    (3, "Pedestrian"),
    (4, "Automotive"),
    (5, "Sea"),
    (6, "Airborne <1g"),
    (7, "Airborne <2g"),
    (8, "Airborne <4g"),
]

# Navigation update rates
NAV_RATES = [
    (1000, "1 Hz"),
    (500, "2 Hz"),
    (200, "5 Hz"),
    (100, "10 Hz"),
]

# Power modes
POWER_MODES = [
    (0, "Full Power"),
    (1, "Balanced"),
    (2, "Interval"),
    (3, "Aggressive 1Hz"),
    (4, "Aggressive 2Hz"),
]

# PPS frequencies
PPS_FREQUENCIES = [
    (1, "1 Hz"),
    (2, "2 Hz"),
    (5, "5 Hz"),
    (10, "10 Hz"),
    (100, "100 Hz"),
    (1000, "1 kHz"),
    (10000, "10 kHz"),
]

# PPS pulse lengths (microseconds)
PPS_DURATIONS = [
    (100000, "100 ms"),
    (50000, "50 ms"),
    (10000, "10 ms"),
    (1000, "1 ms"),
    (100, "100 us"),
    (50, "50 us"),
    (10, "10 us"),
]

# Serial port baud rates
BAUD_RATES = [
    (9600, "9600"),
    (19200, "19200"),
    (38400, "38400"),
    (57600, "57600"),
    (115200, "115200"),
    (230400, "230400"),
    (460800, "460800"),
]

# GNSS constellations for device config
CONSTELLATIONS = ["GPS", "GLONASS", "GALILEO", "BEIDOU", "SBAS", "QZSS"]

# Setting the clock always turns NTP off first (it would undo the change)
_NTP_OFF = (
    "NTP time sync will be disabled (timedatectl set-ntp false); "
    "use Re-enable NTP to turn it back on."
)

# Confirmation dialogs: button id -> (title, message, confirm button label)
CONFIRMATIONS: dict[str, tuple[str, str, str]] = {
    "btn-coldboot": (
        "Cold boot the receiver?",
        "The receiver discards its stored ephemeris, almanac, position and time "
        "and restarts. Getting a fix again can take several minutes.",
        "Cold Boot",
    ),
    "btn-save": (
        "Save configuration to the receiver?",
        "Writes the receiver's current configuration to its non-volatile memory "
        "(battery-backed RAM / flash). It will persist across power cycles.",
        "Save",
    ),
    "btn-pps-disable": (
        "Disable PPS?",
        "Turns off the receiver's timepulse output. Anything disciplined by PPS "
        "(chrony, ntpd, Set Clock (PPS)) loses its reference until PPS is applied again.",
        "Disable PPS",
    ),
    "btn-baud": (
        "Change the serial speed to {speed}?",
        "The receiver switches to {speed} baud, then gpsd is told to follow. If either "
        "step fails, gpsd may lose contact with the receiver; recover by power-cycling "
        "the receiver or with 'gpsctl -s <speed>'.",
        "Change Speed",
    ),
    "btn-set-clock": (
        "Set the system clock now?",
        "Sets the system clock from the latest GPS fix (corrected for its age). "
        + _NTP_OFF,
        "Set Clock",
    ),
    "btn-pps-sync": (
        "Set the system clock on the next PPS pulse?",
        "Waits for the next PPS pulse and steps the system clock to the GPS second "
        "it marks. " + _NTP_OFF,
        "Set Clock",
    ),
    "btn-arm-clock": (
        "Arm clock sync?",
        "The system clock will be set from the next GPS fix as soon as it arrives. "
        + _NTP_OFF,
        "Arm",
    ),
}


class DeviceConfig(Vertical):
    """u-blox 8 device configuration via ubxtool."""

    DEFAULT_CSS = """
    DeviceConfig {
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self._proto_ver = "18"  # u-blox 8 default
        self._device_path = ""
        self._gnss_enabled: dict[str, bool] = {g: False for g in CONSTELLATIONS}

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        if data.device.path:
            self._device_path = data.device.path

    def compose(self) -> ComposeResult:
        # Top: controls (fixed height, scrollable if needed)
        with VerticalScroll(id="config-controls"):
            yield Label("u-blox Device Configuration", classes="config-title")

            with Horizontal(classes="config-row"):
                yield Label("Platform Model: ", classes="config-label")
                yield Select(
                    [(name, val) for val, name in PLATFORM_MODELS],
                    id="sel-model",
                    prompt="Select model...",
                )
                yield Button("Set", id="btn-model", variant="primary")
            with Horizontal(classes="config-row"):
                yield Label("Nav Rate:       ", classes="config-label")
                yield Select(
                    [(name, val) for val, name in NAV_RATES],
                    id="sel-rate",
                    prompt="Select rate...",
                )
                yield Button("Set", id="btn-rate", variant="primary")
            with Horizontal(classes="config-row"):
                yield Label("Power Mode:     ", classes="config-label")
                yield Select(
                    [(name, val) for val, name in POWER_MODES],
                    id="sel-power",
                    prompt="Select mode...",
                )
                yield Button("Set", id="btn-power", variant="primary")

            with Horizontal(classes="config-row"):
                yield Label("Serial Speed:   ", classes="config-label")
                yield Select(
                    [(name, val) for val, name in BAUD_RATES],
                    id="sel-baud",
                    prompt="Select baud...",
                )
                yield Button("Set", id="btn-baud", variant="primary")
                yield Button("Read", id="btn-baud-read", variant="default")

            with Horizontal(classes="config-row"):
                yield Label("PPS Frequency:  ", classes="config-label")
                yield Select(
                    [(name, val) for val, name in PPS_FREQUENCIES],
                    id="sel-pps-freq",
                    prompt="Select freq...",
                    value=1,
                )
            with Horizontal(classes="config-row"):
                yield Label("PPS Duration:   ", classes="config-label")
                yield Select(
                    [(name, val) for val, name in PPS_DURATIONS],
                    id="sel-pps-dur",
                    prompt="Select duration...",
                    value=100000,
                )
            with Horizontal(classes="config-row"):
                yield Label("PPS:            ", classes="config-label")
                yield Button("Apply PPS", id="btn-pps-apply", variant="primary")
                yield Button("Disable PPS", id="btn-pps-disable", variant="error")
                yield Button("Read PPS", id="btn-pps-read", variant="default")

            with Horizontal(classes="config-row"):
                yield Label("Constellations: ", classes="config-label")
                for gnss in CONSTELLATIONS:
                    yield Button(
                        gnss, id=f"btn-gnss-{gnss.lower()}", classes="gnss-btn gnss-off"
                    )
            with Horizontal(classes="config-row"):
                yield Label("", classes="config-label")
                yield Button("Read GNSS", id="btn-gnss-read", variant="default")

            with Horizontal(classes="config-row"):
                yield Button("Save Config", id="btn-save", variant="warning")
                yield Button("Cold Boot", id="btn-coldboot", variant="error")
                yield Button("Read Nav", id="btn-read", variant="default")
                yield Button("Read Rate", id="btn-read-rate", variant="default")
            with Horizontal(classes="config-row"):
                yield Button("Arm Clock Sync", id="btn-arm-clock", variant="warning")
                yield Button("Set Clock (now)", id="btn-set-clock", variant="warning")
                yield Button("Set Clock (PPS)", id="btn-pps-sync", variant="warning")
                yield Button("Re-enable NTP", id="btn-ntp-on", variant="default")
                yield Label("NTP: ?", id="ntp-state")

            with Horizontal(classes="config-row"):
                yield Label("ubxtool cmd:    ", classes="config-label")
                yield Input(placeholder="-p MON-VER", id="input-cmd")
                yield Button("Run", id="btn-run-cmd", variant="primary")

        # Bottom: scrollable output log
        yield RichLog(id="config-output", classes="config-output", wrap=True, markup=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if not btn_id:
            return

        if btn_id == "btn-model":
            value = self._selected("#sel-model")
            if value is not None:
                self._run_ubxtool(f"-p MODEL,{value}")
        elif btn_id == "btn-rate":
            value = self._selected("#sel-rate")
            if value is not None:
                self._run_ubxtool(f"-p CFG-RATE,{value}")
        elif btn_id == "btn-power":
            value = self._selected("#sel-power")
            if value is not None:
                self._run_ubxtool(f"-p PMS,{value}")
        elif btn_id == "btn-baud":
            value = self._selected("#sel-baud")
            if value is not None:
                speed = int(value)
                self._confirm(btn_id, lambda: self._set_baud_rate(speed), speed=speed)
        elif btn_id == "btn-baud-read":
            self._run_ubxtool("-p CFG-PRT")
        elif btn_id == "btn-pps-apply":
            freq_sel = self.query_one("#sel-pps-freq", Select)
            dur_sel = self.query_one("#sel-pps-dur", Select)
            freq = freq_sel.value if freq_sel.value is not Select.NULL else 1
            dur = dur_sel.value if dur_sel.value is not Select.NULL else 100000
            self._send_tp5(freq_hz=int(freq), pulse_us=int(dur), active=True)
        elif btn_id == "btn-pps-disable":
            self._confirm(btn_id, lambda: self._send_tp5(freq_hz=0, pulse_us=0, active=False))
        elif btn_id == "btn-pps-read":
            self._run_ubxtool("-p CFG-TP5")
        elif btn_id == "btn-save":
            self._confirm(btn_id, lambda: self._run_ubxtool("-p SAVE"))
        elif btn_id == "btn-coldboot":
            self._confirm(btn_id, lambda: self._run_ubxtool("-p COLDBOOT"))
        elif btn_id == "btn-read":
            self._run_ubxtool("-p CFG-NAV5")
        elif btn_id == "btn-read-rate":
            self._run_ubxtool("-p CFG-RATE")
        elif btn_id == "btn-gnss-read":
            self._run_ubxtool("-p CFG-GNSS")
        elif btn_id == "btn-arm-clock":
            if self.app._armed_clock_set:
                self._arm_clock_sync()  # disarming needs no confirmation
            else:
                self._confirm(btn_id, self._arm_clock_sync)
        elif btn_id == "btn-set-clock":
            if self._gps_time_available():
                self._confirm(btn_id, self._set_system_clock)
        elif btn_id == "btn-pps-sync":
            if self._pps_sync_params() is not None:
                self._confirm(btn_id, self._pps_sync_clock)
        elif btn_id == "btn-ntp-on":
            self._enable_ntp()
        elif btn_id == "btn-run-cmd":
            inp = self.query_one("#input-cmd", Input)
            if inp.value.strip():
                self._run_ubxtool(inp.value.strip())
        elif btn_id.startswith("btn-gnss-"):
            gnss = btn_id.replace("btn-gnss-", "").upper()
            currently_on = self._gnss_enabled.get(gnss, False)
            if currently_on:
                self._run_ubxtool(f"-d {gnss}")
                self._gnss_enabled[gnss] = False
            else:
                self._run_ubxtool(f"-e {gnss}")
                self._gnss_enabled[gnss] = True
            self._update_gnss_buttons()

    def _confirm(self, action_id: str, action, **fmt) -> None:
        """Run ``action`` once the user confirms the CONFIRMATIONS[action_id] dialog."""
        title, message, label = CONFIRMATIONS[action_id]

        def on_result(confirmed: bool | None) -> None:
            if confirmed:
                action()

        self.app.push_screen(
            ConfirmScreen(title.format(**fmt), message.format(**fmt), label), on_result
        )

    def _selected(self, select_id: str):
        """Return a Select's value, or None (with a hint) when nothing is selected."""
        value = self.query_one(select_id, Select).value
        if value is Select.NULL:
            self._append_output("Select a value first")
            self.app.notify("Select a value first", severity="warning", timeout=2)
            return None
        return value

    def _update_gnss_buttons(self) -> None:
        """Update GNSS button styles to reflect enabled/disabled state."""
        for gnss in CONSTELLATIONS:
            try:
                btn = self.query_one(f"#btn-gnss-{gnss.lower()}", Button)
            except NoMatches:
                continue
            btn.variant = "success" if self._gnss_enabled.get(gnss, False) else "default"

    def _arm_clock_sync(self) -> None:
        """Arm clock sync — fires on the gpsd thread when the next fix arrives.

        This gives minimum latency: the clock is set immediately when gpsd
        delivers a fresh TPV message, before marshaling to the Textual event loop.
        """
        if self.app._armed_clock_set:
            # Already armed, disarm
            self.app._armed_clock_set = False
            self._append_output("Clock sync disarmed")
            btn = self.query_one("#btn-arm-clock", Button)
            btn.variant = "warning"
            btn.label = "Arm Clock Sync"
        else:
            self.app._armed_clock_set = True
            self._append_output("Clock sync ARMED — will fire on next GPS update")
            btn = self.query_one("#btn-arm-clock", Button)
            btn.variant = "success"
            btn.label = "ARMED (click to cancel)"

    def _gps_time_available(self) -> bool:
        if not self._data or not self._data.time:
            self._append_output("Error: no GPS time available")
            return False
        return True

    def _set_system_clock(self) -> None:
        """Set the system clock immediately from current GPS time."""
        if not self._gps_time_available():
            return

        gps_time = self._data.time
        received = self._data.tpv_received  # receipt time of the TPV, not any message
        fix_age = time.time() - received if received > 0 else 0.0
        self._append_output(f"Setting system clock now: {gps_time} (fix age: {fix_age:.1f}s)")

        def run():
            try:
                msg = set_clock_from_gps(gps_time, received)
                self.app.call_from_thread(self.report_clock_result, msg)
            except Exception as e:
                self.app.call_from_thread(self._append_output, f"Error: {e}")

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _find_pps_device() -> str | None:
        """Return the first available /dev/pps* device, or None."""
        devices = sorted(glob.glob("/dev/pps*"))
        return devices[0] if devices else None

    @staticmethod
    def _wait_for_pps(pps_path: str, timeout_sec: int = 3) -> tuple[int, int, int] | None:
        """Wait for next PPS assert edge via kernel ioctl.

        Returns (kernel_sec, kernel_nsec, sequence) or None on timeout/error.
        """
        fd = None
        try:
            fd = os.open(pps_path, os.O_RDONLY)
            fdata = _PPSFData()
            fdata.timeout.sec = timeout_sec
            fdata.timeout.nsec = 0
            fdata.timeout.flags = 0  # not _PPS_TIME_INVALID: honour the timeout
            fcntl.ioctl(fd, _PPS_FETCH, fdata)
            info = fdata.info
            return (info.assert_tu.sec, info.assert_tu.nsec, info.assert_sequence)
        except OSError:
            return None
        finally:
            if fd is not None:
                os.close(fd)

    def _pps_sync_params(self) -> tuple[str, float, float] | None:
        """(PPS device, GPS epoch, TPV receipt time) for a PPS sync, or None.

        Problems (no GPS time, no PPS device, bad time) are reported in the log.
        """
        if not self._data or not self._data.time or self._data.tpv_received <= 0:
            self._append_output("Error: no GPS time available for PPS sync")
            return None

        pps_dev = self._find_pps_device()
        if not pps_dev:
            self._append_output(
                "Error: no PPS device found (/dev/pps*)\n"
                "  Tip: modprobe pps_gpio or pps_ldisc, or check GPS wiring"
            )
            return None

        gps_time_str = self._data.time
        gps_epoch = parse_gps_time(gps_time_str)
        if gps_epoch is None:
            self._append_output(f"Error: cannot parse GPS time {gps_time_str!r}")
            return None
        # Fix age reference: receipt of the TPV that carried this time
        return pps_dev, gps_epoch, self._data.tpv_received

    def _pps_sync_clock(self) -> None:
        """Set system clock precisely using PPS pulse edge timing.

        Flow:
        1. Estimate which GPS second the next PPS pulse will mark
        2. Block on PPS_FETCH ioctl (kernel captures timestamp at interrupt)
        3. Compute offset = GPS_second - kernel_timestamp
        4. Apply relative adjustment via D-Bus SetTime
        The delay between ioctl return and SetTime cancels out mathematically.
        """
        params = self._pps_sync_params()
        if params is None:
            return
        pps_dev, gps_epoch, received = params
        self._append_output(f"PPS sync: waiting for pulse on {pps_dev}...")

        def run():
            try:

                # Estimate current GPS time to predict which second the pulse marks
                elapsed = time.time() - received  # true elapsed (system clock based)
                current_gps = gps_epoch + elapsed
                # Next whole GPS second (the one the PPS pulse will mark)
                target_sec = int(current_gps) + 1

                # Block until PPS pulse (kernel captures CLOCK_REALTIME at interrupt)
                pps = self._wait_for_pps(pps_dev, timeout_sec=3)
                if pps is None:
                    self.app.call_from_thread(
                        self._append_output,
                        f"Error: PPS fetch failed on {pps_dev} (timeout or permission denied)\n"
                        f"  Check: ls -la {pps_dev}  (need read permission)",
                    )
                    return

                kern_sec, kern_nsec, seq = pps

                # After pulse: refine target using post-pulse GPS time estimate
                elapsed_after = time.time() - received
                current_gps_after = gps_epoch + elapsed_after
                target_sec = round(current_gps_after)

                # Offset = where GPS says we should be - where kernel clock was at pulse
                # offset_usec > 0 means system clock is behind GPS
                offset_usec = (
                    target_sec * 1_000_000
                    - kern_sec * 1_000_000
                    - kern_nsec // 1_000
                )

                offset_ms = offset_usec / 1000.0
                self.app.call_from_thread(
                    self._append_output,
                    f"PPS pulse #{seq}: kernel={kern_sec}.{kern_nsec:09d}\n"
                    f"  GPS target second: {target_sec}\n"
                    f"  Clock offset: {offset_ms:+.3f} ms",
                )

                # Disable NTP
                subprocess.run(
                    ["timedatectl", "set-ntp", "false"],
                    capture_output=True, text=True, timeout=5,
                )

                # Apply relative offset via D-Bus (delay since PPS cancels out)
                result = subprocess.run(
                    [
                        "busctl", "call", "org.freedesktop.timedate1",
                        "/org/freedesktop/timedate1",
                        "org.freedesktop.timedate1",
                        "SetTime", "xbb", str(offset_usec), "true", "true",
                    ],
                    capture_output=True, text=True, timeout=10,
                )

                if result.returncode == 0:
                    self.app.call_from_thread(
                        self.report_clock_result,
                        f"System clock adjusted by {offset_ms:+.3f} ms (PPS-disciplined)",
                    )
                else:
                    busctl_err = result.stderr.strip()
                    # Fallback: compute absolute time and use sudo -n date
                    abs_dt = datetime.fromtimestamp(target_sec, tz=timezone.utc)
                    utc_str = abs_dt.strftime("%Y-%m-%d %H:%M:%S")
                    result = subprocess.run(
                        ["sudo", "-n", "date", "-u", "-s", utc_str],
                        capture_output=True, text=True, timeout=5,
                    )
                    if result.returncode == 0:
                        self.app.call_from_thread(
                            self.report_clock_result,
                            f"System clock set to {utc_str} UTC (sudo fallback, ~1s precision)",
                        )
                    else:
                        self.app.call_from_thread(
                            self._append_output,
                            f"Error: could not apply PPS offset\n"
                            f"  busctl: {busctl_err}\n"
                            f"  sudo: {result.stderr.strip()}",
                        )
            except Exception as e:
                self.app.call_from_thread(self._append_output, f"PPS sync error: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _build_tp5_cmd(self, freq_hz: int = 1, pulse_us: int = 100000, active: bool = True) -> str:
        """Build a UBX-CFG-TP5 command string for ubxtool -c.

        Format: class,id,payload_bytes... (comma-separated hex bytes).
        ubxtool -c handles sync header, length, and checksum automatically.

        Uses isFreq=1 so freqPeriod is in Hz, isLength=1 so pulseLenRatio is in us.
        """
        # flags: lockGnssFreq | lockedOtherSet | isFreq | isLength | alignToTow | polarity
        flags = 0x02 | 0x04 | 0x08 | 0x10 | 0x20 | 0x40  # 0x7E
        if active:
            flags |= 0x01  # active bit

        payload = struct.pack(
            "<BBHhhIIIIiI",
            0,          # tpIdx (timepulse 0)
            1,          # version
            0,          # reserved
            0,          # antCableDelay (ns)
            0,          # rfGroupDelay (ns)
            freq_hz,    # freqPeriod (Hz, isFreq=1)
            freq_hz,    # freqPeriodLock
            pulse_us,   # pulseLenRatio (us, isLength=1)
            pulse_us,   # pulseLenRatioLock
            0,          # userConfigDelay (ns)
            flags,
        )

        # ubxtool -c format: class,id,payload_byte1,payload_byte2,...
        parts = ["06", "31"] + [f"{b:02x}" for b in payload]
        return ",".join(parts)

    def _send_tp5(self, freq_hz: int = 1, pulse_us: int = 100000, active: bool = True) -> None:
        """Send a UBX-CFG-TP5 message to configure PPS."""
        cmd_str = self._build_tp5_cmd(freq_hz, pulse_us, active)
        state = "ON" if active else "OFF"
        self._append_output(f"PPS {state}: freq={freq_hz}Hz, pulse={pulse_us}us")
        self._run_ubxtool(f"-c {cmd_str}")

    def _set_baud_rate(self, speed: int) -> None:
        """Set the receiver serial port baud rate and update gpsd."""
        device = self._device_path
        self._append_output(f"Setting baud rate to {speed}...")

        def run():
            try:
                # Step 1: Tell the receiver to switch baud rate
                result = subprocess.run(
                    ["ubxtool", "-P", self._proto_ver, "-S", str(speed)],
                    capture_output=True, text=True, timeout=10,
                )
                output = result.stdout.strip()
                if result.stderr.strip():
                    output += "\n" + result.stderr.strip()
                if output:
                    self.app.call_from_thread(self._append_output, output)

                # Step 2: Tell gpsd the new speed so it can talk to the receiver
                if device:
                    gpsctl_cmd = ["gpsctl", "-s", str(speed), device]
                else:
                    gpsctl_cmd = ["gpsctl", "-s", str(speed)]
                result = subprocess.run(
                    gpsctl_cmd,
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    self.app.call_from_thread(
                        self._append_output,
                        f"Baud rate set to {speed} on {device or 'default device'}",
                    )
                else:
                    err = result.stderr.strip() or result.stdout.strip()
                    self.app.call_from_thread(
                        self._append_output,
                        f"gpsctl: {err}" if err else f"gpsctl returned {result.returncode}",
                    )
            except FileNotFoundError as e:
                self.app.call_from_thread(
                    self._append_output, f"Error: {e.filename} not found"
                )
            except subprocess.TimeoutExpired:
                self.app.call_from_thread(self._append_output, "Error: command timed out")
            except Exception as e:
                self.app.call_from_thread(self._append_output, f"Error: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _run_ubxtool(self, args: str) -> None:
        """Run ubxtool in a background thread."""
        cmd_str = f"ubxtool -P {self._proto_ver} {args}"
        self._append_output(f"$ {cmd_str}")
        try:
            argv = shlex.split(args)
        except ValueError as e:
            self._append_output(f"Error: invalid arguments ({e}); check quoting")
            return

        def run():
            try:
                result = subprocess.run(
                    ["ubxtool", "-P", self._proto_ver] + argv,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                output = result.stdout.strip()
                if result.stderr.strip():
                    output += "\n" + result.stderr.strip()
                if not output:
                    output = "(no output)"
                self.app.call_from_thread(self._append_output, output)
            except FileNotFoundError:
                self.app.call_from_thread(
                    self._append_output, "Error: ubxtool not found (install gpsd-clients)"
                )
            except subprocess.TimeoutExpired:
                self.app.call_from_thread(self._append_output, "Error: command timed out")
            except Exception as e:
                self.app.call_from_thread(self._append_output, f"Error: {e}")

        threading.Thread(target=run, daemon=True).start()

    # ─── NTP ─────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_ntp_state()

    def report_clock_result(self, message: str) -> None:
        """Log a clock-setting result; after a successful set, remind about NTP."""
        self._append_output(message)
        if message.startswith("System clock"):
            self._append_output(NTP_DISABLED_HINT)
        self._refresh_ntp_state()

    def _enable_ntp(self) -> None:
        """Turn NTP time sync back on (timedatectl set-ntp true, in a worker thread)."""
        self._append_output("$ timedatectl set-ntp true")

        async def enable() -> None:
            message = await asyncio.to_thread(clock_sync.set_ntp, True)
            self._append_output(message)
            await self._load_ntp_state()

        self.run_worker(enable(), group="ntp")

    def _refresh_ntp_state(self) -> None:
        self.run_worker(self._load_ntp_state(), group="ntp-state", exclusive=True)

    async def _load_ntp_state(self) -> None:
        """Show whether NTP sync is on (timedatectl show, in a worker thread)."""
        enabled = await asyncio.to_thread(clock_sync.ntp_enabled)
        text = {True: "NTP: on", False: "NTP: off"}.get(enabled, "NTP: ?")
        try:
            self.query_one("#ntp-state", Label).update(text)
        except NoMatches:
            pass

    def _append_output(self, text: str) -> None:
        """Add text to the output log."""
        try:
            output = self.query_one("#config-output", RichLog)
        except NoMatches:
            return  # not mounted (e.g. result delivered during shutdown)
        for line in text.split("\n"):
            output.write(line)
