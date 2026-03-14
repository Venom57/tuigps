"""Time panel — GPS time, UTC, PPS offset, TOFF."""

from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

from ..data_model import GPSData
from ..formatting import fmt, fmt_time_iso


class TimePanel(Static):
    """Displays GPS time, PPS, and TOFF information."""

    DEFAULT_CSS = """
    TimePanel {
        height: auto;
        min-height: 8;
        padding: 0 1;
    }
    """

    def __init__(self, show_pps: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self.show_pps = show_pps

    def update_gps_data(self, data: GPSData) -> None:
        self._data = data
        self.refresh()

    def render(self) -> Text:
        txt = Text()
        txt.append("Time & Timing\n", style="bold")
        txt.append("─" * 30 + "\n", style="bright_black")

        if not self._data:
            txt.append("  No data\n", style="dim")
            return txt

        d = self._data

        # GPS timestamp
        date_str, time_str = fmt_time_iso(d.time)
        txt.append("  Date:    ", style="bright_black")
        txt.append(f"{date_str}\n")
        txt.append("  Time:    ", style="bright_black")
        txt.append(f"{time_str} UTC\n")

        # Time error estimate
        if math.isfinite(d.errors.ept):
            txt.append("  ept:     ", style="bright_black")
            if d.errors.ept < 0.001:
                txt.append(f"{d.errors.ept * 1e6:.1f} us\n")
            elif d.errors.ept < 1.0:
                txt.append(f"{d.errors.ept * 1e3:.1f} ms\n")
            else:
                txt.append(f"{d.errors.ept:.3f} s\n")

        # Leap seconds
        if d.leapseconds > 0:
            txt.append("  Leap:    ", style="bright_black")
            txt.append(f"{d.leapseconds} s\n")

        if not self.show_pps:
            return txt

        # ── PPS section (timing tab only) ──
        pps = d.pps
        txt.append("\n")
        txt.append("PPS\n", style="bold")
        txt.append("─" * 30 + "\n", style="bright_black")

        if math.isfinite(pps.real_sec):
            offset_us = d.pps_offset_us
            if math.isfinite(offset_us):
                txt.append("  Offset:  ", style="bright_black")
                if abs(offset_us) < 1:
                    txt.append(f"{offset_us * 1000:+.1f} ns\n")
                elif abs(offset_us) < 1000:
                    txt.append(f"{offset_us:+.3f} us\n")
                else:
                    txt.append(f"{offset_us / 1000:+.3f} ms\n")

                # Color-coded quality indicator
                txt.append("  Quality: ", style="bright_black")
                if abs(offset_us) < 1:
                    txt.append("Excellent\n", style="green bold")
                elif abs(offset_us) < 10:
                    txt.append("Good\n", style="green")
                elif abs(offset_us) < 100:
                    txt.append("Fair\n", style="yellow")
                else:
                    txt.append("Poor\n", style="red")

            if pps.precision != 0:
                prec_sec = 2.0 ** pps.precision
                txt.append("  Prec:    ", style="bright_black")
                if prec_sec < 1e-6:
                    txt.append(f"{prec_sec * 1e9:.1f} ns\n")
                elif prec_sec < 1e-3:
                    txt.append(f"{prec_sec * 1e6:.1f} us\n")
                else:
                    txt.append(f"{prec_sec * 1e3:.1f} ms\n")

            if math.isfinite(pps.qerr):
                txt.append("  qErr:    ", style="bright_black")
                txt.append(f"{pps.qerr:.1f} ns\n")
        else:
            txt.append("  No PPS data\n", style="dim")
            txt.append("  (enable PPS on Device tab)\n", style="dim")

        # ── TOFF section ──
        toff = d.toff
        txt.append("\n")
        txt.append("TOFF\n", style="bold")
        txt.append("─" * 30 + "\n", style="bright_black")

        if math.isfinite(toff.real_sec):
            toff_offset = (toff.real_sec - toff.clock_sec) + (toff.real_nsec - toff.clock_nsec) / 1e9
            if math.isfinite(toff_offset):
                toff_us = toff_offset * 1e6
                txt.append("  Offset:  ", style="bright_black")
                if abs(toff_us) < 1:
                    txt.append(f"{toff_us * 1000:+.1f} ns\n")
                elif abs(toff_us) < 1000:
                    txt.append(f"{toff_us:+.3f} us\n")
                else:
                    txt.append(f"{toff_us / 1000:+.3f} ms\n")
        else:
            txt.append("  No TOFF data\n", style="dim")

        # ── DOP timing ──
        if math.isfinite(d.dop.tdop):
            txt.append("\n")
            txt.append("  TDOP:    ", style="bright_black")
            txt.append(f"{d.dop.tdop:.1f}\n")

        return txt
