"""Help screen: key bindings and a short description of each tab."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


class HelpScreen(ModalScreen[None]):
    """Modal help. Escape, ``?``, F1 or ``q`` closes it.

    ``keys`` are ``(key, description, details)`` rows, generated from the
    App's BINDINGS so the help never drifts from the real bindings; ``tabs``
    are ``(key, tab name, description)`` rows.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close"),
        Binding("f1", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(
        self,
        keys: list[tuple[str, str, str]],
        tabs: list[tuple[str, str, str]],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._keys = keys
        self._tabs = tabs

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog") as dialog:
            dialog.border_title = "tuigps help"
            dialog.border_subtitle = "Esc, ? or q: close"
            with VerticalScroll(id="help-body"):
                yield Static(self.keys_table(), id="help-keys")
                yield Static(self.tabs_table(), id="help-tabs")

    def keys_table(self) -> Table:
        table = Table(title="Keys", title_style="bold", title_justify="left",
                      show_header=False, box=None, padding=(0, 1), expand=True)
        table.add_column("key", style="bold reverse", no_wrap=True, justify="center")
        table.add_column("action", style="bold", no_wrap=True)
        table.add_column("details", ratio=1)
        for key, description, details in self._keys:
            table.add_row(Text(f" {key} "), Text(description), Text(details))
        return table

    def tabs_table(self) -> Table:
        table = Table(title="Tabs", title_style="bold", title_justify="left",
                      show_header=False, box=None, padding=(0, 1), expand=True)
        table.add_column("key", style="bold reverse", no_wrap=True, justify="center")
        table.add_column("tab", style="bold", no_wrap=True)
        table.add_column("description", ratio=1)
        for key, name, description in self._tabs:
            table.add_row(Text(f" {key} "), Text(name), Text(description))
        return table

    def action_close(self) -> None:
        self.dismiss(None)
