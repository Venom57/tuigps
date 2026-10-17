"""Constellation summary panel — per-constellation satellite counts."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..constants import GNSS_COLORS, GNSS_NAMES
from ..data_model import GPSData
from ..formatting import trim


class ConstellationPanel(Static):
    """Displays per-constellation satellite counts."""

    DEFAULT_CSS = """
    ConstellationPanel {
        height: auto;
        min-height: 5;
        padding: 0 1;
    }
    """

    # gnssids hidden by the constellation display filter: still counted
    # here, but marked "(hidden)" (changing it repaints)
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
        txt.append("Constellations\n", style="bold")
        txt.append("─" * 50 + "\n", style="bright_black")

        if not self._data or not self._data.satellites:
            txt.append("  No satellites in view\n", style="dim")
            return trim(txt)

        counts = self._data.constellation_counts
        total_vis = 0
        total_used = 0

        # Counts cover every constellation; hidden ones are marked
        hidden = self.hidden_gnss
        entries = []
        for gnssid in sorted(counts.keys()):
            vis, used = counts[gnssid]
            total_vis += vis
            total_used += used
            name = GNSS_NAMES.get(gnssid, f"GNSS-{gnssid}")
            color = GNSS_COLORS.get(gnssid, "white")
            entries.append((name, vis, used, color, gnssid in hidden))

        # Two columns
        for i in range(0, len(entries), 2):
            pair = entries[i:i + 2]
            for j, (name, vis, used, color, is_hidden) in enumerate(pair):
                txt.append("   " if j else "  ")
                if is_hidden:
                    txt.append(f"{name:<8} {used:>2}/{vis:<2}", style="dim")
                    txt.append(" (hidden)", style="dim italic")
                else:
                    txt.append(f"{name:<8}", style=Style(color=color))
                    txt.append(f" {used:>2}/{vis:<2}", style="white")
                    if j == 0 and len(pair) > 1:
                        txt.append(" " * 9)  # keep the second column aligned
            txt.append("\n")

        txt.append("  ─────────────────\n", style="bright_black")
        txt.append(f"  Total:   ", style="bright_black")
        txt.append(f"{total_used}", style="green bold")
        txt.append(f" used / ", style="white")
        txt.append(f"{total_vis}", style="white")
        txt.append(f" visible\n", style="white")

        return trim(txt)
