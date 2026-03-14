"""Error estimates panel — EPH, EPV, EPT, EPS, EPD, EPC."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..data_model import GPSData
from ..formatting import fmt


class ErrorPanel(Static):
    """Displays GPS error estimates."""

    DEFAULT_CSS = """
    ErrorPanel {
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

    def render(self) -> Text:
        txt = Text()
        txt.append("Error Estimates\n", style="bold")
        txt.append("─" * 24 + "\n", style="bright_black")

        if not self._data:
            txt.append("  No data\n", style="dim")
            return txt

        e = self._data.errors
        rows = [
            ("EPH", e.eph, ".1f", " m", "Horizontal"),
            ("EPV", e.epv, ".1f", " m", "Vertical"),
            ("EPT", e.ept, ".4f", " s", "Time"),
            ("EPS", e.eps, ".2f", " m/s", "Speed"),
            ("EPD", e.epd, ".1f", "\u00b0", "Direction"),
            ("EPC", e.epc, ".2f", " m/s", "Climb"),
        ]

        for label, value, spec, suffix, desc in rows:
            txt.append(f"  {label}: ", style="bright_black")
            val = fmt(value, spec, suffix)
            if val != "---":
                txt.append(f"\u00b1{val}\n")
            else:
                txt.append(f"{val}\n", style="bright_black")

        return txt
