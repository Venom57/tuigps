"""Device configuration panel — u-blox 8 settings via ubxtool."""

from __future__ import annotations

import struct
import subprocess
import threading

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, RichLog, Select

from ..data_model import GPSData


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

# GNSS constellations for device config
CONSTELLATIONS = ["GPS", "GLONASS", "GALILEO", "BEIDOU", "SBAS", "QZSS"]


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
            sel = self.query_one("#sel-model", Select)
            if sel.value is not Select.BLANK:
                self._run_ubxtool(f"-p MODEL,{sel.value}")
        elif btn_id == "btn-rate":
            sel = self.query_one("#sel-rate", Select)
            if sel.value is not Select.BLANK:
                self._run_ubxtool(f"-p CFG-RATE,{sel.value}")
        elif btn_id == "btn-power":
            sel = self.query_one("#sel-power", Select)
            if sel.value is not Select.BLANK:
                self._run_ubxtool(f"-p PMS,{sel.value}")
        elif btn_id == "btn-pps-apply":
            freq_sel = self.query_one("#sel-pps-freq", Select)
            dur_sel = self.query_one("#sel-pps-dur", Select)
            freq = freq_sel.value if freq_sel.value is not Select.BLANK else 1
            dur = dur_sel.value if dur_sel.value is not Select.BLANK else 100000
            self._send_tp5(freq_hz=int(freq), pulse_us=int(dur), active=True)
        elif btn_id == "btn-pps-disable":
            self._send_tp5(freq_hz=0, pulse_us=0, active=False)
        elif btn_id == "btn-pps-read":
            self._run_ubxtool("-p CFG-TP5")
        elif btn_id == "btn-save":
            self._run_ubxtool("-p SAVE")
        elif btn_id == "btn-coldboot":
            self._run_ubxtool("-p COLDBOOT")
        elif btn_id == "btn-read":
            self._run_ubxtool("-p CFG-NAV5")
        elif btn_id == "btn-read-rate":
            self._run_ubxtool("-p CFG-RATE")
        elif btn_id == "btn-gnss-read":
            self._run_ubxtool("-p CFG-GNSS")
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

    def _update_gnss_buttons(self) -> None:
        """Update GNSS button styles to reflect enabled/disabled state."""
        for gnss in CONSTELLATIONS:
            try:
                btn = self.query_one(f"#btn-gnss-{gnss.lower()}", Button)
                if self._gnss_enabled.get(gnss, False):
                    btn.variant = "success"
                else:
                    btn.variant = "default"
            except Exception:
                pass

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

    def _run_ubxtool(self, args: str) -> None:
        """Run ubxtool in a background thread."""
        cmd_str = f"ubxtool -P {self._proto_ver} {args}"
        self._append_output(f"$ {cmd_str}")

        def run():
            try:
                result = subprocess.run(
                    ["ubxtool", "-P", self._proto_ver] + args.split(),
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

    def _append_output(self, text: str) -> None:
        """Add text to the output log."""
        try:
            output = self.query_one("#config-output", RichLog)
            for line in text.split("\n"):
                output.write(line)
        except Exception:
            pass
