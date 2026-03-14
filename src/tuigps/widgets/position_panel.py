"""Position panel — lat, lon, altitude, geoid separation, position hold."""

from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

from ..data_model import GPSData
from ..formatting import fmt, fmt_altitude, fmt_coord
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

    def __init__(self, coord_format: str = "dd", units: str = "metric", **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self._hold: HoldResult | None = None
        self.coord_format = coord_format
        self.units = units

    def set_hold_data(self, result: HoldResult | None) -> None:
        self._hold = result

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

        # Position hold statistics
        if self._hold and self._hold.fix_count > 0:
            h = self._hold
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
                txt.append(f"{h.std_north:.3f} m\n")
                txt.append("  Std E/W: ", style="bright_black")
                txt.append(f"{h.std_east:.3f} m\n")

                if math.isfinite(h.std_alt):
                    txt.append("  Std Alt: ", style="bright_black")
                    txt.append(f"{h.std_alt:.3f} m\n")

                # CEP with color coding
                txt.append("  CEP50:   ", style="bright_black")
                cep_color = "green" if h.cep50 < 2 else "yellow" if h.cep50 < 5 else "red"
                txt.append(f"{h.cep50:.3f} m\n", style=cep_color)

                txt.append("  CEP95:   ", style="bright_black")
                cep95_color = "green" if h.cep95 < 5 else "yellow" if h.cep95 < 10 else "red"
                txt.append(f"{h.cep95:.3f} m\n", style=cep95_color)

        return txt
