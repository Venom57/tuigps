"""Error estimates panel — EPH, EPV, EPT, EPS, EPD, EPC (+ EPX/EPY, SEP)."""

from __future__ import annotations

import math

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..data_model import GPSData
from ..formatting import fmt, fmt_climb, fmt_distance, fmt_speed


class ErrorPanel(Static):
    """Displays GPS error estimates."""

    DEFAULT_CSS = """
    ErrorPanel {
        height: auto;
        min-height: 8;
        padding: 0 1;
    }
    """

    units = reactive("metric")  # display setting: changing it repaints the panel

    def __init__(self, units: str = "metric", **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self.units = units

    def update_gps_data(self, data: GPSData) -> None:
        if data is self._data:
            return  # same immutable snapshot: nothing new to draw
        self._data = data
        self.refresh()

    def render(self) -> Text:
        txt = Text()
        txt.append("Error Estimates\n", style="bold")
        txt.append("─" * 24 + "\n", style="bright_black")

        if not self._data:
            txt.append("  No data\n", style="dim")
            return txt

        e = self._data.errors
        u = self.units
        rows = [
            ("EPH", fmt_distance(e.eph, u)),  # horizontal
            ("EPV", fmt_distance(e.epv, u)),  # vertical
            ("EPT", fmt(e.ept, ".4f", " s")),  # time
            ("EPS", fmt_speed(e.eps, u, ".2f")),  # speed
            ("EPD", fmt(e.epd, ".1f", "\u00b0")),  # direction
            ("EPC", fmt_climb(e.epc, u)),  # climb (same unit as the climb rate)
        ]
        # Not every receiver reports these; only shown when present
        if math.isfinite(e.epx) or math.isfinite(e.epy):
            rows.append(("EPX", fmt_distance(e.epx, u)))
            rows.append(("EPY", fmt_distance(e.epy, u)))
        if math.isfinite(e.sep):
            rows.append(("SEP", fmt_distance(e.sep, u)))  # spherical (3D)

        for label, val in rows:
            txt.append(f"  {label}: ", style="bright_black")
            if val != "---":
                txt.append(f"\u00b1{val}\n")
            else:
                txt.append(f"{val}\n", style="bright_black")

        return txt
