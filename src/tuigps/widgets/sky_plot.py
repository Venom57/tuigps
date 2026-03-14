"""Sky plot — polar projection of satellite positions by azimuth/elevation."""

from __future__ import annotations

import math

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from ..constants import GNSS_COLORS, GNSS_SHORT
from ..data_model import GPSData


class SkyPlot(Static):
    """ASCII polar sky plot showing satellite positions."""

    DEFAULT_CSS = """
    SkyPlot {
        height: 100%;
        padding: 0;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        self.refresh()

    def render(self) -> Text:
        w = self.size.width or 60
        h = self.size.height or 20

        # Character and color buffers
        buf = [[" "] * w for _ in range(h)]
        col = [["white"] * w for _ in range(h)]
        bold = [[False] * w for _ in range(h)]

        cx, cy = w // 2, h // 2
        # Leave room for labels; aspect ratio correction (chars are ~2:1)
        max_rx = max(cx - 4, 8)
        max_ry = max(cy - 2, 4)

        # Draw concentric elevation rings at 0, 30, 60 degrees
        for elev_deg in (0, 30, 60):
            r_frac = (90 - elev_deg) / 90.0
            rx = int(r_frac * max_rx)
            ry = int(r_frac * max_ry)
            if rx < 1 or ry < 1:
                continue
            # Sample points around the ring
            for angle_deg in range(0, 360, 2):
                rad = math.radians(angle_deg)
                x = cx + int(rx * math.sin(rad))
                y = cy - int(ry * math.cos(rad))
                if 0 <= x < w and 0 <= y < h and buf[y][x] == " ":
                    buf[y][x] = "."
                    col[y][x] = "bright_black"

        # Draw crosshairs
        for x in range(cx - max_rx, cx + max_rx + 1):
            if 0 <= x < w and buf[cy][x] == " ":
                buf[cy][x] = "-"
                col[cy][x] = "bright_black"
        for y in range(cy - max_ry, cy + max_ry + 1):
            if 0 <= y < h and buf[y][cx] == " ":
                buf[y][cx] = "|"
                col[y][cx] = "bright_black"

        # Center — zenith marker
        buf[cy][cx] = "+"
        col[cy][cx] = "white"

        # Cardinal direction labels
        labels = [
            ("N", cx, cy - max_ry - 1),
            ("S", cx, cy + max_ry + 1),
            ("E", cx + max_rx + 2, cy),
            ("W", cx - max_rx - 2, cy),
        ]
        for label, lx, ly in labels:
            if 0 <= lx < w and 0 <= ly < h:
                buf[ly][lx] = label
                col[ly][lx] = "bright_white"
                bold[ly][lx] = True

        # Elevation labels on the horizontal axis
        for elev_deg in (30, 60):
            r_frac = (90 - elev_deg) / 90.0
            lx = cx + int(r_frac * max_rx) + 1
            label = f"{elev_deg}\u00b0"
            if lx + len(label) < w and 0 <= cy - 1 < h:
                for i, ch in enumerate(label):
                    if 0 <= lx + i < w and buf[cy - 1][lx + i] == " ":
                        buf[cy - 1][lx + i] = ch
                        col[cy - 1][lx + i] = "bright_black"

        # Plot satellites
        if self._data and self._data.satellites:
            for sat in self._data.satellites:
                if not math.isfinite(sat.elevation) or not math.isfinite(sat.azimuth):
                    continue
                if sat.elevation < 0:
                    continue

                r_frac = (90 - sat.elevation) / 90.0
                az_rad = math.radians(sat.azimuth)
                sx = cx + int(r_frac * max_rx * math.sin(az_rad))
                sy = cy - int(r_frac * max_ry * math.cos(az_rad))

                if not (0 <= sx < w and 0 <= sy < h):
                    continue

                # Satellite marker
                sat_color = GNSS_COLORS.get(sat.gnssid, "white")
                if sat.used:
                    buf[sy][sx] = "#"
                    col[sy][sx] = sat_color
                    bold[sy][sx] = True
                else:
                    buf[sy][sx] = "o"
                    col[sy][sx] = sat_color

                # Label: just svid number to reduce clutter
                label = str(sat.svid)
                for i, ch in enumerate(label):
                    lx = sx + 1 + i
                    if 0 <= lx < w - 1 and buf[sy][lx] == " ":
                        buf[sy][lx] = ch
                        col[sy][lx] = sat_color

        # Compose into Rich Text
        txt = Text()
        for y in range(h):
            for x in range(w):
                style = Style(color=col[y][x], bold=bold[y][x])
                txt.append(buf[y][x], style=style)
            if y < h - 1:
                txt.append("\n")

        return txt
