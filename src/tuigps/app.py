"""tuigps — Terminal UI GPS monitor using gpsd."""

from __future__ import annotations

import webbrowser

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.widgets import Header, TabbedContent, TabPane

from .data_model import GPSData
from .gpsd_client import GPSDClient
from .screens.settings_screen import SettingsScreen
from .widgets.connection_status import ConnectionStatus
from .widgets.constellation_panel import ConstellationPanel
from .widgets.device_config import DeviceConfig
from .widgets.device_panel import DevicePanel
from .widgets.error_panel import ErrorPanel
from .widgets.fix_panel import FixPanel
from .widgets.position_panel import PositionPanel
from .widgets.satellite_table import SatelliteTable
from .widgets.signal_chart import SignalChart
from .widgets.sky_plot import SkyPlot
from .widgets.time_panel import TimePanel
from .widgets.velocity_panel import VelocityPanel


class TuiGPS(App):
    """GPS monitoring terminal application."""

    TITLE = "tuigps"
    SUB_TITLE = "GPS Monitor"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("d", "toggle_dark", "Dark/Light"),
        Binding("s", "open_settings", "Settings"),
        Binding("r", "reconnect", "Reconnect"),
        Binding("u", "cycle_units", "Units"),
        Binding("m", "open_maps", "Maps"),
    ]

    ENABLE_COMMAND_PALETTE = False

    def __init__(self):
        super().__init__()
        self._gpsd = GPSDClient()
        self._gps_data = GPSData()
        self._units = "metric"
        self._coord_format = "dd"
        self._theme_list: list[str] = []
        self._theme_index = 0
        self._enabled_gnss: set[str] = {"gps"}

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent("Dashboard", "Satellites", "Timing", "Device"):
            with TabPane("Dashboard", id="tab-dashboard"):
                with Grid(id="dashboard-grid"):
                    yield PositionPanel(id="w-position")
                    yield FixPanel(id="w-fix")
                    yield VelocityPanel(id="w-velocity")
                    yield SkyPlot(id="w-skyplot")
                    yield SignalChart(id="w-signal")
                    yield ErrorPanel(id="w-errors")
                    yield DevicePanel(id="w-device")
                    yield TimePanel(id="w-time")
            with TabPane("Satellites", id="tab-satellites"):
                with Vertical(id="satellites-container"):
                    yield ConstellationPanel(id="w-constellations")
                    yield SatelliteTable(id="w-sattable")
            with TabPane("Timing", id="tab-timing"):
                with Vertical(id="timing-container"):
                    yield TimePanel(id="w-time-detail", show_pps=True)
                    yield DevicePanel(id="w-device-detail")
            with TabPane("Device", id="tab-device"):
                yield DeviceConfig(id="w-device-config")
        yield ConnectionStatus(id="w-connection")

    def on_mount(self) -> None:
        # Build sorted theme list, default to textual-dark
        self._theme_list = sorted(self.available_themes.keys())
        self.theme = "textual-dark"
        if "textual-dark" in self._theme_list:
            self._theme_index = self._theme_list.index("textual-dark")

        # Start gpsd connection
        self._gpsd.start(
            on_update=self._on_gpsd_update,
            on_error=self._on_gpsd_error,
        )

        # Heartbeat refresh (catches staleness even without gpsd updates)
        self.set_interval(1.0, self._heartbeat)

    def _on_gpsd_update(self, data: GPSData) -> None:
        """Called from the gpsd thread — marshal to Textual event loop."""
        self._gps_data = data
        try:
            self.call_from_thread(self._refresh_ui)
        except Exception:
            pass

    def _on_gpsd_error(self, error: str) -> None:
        """Called from the gpsd thread on connection errors."""
        try:
            self.call_from_thread(self.notify, f"gpsd: {error}", severity="error", timeout=5)
        except Exception:
            pass

    def _refresh_ui(self) -> None:
        """Push current GPS data to all widgets."""
        widget_ids = [
            "w-position", "w-fix", "w-velocity", "w-skyplot", "w-signal",
            "w-errors", "w-device", "w-time", "w-connection",
            "w-constellations", "w-sattable", "w-time-detail", "w-device-detail",
            "w-device-config",
        ]
        for wid in widget_ids:
            try:
                widget = self.query_one(f"#{wid}")
                if hasattr(widget, "units"):
                    widget.units = self._units
                if hasattr(widget, "coord_format"):
                    widget.coord_format = self._coord_format
                if hasattr(widget, "update_gps_data"):
                    widget.update_gps_data(self._gps_data)
            except Exception:
                pass

    def _heartbeat(self) -> None:
        """Periodic refresh for staleness detection and clock updates."""
        self._refresh_ui()

    # ─── Actions ─────────────────────────────────────────────

    def action_cycle_theme(self) -> None:
        """Cycle through available Textual themes."""
        if not self._theme_list:
            return
        self._theme_index = (self._theme_index + 1) % len(self._theme_list)
        self.theme = self._theme_list[self._theme_index]
        self.notify(f"Theme: {self.theme}", timeout=2)

    def action_cycle_units(self) -> None:
        """Cycle through unit systems."""
        cycle = ["metric", "imperial", "nautical"]
        idx = cycle.index(self._units) if self._units in cycle else 0
        self._units = cycle[(idx + 1) % len(cycle)]
        self.notify(f"Units: {self._units}", timeout=2)
        self._refresh_ui()

    def action_open_settings(self) -> None:
        """Open the settings modal."""

        def on_settings_result(result: dict | None) -> None:
            if result is None:
                return
            new_host = result.get("host", "127.0.0.1")
            new_port = result.get("port", "2947")
            self._units = result.get("units", self._units)
            self._coord_format = result.get("coord_format", self._coord_format)
            self._enabled_gnss = result.get("enabled_gnss", self._enabled_gnss)

            # Reconnect if host/port changed
            if new_host != self._gpsd.host or new_port != self._gpsd.port:
                self._gpsd.stop()
                self._gpsd = GPSDClient(host=new_host, port=new_port)
                self._gpsd.start(
                    on_update=self._on_gpsd_update,
                    on_error=self._on_gpsd_error,
                )
                self.notify("Reconnecting to gpsd...", timeout=3)

            self._refresh_ui()

        self.push_screen(
            SettingsScreen(
                host=self._gpsd.host,
                port=self._gpsd.port,
                units=self._units,
                coord_format=self._coord_format,
                enabled_gnss=self._enabled_gnss,
            ),
            callback=on_settings_result,
        )

    def action_open_maps(self) -> None:
        """Open current GPS position in Google Maps."""
        d = self._gps_data
        if not d.has_fix:
            self.notify("No GPS fix — cannot open maps", severity="warning", timeout=3)
            return
        url = f"https://maps.google.com/?q={d.latitude:.6f},{d.longitude:.6f}"
        webbrowser.open(url)
        self.notify("Opening Google Maps...", timeout=2)

    def action_reconnect(self) -> None:
        """Force reconnection to gpsd."""
        self._gpsd.stop()
        self._gps_data = GPSData()
        self._gpsd.start(
            on_update=self._on_gpsd_update,
            on_error=self._on_gpsd_error,
        )
        self.notify("Reconnecting to gpsd...", timeout=3)

    def on_unmount(self) -> None:
        """Clean up gpsd connection on exit."""
        self._gpsd.stop()


def run() -> None:
    """Entry point for the tuigps command."""
    app = TuiGPS()
    app.run()
