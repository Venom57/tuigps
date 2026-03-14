"""Signal strength bar chart — SNR per satellite."""

from __future__ import annotations

import math

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from ..constants import GNSS_COLORS, GNSS_SHORT
from ..data_model import GPSData


class SignalChart(Static):
    """Horizontal bar chart of satellite signal strengths (SNR)."""

    DEFAULT_CSS = """
    SignalChart {
        height: 100%;
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
        txt.append("Signal Strength (dBHz)\n", style="bold")
        txt.append("─" * 30 + "\n", style="bright_black")

        if not self._data or not self._data.satellites:
            txt.append("  No satellites\n", style="dim")
            return txt

        # Sort: used first, then by SNR descending
        sats = sorted(
            self._data.satellites,
            key=lambda s: (not s.used, -(s.snr if math.isfinite(s.snr) else 0)),
        )

        max_snr = 55.0  # typical max dBHz for bar scaling
        bar_width = max(8, (self.size.width or 30) - 14)
        height_budget = (self.size.height or 20) - 3  # account for header lines

        for i, sat in enumerate(sats):
            if i >= height_budget:
                remaining = len(sats) - i
                if remaining > 0:
                    txt.append(f"  ... +{remaining} more\n", style="dim")
                break

            if not math.isfinite(sat.snr) or sat.snr <= 0:
                continue

            prefix = GNSS_SHORT.get(sat.gnssid, "??")
            label = f"{prefix}{sat.svid:02d}"
            snr_val = min(sat.snr, max_snr)
            filled = int((snr_val / max_snr) * bar_width)

            color = GNSS_COLORS.get(sat.gnssid, "white")
            use_marker = "\u2022" if sat.used else " "  # bullet for used

            txt.append(f"{use_marker}{label} ", style="white")

            # Filled portion
            txt.append("\u2588" * filled, style=Style(color=color if sat.used else "grey50"))
            # Empty portion
            txt.append("\u2591" * (bar_width - filled), style=Style(color="grey23"))

            txt.append(f" {int(sat.snr):2d}\n", style="white")

        # Legend
        txt.append("\n", style="")
        txt.append(" \u2022", style="white")
        txt.append("=Used  ", style="dim")
        for gnssid, color in sorted(GNSS_COLORS.items()):
            short = GNSS_SHORT.get(gnssid, "??")
            txt.append(f"\u2588{short} ", style=Style(color=color))

        return txt
