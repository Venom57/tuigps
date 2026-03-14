"""Position panel — lat, lon, altitude, geoid separation."""

from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

from ..data_model import GPSData
from ..formatting import fmt, fmt_altitude, fmt_coord


class PositionPanel(Static):
    """Displays current GPS position data."""

    DEFAULT_CSS = """
    PositionPanel {
        height: auto;
        min-height: 8;
        padding: 0 1;
    }
    """

    def __init__(self, coord_format: str = "dd", units: str = "metric", **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self.coord_format = coord_format
        self.units = units

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        self.refresh()

    def render(self) -> Text:
        txt = Text()
        txt.append("Position\n", style="bold")
        txt.append("─" * 24 + "\n", style="bright_black")

        if not self._data:
            txt.append("  No data\n", style="dim")
            return txt

        d = self._data
        cf = self.coord_format

        txt.append("  Lat:  ", style="bright_black")
        txt.append(fmt_coord(d.latitude, "lat", cf) + "\n")

        txt.append("  Lon:  ", style="bright_black")
        txt.append(fmt_coord(d.longitude, "lon", cf) + "\n")

        txt.append("  Alt HAE: ", style="bright_black")
        txt.append(fmt_altitude(d.alt_hae, self.units) + "\n")

        txt.append("  Alt MSL: ", style="bright_black")
        txt.append(fmt_altitude(d.alt_msl, self.units) + "\n")

        txt.append("  Geoid:   ", style="bright_black")
        txt.append(fmt(d.geoid_sep, ".1f", " m") + "\n")

        if d.has_fix:
            url = f"https://maps.google.com/?q={d.latitude:.6f},{d.longitude:.6f}"
            txt.append("  Maps:    ", style="bright_black")
            txt.append("Open in Google Maps", style=f"underline dodger_blue2 link {url}")
            txt.append("  [m]\n", style="dim")

        return txt
