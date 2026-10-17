"""Signal strength bar chart — SNR per satellite."""

from __future__ import annotations

import math

from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
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

    # gnssids hidden by the constellation display filter (changing it repaints)
    hidden_gnss: reactive[frozenset[int]] = reactive(frozenset())

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None

    def update_gps_data(self, data: GPSData) -> None:
        if data is self._data:
            return  # same immutable snapshot: nothing new to draw
        self._data = data
        self.refresh()

    def render(self) -> Text:
        txt = Text()
        txt.append("Signal Strength (dBHz)\n", style="bold")
        txt.append("─" * 30 + "\n", style="bright_black")

        hidden = self.hidden_gnss
        all_sats = self._data.satellites if self._data else []
        n_hidden = sum(1 for s in all_sats if s.gnssid in hidden)
        if len(all_sats) == n_hidden:
            txt.append("  No satellites\n", style="dim")
            if n_hidden:
                txt.append(f"  ({n_hidden} hidden by filter)\n", style="dim")
            return txt

        # Only satellites with a signal get a row, so only they count against
        # the height budget. Sort: used first, then by SNR descending.
        sats = sorted(
            (s for s in all_sats
             if s.gnssid not in hidden and math.isfinite(s.snr) and s.snr > 0),
            key=lambda s: (not s.used, -s.snr),
        )

        max_snr = 55.0  # typical max dBHz for bar scaling
        bar_width = max(8, (self.size.width or 30) - 14)
        height_budget = (self.size.height or 20) - 3  # account for header lines
        if n_hidden:
            height_budget -= 1  # the "hidden by filter" line

        for i, sat in enumerate(sats):
            if i >= height_budget:
                remaining = len(sats) - i
                if remaining > 0:
                    txt.append(f"  ... +{remaining} more\n", style="dim")
                break

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

        if n_hidden:
            txt.append(f"  ({n_hidden} hidden by filter)\n", style="dim")

        # Legend
        txt.append("\n", style="")
        txt.append(" \u2022", style="white")
        txt.append("=Used  ", style="dim")
        for gnssid, color in sorted(GNSS_COLORS.items()):
            if gnssid in hidden:
                continue
            short = GNSS_SHORT.get(gnssid, "??")
            txt.append(f"\u2588{short} ", style=Style(color=color))

        return txt
