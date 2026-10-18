"""Settings modal screen."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select

from ..config import ALL_GNSS, check_host, check_log_dir, check_port
from ..constants import GNSS_IDS, GNSS_NAMES

# Display filter checkboxes: (label, key) for every known constellation
CONSTELLATIONS = [(GNSS_NAMES[GNSS_IDS[key]], key) for key in ALL_GNSS]


class SettingsScreen(ModalScreen[dict | None]):
    """Modal dialog for application settings.

    Dismisses with a dict of validated values (host, port, units,
    coord_format, log_format, log_dir, display_gnss), or None on cancel.
    Enter in a text field applies; Escape cancels.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: str = "2947",
        units: str = "metric",
        coord_format: str = "dd",
        log_format: str = "gpx",
        log_dir: str = ".",
        display_gnss: list[str] | set[str] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._host = host
        self._port = str(port)
        self._units = units
        self._coord_format = coord_format
        self._log_format = log_format
        self._log_dir = log_dir
        self._display_gnss = set(ALL_GNSS if display_gnss is None else display_gnss)

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Label("Settings", classes="settings-title")
            with VerticalScroll(id="settings-body"):
                with Horizontal(classes="settings-row"):
                    with Vertical(classes="settings-field"):
                        yield Label("gpsd Host:")
                        yield Input(value=self._host, id="input-host", placeholder="127.0.0.1")
                        yield Label("", id="error-host", classes="field-error")
                    with Vertical(classes="settings-field settings-narrow"):
                        yield Label("gpsd Port:")
                        yield Input(value=self._port, id="input-port", placeholder="2947")
                        yield Label("", id="error-port", classes="field-error")

                with Horizontal(classes="settings-row"):
                    with Vertical(classes="settings-field"):
                        yield Label("Units:")
                        yield Select(
                            [
                                ("Metric (m, km/h)", "metric"),
                                ("Imperial (ft, mph)", "imperial"),
                                ("Nautical (ft, knots)", "nautical"),
                            ],
                            id="select-units",
                            value=self._units,
                            allow_blank=False,
                        )
                    with Vertical(classes="settings-field"):
                        yield Label("Coordinate Format:")
                        yield Select(
                            [
                                ("Decimal Degrees (47.606200°)", "dd"),
                                ("Deg Min Sec (47°36'22.32\")", "dms"),
                                ("Deg Decimal Min (47°36.372')", "ddm"),
                            ],
                            id="select-coord",
                            value=self._coord_format,
                            allow_blank=False,
                        )

                with Horizontal(classes="settings-row"):
                    with Vertical(classes="settings-field"):
                        yield Label("Log Directory:")
                        yield Input(value=self._log_dir, id="input-log-dir", placeholder="~/gps-logs")
                        yield Label("", id="error-log-dir", classes="field-error")
                    with Vertical(classes="settings-field settings-narrow"):
                        yield Label("Log Format:")
                        yield Select(
                            [("GPX", "gpx"), ("CSV", "csv")],
                            id="select-log-format",
                            value=self._log_format,
                            allow_blank=False,
                        )

                yield Label("Show constellations (display filter only; the Device tab "
                            "configures the receiver):", id="gnss-label")
                with Grid(id="gnss-checkboxes"):
                    for display_name, key in CONSTELLATIONS:
                        yield Checkbox(
                            display_name,
                            value=key in self._display_gnss,
                            id=f"chk-gnss-{key}",
                        )
                yield Label("", id="error-gnss", classes="field-error")

            with Horizontal(id="settings-buttons"):
                yield Button("Apply", variant="primary", id="btn-apply")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        self._show_errors(self._validate()[1])

    # ─── Validation ──────────────────────────────────────────────

    def _selected_gnss(self) -> list[str]:
        return [key for _, key in CONSTELLATIONS
                if self.query_one(f"#chk-gnss-{key}", Checkbox).value]

    def _validate(self) -> tuple[dict, dict[str, str]]:
        """Return (values, errors); errors maps a field name to a message."""
        values: dict = {
            "units": self.query_one("#select-units", Select).value,
            "coord_format": self.query_one("#select-coord", Select).value,
            "log_format": self.query_one("#select-log-format", Select).value,
        }
        errors: dict[str, str] = {}
        host = self.query_one("#input-host", Input).value
        port = self.query_one("#input-port", Input).value
        log_dir = self.query_one("#input-log-dir", Input).value
        try:
            values["host"] = check_host(host)
        except ValueError as exc:
            errors["host"] = str(exc)
        try:
            values["port"] = check_port(port)
        except ValueError as exc:
            errors["port"] = str(exc)
        try:
            check_log_dir(log_dir)
            values["log_dir"] = log_dir.strip()  # kept as typed: "~" expands when used
        except ValueError as exc:
            errors["log-dir"] = str(exc)
        gnss = self._selected_gnss()
        if gnss:
            values["display_gnss"] = gnss
        else:
            errors["gnss"] = "select at least one constellation to show"
        return values, errors

    def _show_errors(self, errors: dict[str, str]) -> None:
        for name in ("host", "port", "log-dir", "gnss"):
            label = self.query_one(f"#error-{name}", Label)
            message = errors.get(name, "")
            label.update(message)
            label.display = bool(message)
        self.query_one("#btn-apply", Button).disabled = bool(errors)

    @on(Input.Changed)
    @on(Checkbox.Changed)
    def _revalidate(self) -> None:
        self._show_errors(self._validate()[1])

    # ─── Apply / cancel ──────────────────────────────────────────

    def _apply(self) -> None:
        values, errors = self._validate()
        self._show_errors(errors)
        if errors:
            self.notify("Fix the invalid settings first", severity="warning", timeout=3)
            return
        self.dismiss(values)

    @on(Input.Submitted)
    def _on_submitted(self) -> None:
        self._apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            self._apply()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
