"""Combined status bar — key hints on the left, GPS status on the right.

Space is given out by priority: the GPS status (right) is always shown in
full, then the REC/HOLD badges, then as many key hints as fit. Hints are
dropped from the end and replaced by the help hint, so the user can always
find the rest.
"""

from __future__ import annotations

import math
import time

from rich.style import Style
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..constants import GNSS_SHORT, MODE_NAMES, STALE_AFTER, STATUS_COLORS, STATUS_NAMES
from ..data_model import GPSData

KEY_STYLE = Style(color="black", bgcolor="white", bold=True)
LABEL_STYLE = Style(color="white")


def hint_text(key: str, label: str) -> Text:
    """One key hint: the key in reverse video, then its label."""
    txt = Text()
    txt.append(f" {key} ", style=KEY_STYLE)
    txt.append(f" {label} ", style=LABEL_STYLE)
    return txt


class ConnectionStatus(Static):
    """Bottom bar with key hints and GPS connection status.

    ``hints`` are ``(key, label)`` pairs, normally generated from the App's
    BINDINGS (see ``TuiGPS.key_hints``); ``help_hint`` is shown in place of
    the hints that do not fit.
    """

    DEFAULT_CSS = """
    ConnectionStatus {
        height: 1;
        dock: bottom;
        width: 100%;
        padding: 0 0;
    }
    """

    # GPS status variants, from the most to the least detailed
    STATUS_DETAIL = (
        {"device": True, "sats": True},
        {"device": False, "sats": True},
        {"device": False, "sats": False},
    )

    # Activity badges: changing one repaints the bar
    logging_active = reactive(False)
    log_count = reactive(0)
    hold_active = reactive(False)
    hold_count = reactive(0)

    def __init__(
        self,
        hints: list[tuple[str, str]] | None = None,
        help_hint: tuple[str, str] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._data: GPSData | None = None
        self._hints = [hint_text(k, label) for k, label in (hints or [])]
        self._help_hint = hint_text(*help_hint) if help_hint else None

    def update_gps_data(self, data: GPSData) -> None:
        if data is self._data:
            return  # same immutable snapshot: nothing new to draw
        self._data = data
        self.refresh()

    def render(self) -> Text:
        w = self.size.width or 80

        # 1. GPS status: the most detailed variant that fits, with the badges
        #    if possible (the device path and satellite counts go first).
        badges = self._render_badges(counts=True)
        for detail in self.STATUS_DETAIL:
            right = self._render_status(**detail)
            if right.cell_len + badges.cell_len + 1 <= w:
                break

        # 2. Badges: before the hints; dropped only when the status needs the room
        if right.cell_len + badges.cell_len + 1 > w:
            badges = self._render_badges(counts=False)
            if right.cell_len + badges.cell_len + 1 > w:
                badges = Text()

        # 3. Key hints in the space that is left
        left = self._fit_hints(w - right.cell_len - badges.cell_len - 1)

        gap = max(1, w - left.cell_len - badges.cell_len - right.cell_len)
        txt = Text()
        txt.append_text(left)
        txt.append_text(badges)
        txt.append(" " * gap)
        txt.append_text(right)
        txt.truncate(w)  # only bites when the status alone is wider than the bar
        return txt

    def _fit_hints(self, budget: int) -> Text:
        """All hints if they fit, else a prefix of them followed by the help hint."""
        txt = Text()
        if sum(h.cell_len for h in self._hints) <= budget:
            for hint in self._hints:
                txt.append_text(hint)
            return txt
        help_hint = self._help_hint
        if help_hint is None or help_hint.cell_len > budget:
            help_hint = Text()
        room = budget - help_hint.cell_len
        for hint in self._hints:
            if hint.plain == help_hint.plain:
                continue
            if hint.cell_len > room:
                break
            txt.append_text(hint)
            room -= hint.cell_len
        txt.append_text(help_hint)
        return txt

    def _render_badges(self, counts: bool) -> Text:
        badges = Text()
        if self.logging_active:
            badges.append(" REC ", style=Style(color="white", bgcolor="red", bold=True))
            if counts:
                badges.append(f" {self.log_count} ", style="red")
        if self.hold_active:
            badges.append(" HOLD ", style=Style(color="black", bgcolor="cyan", bold=True))
            if counts:
                badges.append(f" {self.hold_count} ", style="cyan")
        return badges

    def _render_status(self, device: bool = True, sats: bool = True) -> Text:
        txt = Text()

        d = self._data
        if d is not None and d.connection_state == "connecting":
            txt.append(" CONNECTING\u2026 ", style=Style(color="black", bgcolor="yellow", bold=True))
            return txt
        if d is None or not d.connected:
            label = " DISCONNECTED "
            if d is not None and d.next_retry_at > 0:
                retry_in = max(0, math.ceil(d.next_retry_at - time.time()))
                label = f" DISCONNECTED \u2014 retry in {retry_in}s "
            txt.append(label, style=Style(color="white", bgcolor="red", bold=True))
            return txt

        if d.last_seen <= 0:
            # Connected, but no report received yet
            txt.append(" WAITING ", style=Style(color="black", bgcolor="yellow", bold=True))
            return txt

        age = time.time() - d.last_seen
        if age > STALE_AFTER:
            txt.append(f" STALE ({int(age)}s) ", style=Style(color="black", bgcolor="yellow", bold=True))
            return txt

        # Constellation breakdown (e.g., "4GP+2GA")
        counts = d.constellation_counts if sats else {}
        if counts:
            parts = []
            for gnssid, (visible, used) in sorted(counts.items()):
                if used > 0:
                    short = GNSS_SHORT.get(gnssid, "??")
                    parts.append(f"{used}{short}")
            if parts:
                txt.append("+".join(parts), style="green bold")
                txt.append(f"/{len(d.satellites)}sv ", style="white")
        elif sats and d.satellites_used > 0:
            txt.append(f"{d.satellites_used}", style="green bold")
            txt.append(f"/{len(d.satellites)}sv ", style="white")

        if device and d.device.path:
            txt.append(f"{d.device.path} ", style="dim")

        mode_name = MODE_NAMES.get(d.mode, "?")
        status_name = STATUS_NAMES.get(d.status, "?")
        status_color = STATUS_COLORS.get(d.status, "green")

        txt.append(f" {mode_name} ", style=Style(color="black", bgcolor=status_color, bold=True))
        txt.append(f" {status_name} ", style=status_color)

        return txt
