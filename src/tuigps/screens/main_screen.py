"""Main dashboard screen with tabbed content."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, TabbedContent, TabPane

from ..data_model import GPSData
from ..widgets.connection_status import ConnectionStatus
from ..widgets.constellation_panel import ConstellationPanel
from ..widgets.device_panel import DevicePanel
from ..widgets.error_panel import ErrorPanel
from ..widgets.fix_panel import FixPanel
from ..widgets.position_panel import PositionPanel
from ..widgets.satellite_table import SatelliteTable
from ..widgets.signal_chart import SignalChart
from ..widgets.sky_plot import SkyPlot
from ..widgets.time_panel import TimePanel
from ..widgets.velocity_panel import VelocityPanel


class MainScreen(Screen):
    """Primary application screen with Dashboard, Satellites, and Timing tabs."""

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent("Dashboard", "Satellites", "Timing"):
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
                    yield ConnectionStatus(id="w-connection")
            with TabPane("Satellites", id="tab-satellites"):
                with Vertical(id="satellites-container"):
                    yield ConstellationPanel(id="w-constellations")
                    yield SatelliteTable(id="w-sattable")
            with TabPane("Timing", id="tab-timing"):
                with Vertical(id="timing-container"):
                    yield TimePanel(id="w-time-detail", show_pps=True)
                    yield DevicePanel(id="w-device-detail")
        yield Footer()

    def update_data(self, data: GPSData, units: str = "metric", coord_format: str = "dd") -> None:
        """Push GPS data to all widgets."""
        widget_ids = [
            "w-position", "w-fix", "w-velocity", "w-skyplot", "w-signal",
            "w-errors", "w-device", "w-time", "w-connection",
            "w-constellations", "w-sattable", "w-time-detail", "w-device-detail",
        ]
        for wid in widget_ids:
            try:
                widget = self.query_one(f"#{wid}")
                # Update units/format on panels that support it
                if hasattr(widget, "units"):
                    widget.units = units
                if hasattr(widget, "coord_format"):
                    widget.coord_format = coord_format
                if hasattr(widget, "update_gps_data"):
                    widget.update_gps_data(data)
            except Exception:
                pass
