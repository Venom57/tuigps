"""Device information panel."""

from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

from ..data_model import GPSData
from ..formatting import fmt


class DevicePanel(Static):
    """Displays GPS device information."""

    DEFAULT_CSS = """
    DevicePanel {
        height: auto;
        min-height: 6;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        self.refresh()

    def render(self) -> Text:
        txt = Text()
        txt.append("Device\n", style="bold")
        txt.append("─" * 24 + "\n", style="bright_black")

        if not self._data or not self._data.device.path:
            txt.append("  No device\n", style="dim")
            # Show gpsd version if available
            if self._data and self._data.version.release:
                txt.append(f"  gpsd {self._data.version.release}", style="bright_black")
                txt.append(f" (proto {self._data.version.proto_major}.{self._data.version.proto_minor})\n",
                           style="bright_black")
            return txt

        dev = self._data.device

        txt.append("  Path:   ", style="bright_black")
        txt.append(f"{dev.path}\n")

        if dev.driver:
            txt.append("  Driver: ", style="bright_black")
            txt.append(f"{dev.driver}\n")

        if dev.bps:
            txt.append("  Baud:   ", style="bright_black")
            txt.append(f"{dev.bps}\n")

        if math.isfinite(dev.cycle) and dev.cycle > 0:
            txt.append("  Cycle:  ", style="bright_black")
            txt.append(f"{dev.cycle:.1f}s\n")

        # gpsd version
        v = self._data.version
        if v.release:
            txt.append("  gpsd:   ", style="bright_black")
            txt.append(f"{v.release}\n")

        return txt
