"""Combined status bar — keybindings on the left, GPS status on the right."""

from __future__ import annotations

import time

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from ..constants import MODE_NAMES, STATUS_COLORS, STATUS_NAMES
from ..data_model import GPSData


class ConnectionStatus(Static):
    """Bottom bar with key bindings and GPS connection status."""

    DEFAULT_CSS = """
    ConnectionStatus {
        height: 1;
        dock: bottom;
        width: 100%;
        padding: 0 0;
    }
    """

    BINDINGS_DISPLAY = [
        ("q", "Quit"),
        ("t", "Theme"),
        ("d", "Dark/Light"),
        ("s", "Settings"),
        ("r", "Reconnect"),
        ("u", "Units"),
        ("m", "Maps"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        self.refresh()

    def render(self) -> Text:
        w = self.size.width or 80

        # Left side: key bindings
        left = Text()
        for key, label in self.BINDINGS_DISPLAY:
            left.append(f" {key} ", style=Style(color="black", bgcolor="white", bold=True))
            left.append(f" {label} ", style="white")

        # Right side: GPS status
        right = self._render_status()

        # Pad the middle to push right side to the edge
        left_len = left.cell_len
        right_len = right.cell_len
        gap = max(1, w - left_len - right_len)

        txt = Text()
        txt.append_text(left)
        txt.append(" " * gap)
        txt.append_text(right)
        return txt

    def _render_status(self) -> Text:
        txt = Text()

        if not self._data or not self._data.connected:
            txt.append(" DISCONNECTED ", style=Style(color="white", bgcolor="red", bold=True))
            return txt

        d = self._data
        age = time.time() - d.last_seen if d.last_seen > 0 else 999

        if age > 10:
            txt.append(f" STALE ({int(age)}s) ", style=Style(color="black", bgcolor="yellow", bold=True))
            return txt

        # Connected with data
        mode_name = MODE_NAMES.get(d.mode, "?")
        status_name = STATUS_NAMES.get(d.status, "?")
        status_color = STATUS_COLORS.get(d.status, "green")

        if d.satellites_used > 0:
            txt.append(f"{d.satellites_used}", style="green bold")
            txt.append(f"/{len(d.satellites)}sv ", style="white")

        if d.device.path:
            txt.append(f"{d.device.path} ", style="dim")

        txt.append(f" {mode_name} ", style=Style(color="black", bgcolor=status_color, bold=True))
        txt.append(f" {status_name} ", style=status_color)

        return txt
