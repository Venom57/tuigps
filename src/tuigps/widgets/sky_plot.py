"""Sky plot — polar projection of satellite positions by azimuth/elevation."""

from __future__ import annotations

import math
from itertools import groupby

from rich.style import Style
from rich.text import Span, Text
from textual.reactive import reactive
from textual.widgets import Static

from ..constants import GNSS_COLORS
from ..data_model import GPSData

# A cell's style key: (color, bold). Style objects are shared through a cache.
StyleKey = tuple[str, bool]
DEFAULT_KEY: StyleKey = ("white", False)
GRID_KEY: StyleKey = ("bright_black", False)
_STYLES: dict[StyleKey, Style] = {}


def _style(key: StyleKey) -> Style:
    style = _STYLES.get(key)
    if style is None:
        style = _STYLES[key] = Style(color=key[0], bold=key[1])
    return style


def _runs(keys: list[StyleKey], offset: int) -> list[Span]:
    """Spans for runs of equal style keys (one span per run, not per cell)."""
    spans = []
    x = offset
    for key, group in groupby(keys):
        n = sum(1 for _ in group)
        spans.append(Span(x, x + n, _style(key)))
        x += n
    return spans


class _Background:
    """The static part of the plot for one size: rings, crosshairs and labels."""

    def __init__(self, w: int, h: int):
        self.cx, self.cy = cx, cy = w // 2, h // 2
        # Leave room for labels; aspect ratio correction (chars are ~2:1)
        self.max_rx = max_rx = max(cx - 4, 8)
        self.max_ry = max_ry = max(cy - 2, 4)
        buf = [[" "] * w for _ in range(h)]
        keys = [[DEFAULT_KEY] * w for _ in range(h)]

        # Concentric elevation rings at 0, 30, 60 degrees
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
                    keys[y][x] = GRID_KEY

        # Crosshairs (w, h >= 1, so the center cell is always inside the plot)
        for x in range(cx - max_rx, cx + max_rx + 1):
            if 0 <= x < w and buf[cy][x] == " ":
                buf[cy][x] = "-"
                keys[cy][x] = GRID_KEY
        for y in range(cy - max_ry, cy + max_ry + 1):
            if 0 <= y < h and buf[y][cx] == " ":
                buf[y][cx] = "|"
                keys[y][cx] = GRID_KEY

        # Center: zenith marker
        buf[cy][cx] = "+"
        keys[cy][cx] = DEFAULT_KEY

        # Cardinal direction labels
        for label, lx, ly in (
            ("N", cx, cy - max_ry - 1),
            ("S", cx, cy + max_ry + 1),
            ("E", cx + max_rx + 2, cy),
            ("W", cx - max_rx - 2, cy),
        ):
            if 0 <= lx < w and 0 <= ly < h:
                buf[ly][lx] = label
                keys[ly][lx] = ("bright_white", True)

        # Elevation labels on the horizontal axis
        for elev_deg in (30, 60):
            r_frac = (90 - elev_deg) / 90.0
            lx = cx + int(r_frac * max_rx) + 1
            label = f"{elev_deg}\u00b0"
            if lx + len(label) < w and 0 <= cy - 1 < h:
                for i, ch in enumerate(label):
                    if 0 <= lx + i < w and buf[cy - 1][lx + i] == " ":
                        buf[cy - 1][lx + i] = ch
                        keys[cy - 1][lx + i] = GRID_KEY

        self.chars = buf
        self.keys = keys
        # Pre-rendered rows: (text, spans at the row's offset in the plot text;
        # every row is exactly w characters plus a newline)
        self.rows = [
            ("".join(row), _runs(krow, y * (w + 1)))
            for y, (row, krow) in enumerate(zip(buf, keys))
        ]


class SkyPlot(Static):
    """ASCII polar sky plot showing satellite positions."""

    DEFAULT_CSS = """
    SkyPlot {
        height: 100%;
        padding: 0;
    }
    """

    MAX_CACHED_SIZES = 4

    # gnssids hidden by the constellation display filter (changing it repaints)
    hidden_gnss: reactive[frozenset[int]] = reactive(frozenset())

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self._backgrounds: dict[tuple[int, int], _Background] = {}

    def update_gps_data(self, data: GPSData) -> None:
        if data is self._data:
            return  # same immutable snapshot: nothing new to draw
        self._data = data
        self.refresh()

    def _background(self, w: int, h: int) -> _Background:
        bg = self._backgrounds.get((w, h))
        if bg is None:
            if len(self._backgrounds) >= self.MAX_CACHED_SIZES:
                self._backgrounds.clear()
            bg = self._backgrounds[(w, h)] = _Background(w, h)
        return bg

    def render(self) -> Text:
        w = self.size.width or 60
        h = self.size.height or 20
        bg = self._background(w, h)
        cx, cy, max_rx, max_ry = bg.cx, bg.cy, bg.max_rx, bg.max_ry

        # Rows touched by satellites are copied from the background lazily
        chars: dict[int, list[str]] = {}
        keys: dict[int, list[StyleKey]] = {}

        hidden = self.hidden_gnss
        if self._data and self._data.satellites:
            for sat in self._data.satellites:
                if sat.gnssid in hidden:
                    continue
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

                row = chars.get(sy)
                if row is None:
                    row = chars[sy] = list(bg.chars[sy])
                    keys[sy] = list(bg.keys[sy])
                krow = keys[sy]

                # Satellite marker
                sat_color = GNSS_COLORS.get(sat.gnssid, "white")
                if sat.used:
                    row[sx] = "#"
                    krow[sx] = (sat_color, True)
                else:
                    row[sx] = "o"
                    krow[sx] = (sat_color, krow[sx][1])

                # Label: just svid number to reduce clutter
                for i, ch in enumerate(str(sat.svid)):
                    lx = sx + 1 + i
                    if 0 <= lx < w - 1 and row[lx] == " ":
                        row[lx] = ch
                        krow[lx] = (sat_color, krow[lx][1])

        # Compose into Rich Text: one string, one span per run of equal style
        lines: list[str] = []
        spans: list[Span] = []
        for y, (line, row_spans) in enumerate(bg.rows):
            row = chars.get(y)
            if row is None:
                spans.extend(row_spans)
            else:
                line = "".join(row)
                spans.extend(_runs(keys[y], y * (w + 1)))
            lines.append(line)
        return Text("\n".join(lines), spans=spans)
