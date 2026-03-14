"""NMEA sentence viewer — raw NMEA stream display."""

from __future__ import annotations

from collections import deque

from rich.text import Text
from textual.app import ComposeResult
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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._paused = False
        self._buffer: deque[str] = deque(maxlen=1000)
        self._filter: str = ""  # empty = show all

    def compose(self) -> ComposeResult:
        with Horizontal(id="nmea-controls"):
            yield Button("Pause", id="btn-nmea-pause", variant="default")
            yield Button("Clear", id="btn-nmea-clear", variant="default")
            yield Button("All", id="btn-nmea-all", variant="primary")
            for stype in ["GGA", "RMC", "GSA", "GSV", "VTG", "GLL"]:
                yield Button(stype, id=f"btn-nmea-{stype.lower()}", variant="default")
            yield Static("", id="nmea-stats")
        yield RichLog(id="nmea-log", wrap=False, markup=False, max_lines=2000)

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
                # Flush buffered sentences
                self._flush_buffer()
        elif btn_id == "btn-nmea-clear":
            self._buffer.clear()
            try:
                log = self.query_one("#nmea-log", RichLog)
                log.clear()
            except Exception:
                pass
        elif btn_id == "btn-nmea-all":
            self._set_filter("")
        elif btn_id.startswith("btn-nmea-"):
            stype = btn_id.replace("btn-nmea-", "").upper()
            self._set_filter(stype)

    def _set_filter(self, stype: str) -> None:
        """Set the sentence type filter."""
        self._filter = stype
        # Update button styles
        for child_id in ["all", "gga", "rmc", "gsa", "gsv", "vtg", "gll"]:
            try:
                btn = self.query_one(f"#btn-nmea-{child_id}", Button)
                expected = child_id.upper() if child_id != "all" else ""
                btn.variant = "primary" if self._filter == expected else "default"
            except Exception:
                pass

    def append_nmea(self, sentence: str) -> None:
        """Add an NMEA sentence to the viewer."""
        self._buffer.append(sentence)
        if not self._paused:
            self._write_sentence(sentence)

    def _flush_buffer(self) -> None:
        """Write all buffered sentences to the log."""
        try:
            log = self.query_one("#nmea-log", RichLog)
            for sentence in self._buffer:
                if self._matches_filter(sentence):
                    txt = self._colorize(sentence)
                    log.write(txt)
        except Exception:
            pass

    def _write_sentence(self, sentence: str) -> None:
        """Write a single sentence to the log if it matches the filter."""
        if not self._matches_filter(sentence):
            return
        try:
            log = self.query_one("#nmea-log", RichLog)
            txt = self._colorize(sentence)
            log.write(txt)
        except Exception:
            pass

    def _matches_filter(self, sentence: str) -> bool:
        """Check if a sentence matches the current filter."""
        if not self._filter:
            return True
        return _sentence_type(sentence) == self._filter

    def _colorize(self, sentence: str) -> Text:
        """Apply color based on sentence type."""
        stype = _sentence_type(sentence)
        color = SENTENCE_COLORS.get(stype, "white")
        return Text(sentence, style=color)
