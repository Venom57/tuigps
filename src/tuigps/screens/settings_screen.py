"""Settings modal screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select


# Constellations available on u-blox 8
CONSTELLATIONS = [
    ("GPS", "gps", True),
    ("GLONASS", "glonass", False),
    ("Galileo", "galileo", False),
    ("BeiDou", "beidou", False),
    ("SBAS", "sbas", False),
    ("QZSS", "qzss", False),
]


class SettingsScreen(ModalScreen[dict | None]):
    """Modal dialog for application settings."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: str = "2947",
        units: str = "metric",
        coord_format: str = "dd",
        enabled_gnss: set[str] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._host = host
        self._port = port
        self._units = units
        self._coord_format = coord_format
        self._enabled_gnss = enabled_gnss or {"gps"}

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Label("Settings", classes="settings-title")

            yield Label("gpsd Host:")
            yield Input(value=self._host, id="input-host", placeholder="127.0.0.1")

            yield Label("gpsd Port:")
            yield Input(value=self._port, id="input-port", placeholder="2947")

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

            yield Label("Coordinate Format:")
            yield Select(
                [
                    ("Decimal Degrees (47.606200\u00b0)", "dd"),
                    ("Deg Min Sec (47\u00b036'22.32\")", "dms"),
                    ("Deg Decimal Min (47\u00b036.372')", "ddm"),
                ],
                id="select-coord",
                value=self._coord_format,
                allow_blank=False,
            )

            yield Label("Constellations:")
            with Horizontal(id="gnss-checkboxes"):
                for display_name, key, _ in CONSTELLATIONS:
                    yield Checkbox(
                        display_name,
                        value=key in self._enabled_gnss,
                        id=f"chk-gnss-{key}",
                    )

            with Horizontal(id="settings-buttons"):
                yield Button("Apply", variant="primary", id="btn-apply")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            # Gather enabled constellations
            enabled = set()
            for _, key, _ in CONSTELLATIONS:
                try:
                    chk = self.query_one(f"#chk-gnss-{key}", Checkbox)
                    if chk.value:
                        enabled.add(key)
                except Exception:
                    pass

            result = {
                "host": self.query_one("#input-host", Input).value,
                "port": self.query_one("#input-port", Input).value,
                "units": self.query_one("#select-units", Select).value,
                "coord_format": self.query_one("#select-coord", Select).value,
                "enabled_gnss": enabled,
            }
            self.dismiss(result)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
