"""NMEA sentence viewer — raw NMEA stream display."""

from __future__ import annotations

import time
from collections import Counter, deque

from rich.text import Text
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, RichLog, Static


# Color map for NMEA sentence types
SENTENCE_COLORS = {
    "GGA": "green",
    "RMC": "dodger_blue2",
    "GSA": "yellow",
    "GSV": "orange1",
    "VTG": "cyan",
    "GLL": "magenta",
    "ZDA": "bright_cyan",
    "TXT": "bright_black",
}


def _sentence_type(sentence: str) -> str:
    """Extract the sentence type (e.g., 'GGA' from '$GPGGA,...')."""
    if len(sentence) < 6 or sentence[0] != "$":
        return ""
    # Skip talker ID (2 chars after $) to get sentence type
    return sentence[3:6]


class NMEAViewer(Vertical):
    """Displays raw NMEA sentences from the GPS receiver."""

    DEFAULT_CSS = """
    NMEAViewer {
        height: 1fr;
        padding: 0 1;
    }
    """

    RATE_WINDOW = 5.0  # seconds of history used for the sentences/s rate

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._paused = False
        # Recent sentences (shown and pending), used to re-filter the log
        self._buffer: deque[str] = deque(maxlen=1000)
        self._pending = 0  # sentences at the end of _buffer received while paused
        self._filter: str = ""  # empty = show all
        self._total = 0
        self._type_counts: Counter[str] = Counter()
        self._arrivals: deque[float] = deque()  # monotonic receipt times

    def compose(self) -> ComposeResult:
        with Horizontal(id="nmea-controls"):
            yield Button("Pause", id="btn-nmea-pause", variant="default")
            yield Button("Clear", id="btn-nmea-clear", variant="default")
            yield Button("All", id="btn-nmea-all", variant="primary")
            for stype in ["GGA", "RMC", "GSA", "GSV", "VTG", "GLL"]:
                yield Button(stype, id=f"btn-nmea-{stype.lower()}", variant="default")
            yield Static("", id="nmea-stats")
        yield RichLog(id="nmea-log", wrap=False, markup=False, max_lines=2000)

    def on_mount(self) -> None:
        self._update_stats()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if not btn_id:
            return

        if btn_id == "btn-nmea-pause":
            self._paused = not self._paused
            btn = self.query_one("#btn-nmea-pause", Button)
            if self._paused:
                btn.label = "Resume"
                btn.variant = "warning"
            else:
                btn.label = "Pause"
                btn.variant = "default"
                # Write only the sentences received while paused
                self._flush_pending()
            self._update_stats()
        elif btn_id == "btn-nmea-clear":
            self._buffer.clear()
            self._pending = 0
            self._total = 0
            self._type_counts.clear()
            self._arrivals.clear()
            try:
                self.query_one("#nmea-log", RichLog).clear()
            except NoMatches:
                pass
            self._update_stats()
        elif btn_id == "btn-nmea-all":
            self._set_filter("")
        elif btn_id.startswith("btn-nmea-"):
            stype = btn_id.replace("btn-nmea-", "").upper()
            self._set_filter(stype)

    def _set_filter(self, stype: str) -> None:
        """Set the sentence type filter and re-filter the log from the buffer."""
        self._filter = stype
        # Update button styles
        for child_id in ["all", "gga", "rmc", "gsa", "gsv", "vtg", "gll"]:
            try:
                btn = self.query_one(f"#btn-nmea-{child_id}", Button)
            except NoMatches:
                continue
            expected = child_id.upper() if child_id != "all" else ""
            btn.variant = "primary" if self._filter == expected else "default"
        self._rerender()
        self._update_stats()

    def append_nmea(self, sentence: str) -> None:
        """Add an NMEA sentence to the viewer."""
        self.append_nmea_batch((sentence,))

    def append_nmea_batch(self, sentences) -> None:
        """Add several NMEA sentences at once (one log write and stats update)."""
        if not sentences:
            return
        now = time.monotonic()
        buffer = self._buffer
        counts = self._type_counts
        for sentence in sentences:
            buffer.append(sentence)
            counts[_sentence_type(sentence)] += 1
        n = len(sentences)
        self._total += n
        self._arrivals.extend([now] * n)
        if self._paused:
            self._pending = min(self._pending + n, len(buffer))
        else:
            self._write_sentences(sentences)
        self._update_stats()

    def _shown(self) -> list[str]:
        """Buffered sentences that are already (or should be) in the log."""
        items = list(self._buffer)
        return items[: len(items) - self._pending]

    def _rerender(self) -> None:
        """Clear the log and write the matching sentences from the buffer."""
        try:
            log = self.query_one("#nmea-log", RichLog)
        except NoMatches:
            return
        log.clear()
        self._write_sentences(self._shown())

    def _flush_pending(self) -> None:
        """Write the sentences received while paused to the log."""
        if self._pending:
            pending = list(self._buffer)[-self._pending:]
            self._pending = 0
            self._write_sentences(pending)

    def _write_sentences(self, sentences) -> None:
        """Write the sentences that match the filter to the log, in one write."""
        if self._filter:
            sentences = [s for s in sentences if self._matches_filter(s)]
        if not sentences:
            return
        try:
            log = self.query_one("#nmea-log", RichLog)
        except NoMatches:
            return
        if len(sentences) == 1:
            log.write(self._colorize(sentences[0]))
            return
        text = Text()
        last = len(sentences) - 1
        for i, sentence in enumerate(sentences):
            text.append(sentence, style=self._color(sentence))
            if i < last:
                text.append("\n")
        log.write(text)

    def _rate(self) -> float:
        """Sentences per second over the last RATE_WINDOW seconds."""
        cutoff = time.monotonic() - self.RATE_WINDOW
        while self._arrivals and self._arrivals[0] < cutoff:
            self._arrivals.popleft()
        return len(self._arrivals) / self.RATE_WINDOW

    def stats_text(self) -> str:
        parts = [f"{self._total} sentences", f"{self._rate():.1f}/s"]
        if self._filter:
            parts.append(f"{self._filter}: {self._type_counts.get(self._filter, 0)}")
        if self._paused:
            parts.append(f"PAUSED ({self._pending} buffered)")
        return "  ".join(parts)

    def _update_stats(self) -> None:
        try:
            stats = self.query_one("#nmea-stats", Static)
        except NoMatches:
            return
        stats.update(self.stats_text())

    def _matches_filter(self, sentence: str) -> bool:
        """Check if a sentence matches the current filter."""
        if not self._filter:
            return True
        return _sentence_type(sentence) == self._filter

    @staticmethod
    def _color(sentence: str) -> str:
        return SENTENCE_COLORS.get(_sentence_type(sentence), "white")

    def _colorize(self, sentence: str) -> Text:
        """Apply color based on sentence type."""
        return Text(sentence, style=self._color(sentence))
