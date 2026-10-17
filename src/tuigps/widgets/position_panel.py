"""Position panel — lat, lon, altitude, geoid separation, position hold."""

from __future__ import annotations

import math

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..data_model import GPSData
from ..formatting import fmt_altitude, fmt_coord, fmt_distance
from ..position_hold import HoldResult


class PositionPanel(Static):
    """Displays current GPS position data."""

    DEFAULT_CSS = """
    PositionPanel {
        height: auto;
        min-height: 8;
        padding: 0 1;
    }
    """

    # Display settings: changing one repaints the panel
    coord_format = reactive("dd")
    units = reactive("metric")

    def __init__(self, coord_format: str = "dd", units: str = "metric", **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self._hold: HoldResult | None = None
        self.coord_format = coord_format
        self.units = units

    def set_hold_data(self, result: HoldResult | None) -> None:
        if result is None and self._hold is None:
            return
        self._hold = result  # a new result each time (its duration keeps growing)
        self.refresh()

    def update_gps_data(self, data: GPSData) -> None:
        if data is self._data:
            return  # same immutable snapshot: nothing new to draw
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
        txt.append(fmt_distance(d.geoid_sep, self.units) + "\n")

        if d.has_fix:
            url = f"https://maps.google.com/?q={d.latitude:.6f},{d.longitude:.6f}"
            txt.append("  Maps:    ", style="bright_black")
            txt.append("Open in Google Maps", style=f"underline dodger_blue2 link {url}")
            txt.append("  [m]\n", style="dim")

        # Position hold statistics
        if self._hold and self._hold.fix_count > 0:
            h = self._hold
            u = self.units
            mins = int(h.duration) // 60
            secs = int(h.duration) % 60
            txt.append(f"─ Hold ({h.fix_count} fixes, {mins}:{secs:02d}) ─\n", style="bold cyan")

            txt.append("  Avg Lat: ", style="bright_black")
            txt.append(fmt_coord(h.mean_lat, "lat", cf) + "\n")
            txt.append("  Avg Lon: ", style="bright_black")
            txt.append(fmt_coord(h.mean_lon, "lon", cf) + "\n")

            if math.isfinite(h.mean_alt):
                txt.append("  Avg Alt: ", style="bright_black")
                txt.append(fmt_altitude(h.mean_alt, self.units) + "\n")

            if math.isfinite(h.std_north) and h.fix_count >= 2:
                txt.append("  Std N/S: ", style="bright_black")
                txt.append(fmt_distance(h.std_north, u, ".3f") + "\n")
                txt.append("  Std E/W: ", style="bright_black")
                txt.append(fmt_distance(h.std_east, u, ".3f") + "\n")

                if math.isfinite(h.std_alt):
                    txt.append("  Std Alt: ", style="bright_black")
                    txt.append(fmt_distance(h.std_alt, u, ".3f") + "\n")

                # CEP with color coding (thresholds in meters)
                txt.append("  CEP50:   ", style="bright_black")
                cep_color = "green" if h.cep50 < 2 else "yellow" if h.cep50 < 5 else "red"
                txt.append(fmt_distance(h.cep50, u, ".3f") + "\n", style=cep_color)

                txt.append("  CEP95:   ", style="bright_black")
                cep95_color = "green" if h.cep95 < 5 else "yellow" if h.cep95 < 10 else "red"
                txt.append(fmt_distance(h.cep95, u, ".3f") + "\n", style=cep95_color)

        return txt
