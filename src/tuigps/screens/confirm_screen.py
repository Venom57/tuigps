"""Reusable confirmation dialog for destructive actions."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Ask before doing something destructive; dismisses with True to go ahead.

    Cancel has the focus, so Enter (or Escape) never confirms by accident.
    """

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: 64;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        border: heavy $warning;
        border-title-align: center;
        border-title-style: bold;
        background: $surface;
        padding: 1 2 0 2;
    }
    #confirm-message {
        height: auto;
    }
    #confirm-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    AUTO_FOCUS = "#confirm-no"

    def __init__(
        self,
        title: str,
        message: str,
        confirm_label: str = "Confirm",
        variant: str = "error",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._title = title
        self._message = message
        self._confirm_label = confirm_label
        self._variant = variant

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog") as dialog:
            dialog.border_title = Text(self._title)  # not markup
            yield Static(Text(self._message), id="confirm-message")  # not markup
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="confirm-no", variant="default")
                yield Button(self._confirm_label, id="confirm-yes", variant=self._variant)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")

    def action_cancel(self) -> None:
        self.dismiss(False)
