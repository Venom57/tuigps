"""Satellite detail table — full DataTable with all satellite info."""

from __future__ import annotations

import math

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from ..constants import GNSS_COLORS, GNSS_NAMES, GNSS_SHORT
from ..data_model import GPSData


class SatelliteTable(Static):
    """Displays detailed satellite information in a table format."""

    DEFAULT_CSS = """
    SatelliteTable {
        height: 1fr;
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
        txt.append("Satellite Details\n", style="bold")

        # Header
        header = f"{'GNSS':<6} {'PRN':>4} {'SV':>3} {'El\u00b0':>5} {'Az\u00b0':>5} {'SNR':>5} {'Used':>5} {'Sig':>4} {'Hlth':>5}"
        txt.append(header + "\n", style="bold bright_white")
        txt.append("\u2500" * len(header) + "\n", style="bright_black")

        if not self._data or not self._data.satellites:
            txt.append("  No satellites in view\n", style="dim")
            return txt

        # Sort by constellation, then by PRN
        sats = sorted(self._data.satellites, key=lambda s: (s.gnssid, s.prn))

        for sat in sats:
            color = GNSS_COLORS.get(sat.gnssid, "white")
            prefix = GNSS_SHORT.get(sat.gnssid, "??")

            # Constellation
            txt.append(f"{prefix:<6}", style=Style(color=color))

            # PRN
            txt.append(f" {sat.prn:>4}", style="white")

            # SV ID
            txt.append(f" {sat.svid:>3}", style="white")

            # Elevation
            if math.isfinite(sat.elevation):
                txt.append(f" {sat.elevation:>5.0f}", style="white")
            else:
                txt.append(f" {'---':>5}", style="bright_black")

            # Azimuth
            if math.isfinite(sat.azimuth):
                txt.append(f" {sat.azimuth:>5.0f}", style="white")
            else:
                txt.append(f" {'---':>5}", style="bright_black")

            # SNR
            if math.isfinite(sat.snr) and sat.snr > 0:
                snr_color = "green" if sat.snr >= 30 else "yellow" if sat.snr >= 20 else "red"
                txt.append(f" {sat.snr:>5.0f}", style=snr_color)
            else:
                txt.append(f" {'---':>5}", style="bright_black")

            # Used
            if sat.used:
                txt.append(f"   {'*':>2}", style="green bold")
            else:
                txt.append(f"   {' ':>2}", style="bright_black")

            # Signal ID
            txt.append(f" {sat.sigid:>4}", style="bright_black")

            # Health
            health_str = "OK" if sat.health == 0 else f"{sat.health}"
            health_color = "green" if sat.health == 0 else "red"
            txt.append(f" {health_str:>5}", style=health_color)

            txt.append("\n")

        return txt
