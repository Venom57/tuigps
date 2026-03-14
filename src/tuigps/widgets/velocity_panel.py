"""Velocity panel — speed, heading, climb rate."""

from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

from ..constants import bearing_to_compass
from ..data_model import GPSData
from ..formatting import fmt, fmt_speed


class VelocityPanel(Static):
    """Displays velocity information."""

    DEFAULT_CSS = """
    VelocityPanel {
        height: auto;
        min-height: 8;
        padding: 0 1;
    }
    """

    def __init__(self, units: str = "metric", **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self.units = units

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        self.refresh()

    def render(self) -> Text:
        txt = Text()
        txt.append("Velocity\n", style="bold")
        txt.append("─" * 24 + "\n", style="bright_black")

        if not self._data:
            txt.append("  No data\n", style="dim")
            return txt

        d = self._data

        # Speed
        txt.append("  Speed:  ", style="bright_black")
        txt.append(fmt_speed(d.speed, self.units) + "\n")

        # Heading / Track
        txt.append("  Track:  ", style="bright_black")
        if math.isfinite(d.track):
            compass = bearing_to_compass(d.track)
            txt.append(f"{d.track:.1f}\u00b0 ({compass})\n")
        else:
            txt.append("---\n")

        # Magnetic track
        txt.append("  Mag:    ", style="bright_black")
        if math.isfinite(d.magtrack):
            txt.append(f"{d.magtrack:.1f}\u00b0\n")
        else:
            txt.append("---\n")

        # Climb
        txt.append("  Climb:  ", style="bright_black")
        txt.append(fmt(d.climb, ".2f", " m/s") + "\n")

        # Magnetic variation
        txt.append("  MagVar: ", style="bright_black")
        if math.isfinite(d.magvar):
            direction = "E" if d.magvar >= 0 else "W"
            txt.append(f"{abs(d.magvar):.1f}\u00b0 {direction}\n")
        else:
            txt.append("---\n")

        return txt
