"""Constellation summary panel — per-constellation satellite counts."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from ..constants import GNSS_COLORS, GNSS_NAMES
from ..data_model import GPSData


class ConstellationPanel(Static):
    """Displays per-constellation satellite counts."""

    DEFAULT_CSS = """
    ConstellationPanel {
        height: auto;
        min-height: 5;
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
        txt.append("Constellations\n", style="bold")
        txt.append("─" * 50 + "\n", style="bright_black")

        if not self._data or not self._data.satellites:
            txt.append("  No satellites in view\n", style="dim")
            return txt

        counts = self._data.constellation_counts
        total_vis = 0
        total_used = 0

        # Display each constellation on a line, two per row
        entries = []
        for gnssid in sorted(counts.keys()):
            vis, used = counts[gnssid]
            total_vis += vis
            total_used += used
            name = GNSS_NAMES.get(gnssid, f"GNSS-{gnssid}")
            color = GNSS_COLORS.get(gnssid, "white")
            entries.append((name, vis, used, color))

        # Two columns
        for i in range(0, len(entries), 2):
            name1, vis1, used1, color1 = entries[i]
            txt.append(f"  {name1:<8}", style=Style(color=color1))
            txt.append(f" {used1:>2}/{vis1:<2}", style="white")

            if i + 1 < len(entries):
                name2, vis2, used2, color2 = entries[i + 1]
                txt.append("   ", style="")
                txt.append(f"{name2:<8}", style=Style(color=color2))
                txt.append(f" {used2:>2}/{vis2:<2}", style="white")
            txt.append("\n")

        txt.append("  ─────────────────\n", style="bright_black")
        txt.append(f"  Total:   ", style="bright_black")
        txt.append(f"{total_used}", style="green bold")
        txt.append(f" used / ", style="white")
        txt.append(f"{total_vis}", style="white")
        txt.append(f" visible\n", style="white")

        return txt
