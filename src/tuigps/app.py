"""tuigps — Terminal UI GPS monitor using gpsd."""

from __future__ import annotations

import webbrowser

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.widgets import Button, Header, TabbedContent, TabPane

from .clock_sync import set_clock_from_gps
from .data_model import GPSData
from .gps_logger import GPSLogger
from .gpsd_client import GPSDClient
from .position_hold import PositionHold
from .screens.settings_screen import SettingsScreen
from .widgets.connection_status import ConnectionStatus
from .widgets.constellation_panel import ConstellationPanel
from .widgets.device_config import DeviceConfig
from .widgets.device_panel import DevicePanel
from .widgets.error_panel import ErrorPanel
from .widgets.fix_panel import FixPanel
from .widgets.nmea_viewer import NMEAViewer
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
        Binding("l", "toggle_log", "Log"),
        Binding("h", "toggle_hold", "Hold"),
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
        self._gps_logger = GPSLogger()
        self._hold = PositionHold()
        self._armed_clock_set = False

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent("Dashboard", "Satellites", "Timing", "Device", "NMEA"):
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
            with TabPane("NMEA", id="tab-nmea"):
                yield NMEAViewer(id="w-nmea")
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
            on_nmea=self._on_nmea,
        )

        # Heartbeat refresh (catches staleness even without gpsd updates)
        self.set_interval(1.0, self._heartbeat)

    def _on_gpsd_update(self, data: GPSData) -> None:
        """Called from the gpsd thread — marshal to Textual event loop."""
        self._gps_data = data

        # Armed clock set: fire immediately on gpsd thread for minimum latency.
        # The GPS time string is as fresh as possible — no event loop delay.
        if self._armed_clock_set and data.time:
            self._armed_clock_set = False
            try:
                msg = set_clock_from_gps(data.time, data.last_seen)
                self.call_from_thread(self._deliver_clock_result, msg)
            except Exception as e:
                self.call_from_thread(
                    self._deliver_clock_result, f"Clock sync error: {e}"
                )

        # Log and hold in the callback (already on gpsd thread, but data is fresh)
        if self._gps_logger.is_active:
            self._gps_logger.log_point(data)
        if self._hold.is_active and data.has_fix:
            self._hold.add_fix(data.latitude, data.longitude, data.alt_msl)

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

    def _on_nmea(self, sentence: str) -> None:
        """Called from the gpsd thread with raw NMEA sentences."""
        try:
            self.call_from_thread(self._deliver_nmea, sentence)
        except Exception:
            pass

    def _deliver_clock_result(self, message: str) -> None:
        """Show clock sync result in Device tab output and as notification."""
        try:
            config = self.query_one("#w-device-config", DeviceConfig)
            config._append_output(message)
            # Reset the arm button
            btn = config.query_one("#btn-arm-clock", Button)
            btn.variant = "warning"
            btn.label = "Arm Clock Sync"
        except Exception:
            pass
        is_error = message.startswith("Error")
        self.notify(
            message.split("\n")[0],
            severity="error" if is_error else "information",
            timeout=4,
        )

    def _deliver_nmea(self, sentence: str) -> None:
        """Deliver NMEA sentence to the viewer widget (on Textual thread)."""
        try:
            viewer = self.query_one("#w-nmea", NMEAViewer)
            viewer.append_nmea(sentence)
        except Exception:
            pass

    def _refresh_ui(self) -> None:
        """Push current GPS data to all widgets."""
        # Pass hold data to position panel
        try:
            pos = self.query_one("#w-position", PositionPanel)
            pos.units = self._units
            pos.coord_format = self._coord_format
            pos.set_hold_data(self._hold.result if self._hold.is_active else None)
            pos.update_gps_data(self._gps_data)
        except Exception:
            pass

        # Pass logging/hold state to connection status
        try:
            conn = self.query_one("#w-connection", ConnectionStatus)
            conn.logging_active = self._gps_logger.is_active
            conn.log_count = self._gps_logger.fix_count
            conn.hold_active = self._hold.is_active
            conn.hold_count = self._hold.fix_count
            conn.update_gps_data(self._gps_data)
        except Exception:
            pass

        widget_ids = [
            "w-fix", "w-velocity", "w-skyplot", "w-signal",
            "w-errors", "w-device", "w-time",
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
                    on_nmea=self._on_nmea,
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

    def action_toggle_log(self) -> None:
        """Toggle GPS logging to file."""
        if self._gps_logger.is_active:
            path = self._gps_logger.filepath
            self._gps_logger.stop()
            self.notify(f"Logging stopped: {path} ({self._gps_logger.fix_count} pts)", timeout=4)
        else:
            path = self._gps_logger.start()
            self.notify(f"Logging to {path}", timeout=3)

    def action_toggle_hold(self) -> None:
        """Toggle position hold/averaging."""
        if self._hold.is_active:
            result = self._hold.stop()
            self.notify(
                f"Hold stopped: {result.fix_count} fixes, CEP50={result.cep50:.2f}m",
                timeout=4,
            )
        else:
            self._hold.start()
            self.notify("Position hold started — accumulating fixes", timeout=3)

    def action_reconnect(self) -> None:
        """Force reconnection to gpsd."""
        self._gpsd.stop()
        self._gps_data = GPSData()
        self._gpsd.start(
            on_update=self._on_gpsd_update,
            on_error=self._on_gpsd_error,
            on_nmea=self._on_nmea,
        )
        self.notify("Reconnecting to gpsd...", timeout=3)

    def on_unmount(self) -> None:
        """Clean up on exit."""
        self._gps_logger.stop()
        self._gpsd.stop()


def run() -> None:
    """Entry point for the tuigps command."""
    app = TuiGPS()
    app.run()
