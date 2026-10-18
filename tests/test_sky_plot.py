"""SkyPlot rendering: the optimized renderer (P4) must match the original.

``reference_render`` is a verbatim copy of the original per-cell algorithm
(one Style object and one append per cell, background redrawn every frame).
"""

from __future__ import annotations

import math
import random

import pytest
from rich.style import Style
from rich.text import Text
from textual.geometry import Size

from tests.conftest import sample_satellites
from tuigps.constants import GNSS_COLORS
from tuigps.data_model import GPSData, SatelliteInfo
from tuigps.widgets.sky_plot import SkyPlot


def reference_render(data: GPSData | None, w: int, h: int) -> Text:
    """The original SkyPlot.render (before Phase 3), kept as the reference."""
    buf = [[" "] * w for _ in range(h)]
    col = [["white"] * w for _ in range(h)]
    bold = [[False] * w for _ in range(h)]

    cx, cy = w // 2, h // 2
    max_rx = max(cx - 4, 8)
    max_ry = max(cy - 2, 4)

    for elev_deg in (0, 30, 60):
        r_frac = (90 - elev_deg) / 90.0
        rx = int(r_frac * max_rx)
        ry = int(r_frac * max_ry)
        if rx < 1 or ry < 1:
            continue
        for angle_deg in range(0, 360, 2):
            rad = math.radians(angle_deg)
            x = cx + int(rx * math.sin(rad))
            y = cy - int(ry * math.cos(rad))
            if 0 <= x < w and 0 <= y < h and buf[y][x] == " ":
                buf[y][x] = "."
                col[y][x] = "bright_black"

    for x in range(cx - max_rx, cx + max_rx + 1):
        if 0 <= x < w and buf[cy][x] == " ":
            buf[cy][x] = "-"
            col[cy][x] = "bright_black"
    for y in range(cy - max_ry, cy + max_ry + 1):
        if 0 <= y < h and buf[y][cx] == " ":
            buf[y][cx] = "|"
            col[y][cx] = "bright_black"

    buf[cy][cx] = "+"
    col[cy][cx] = "white"

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

    for elev_deg in (30, 60):
        r_frac = (90 - elev_deg) / 90.0
        lx = cx + int(r_frac * max_rx) + 1
        label = f"{elev_deg}°"
        if lx + len(label) < w and 0 <= cy - 1 < h:
            for i, ch in enumerate(label):
                if 0 <= lx + i < w and buf[cy - 1][lx + i] == " ":
                    buf[cy - 1][lx + i] = ch
                    col[cy - 1][lx + i] = "bright_black"

    if data and data.satellites:
        for sat in data.satellites:
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

            sat_color = GNSS_COLORS.get(sat.gnssid, "white")
            if sat.used:
                buf[sy][sx] = "#"
                col[sy][sx] = sat_color
                bold[sy][sx] = True
            else:
                buf[sy][sx] = "o"
                col[sy][sx] = sat_color

            label = str(sat.svid)
            for i, ch in enumerate(label):
                lx = sx + 1 + i
                if 0 <= lx < w - 1 and buf[sy][lx] == " ":
                    buf[sy][lx] = ch
                    col[sy][lx] = sat_color

    txt = Text()
    for y in range(h):
        for x in range(w):
            style = Style(color=col[y][x], bold=bold[y][x])
            txt.append(buf[y][x], style=style)
        if y < h - 1:
            txt.append("\n")

    return txt


class FixedSizeSkyPlot(SkyPlot):
    """A SkyPlot that renders at a given size without being mounted."""

    def __init__(self, width: int, height: int):
        super().__init__()
        self._fixed_size = Size(width, height)

    @property
    def size(self) -> Size:
        return self._fixed_size


def random_satellites(n: int, seed: int) -> list[SatelliteInfo]:
    rng = random.Random(seed)
    sats = []
    for i in range(n):
        sats.append(SatelliteInfo(
            prn=i + 1,
            gnssid=rng.choice([0, 1, 2, 3, 5, 6, 9]),  # 9: unknown -> default color
            svid=rng.choice([1, 7, 12, 33, 120, 195]),
            elevation=rng.choice([rng.uniform(-10, 90), 90.0, 0.0, 30.0, 60.0]),
            azimuth=rng.choice([rng.uniform(0, 360), 0.0, 90.0, 180.0, 270.0]),
            snr=rng.uniform(0, 50),
            used=rng.random() < 0.5,
        ))
    return sats


def cell_styles(text: Text) -> list[Style]:
    """The effective style of every character (spans applied in order)."""
    styles = [Style.null()] * len(text.plain)
    for span in text.spans:
        style = span.style if isinstance(span.style, Style) else Style.parse(span.style)
        for i in range(span.start, span.end):
            styles[i] = styles[i] + style
    return styles


SIZES = [(60, 20), (120, 40), (61, 21), (30, 10), (17, 9), (10, 5), (3, 2), (1, 1), (200, 60)]
DATASETS = {
    "none": None,
    "empty": GPSData(),
    "sample": GPSData(satellites=sample_satellites()),
    "random40": GPSData(satellites=random_satellites(40, 1)),
    "random80": GPSData(satellites=random_satellites(80, 2)),
    "crowded": GPSData(satellites=[  # overlapping markers/labels, used over unused and back
        SatelliteInfo(gnssid=0, svid=5, elevation=45.0, azimuth=45.0, used=False),
        SatelliteInfo(gnssid=2, svid=11, elevation=45.0, azimuth=45.0, used=True),
        SatelliteInfo(gnssid=1, svid=133, elevation=45.0, azimuth=45.0, used=False),
        SatelliteInfo(gnssid=6, svid=8, elevation=90.0, azimuth=0.0, used=False),  # on "+"
        SatelliteInfo(gnssid=3, svid=19, elevation=0.0, azimuth=0.0, used=False),  # near "N"
        SatelliteInfo(gnssid=0, svid=2, elevation=0.0, azimuth=90.0, used=True),
    ]),
}


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("dataset", list(DATASETS))
def test_render_matches_reference(size, dataset):
    w, h = size
    data = DATASETS[dataset]
    sky = FixedSizeSkyPlot(w, h)
    sky._data = data
    expected = reference_render(data, w, h)
    for _ in range(2):  # the second render uses the cached background
        got = sky.render()
        assert got.plain == expected.plain
        assert cell_styles(got) == cell_styles(expected)
        assert got.style == expected.style and got.end == expected.end


def test_render_uses_run_length_spans_and_shared_styles():
    sky = FixedSizeSkyPlot(120, 40)
    sky._data = DATASETS["random40"]
    text = sky.render()
    assert len(text.spans) < 120 * 40 / 4  # far fewer spans than cells
    assert len({id(s.style) for s in text.spans}) == len({s.style for s in text.spans})


def test_background_cache_is_per_size_and_bounded():
    sky = FixedSizeSkyPlot(60, 20)
    sky.render()
    bg = sky._backgrounds[(60, 20)]
    sky.render()
    assert sky._backgrounds[(60, 20)] is bg
    for w in range(30, 30 + SkyPlot.MAX_CACHED_SIZES + 2):
        sky._fixed_size = Size(w, 20)
        sky.render()
    assert len(sky._backgrounds) <= SkyPlot.MAX_CACHED_SIZES


def test_satellites_do_not_modify_cached_background():
    sky = FixedSizeSkyPlot(60, 20)
    sky._data = DATASETS["random40"]
    sky.render()
    sky._data = None
    assert sky.render().plain == reference_render(None, 60, 20).plain
