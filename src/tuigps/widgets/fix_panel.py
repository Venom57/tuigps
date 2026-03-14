"""Fix quality panel — mode, status, DOP values."""

from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

from ..constants import MODE_COLORS, MODE_NAMES, STATUS_COLORS, STATUS_NAMES, dop_rating
from ..data_model import GPSData
from ..formatting import fmt


class FixPanel(Static):
    """Displays fix quality and DOP values."""

    DEFAULT_CSS = """
    FixPanel {
        height: auto;
        min-height: 8;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        self.refresh()

    def _fmt_dop(self, value: float) -> tuple[str, str]:
        """Format a DOP value with rating color."""
        if not math.isfinite(value):
            return "---", "bright_black"
        label, color = dop_rating(value)
        return f"{value:.1f} ({label})", color

    def render(self) -> Text:
        txt = Text()
        txt.append("Fix Quality\n", style="bold")
        txt.append("─" * 24 + "\n", style="bright_black")

        if not self._data:
            txt.append("  No data\n", style="dim")
            return txt

        d = self._data

        # Mode
        mode_name = MODE_NAMES.get(d.mode, "Unknown")
        mode_color = MODE_COLORS.get(d.mode, "bright_black")
        txt.append("  Mode:   ", style="bright_black")
        txt.append(f"{mode_name}\n", style=mode_color)

        # Status
        status_name = STATUS_NAMES.get(d.status, "Unknown")
        status_color = STATUS_COLORS.get(d.status, "bright_black")
        txt.append("  Status: ", style="bright_black")
        txt.append(f"{status_name}\n", style=status_color)

        # Sats used
        txt.append("  Sats:   ", style="bright_black")
        if d.satellites_used > 0:
            txt.append(f"{d.satellites_used} used", style="green")
            txt.append(f" / {len(d.satellites)} vis\n")
        else:
            txt.append("---\n", style="bright_black")

        # DOP values
        dops = [
            ("HDOP", d.dop.hdop),
            ("VDOP", d.dop.vdop),
            ("PDOP", d.dop.pdop),
            ("GDOP", d.dop.gdop),
            ("TDOP", d.dop.tdop),
        ]
        for label, value in dops:
            val_str, color = self._fmt_dop(value)
            txt.append(f"  {label}:   ", style="bright_black")
            txt.append(f"{val_str}\n", style=color)

        return txt
