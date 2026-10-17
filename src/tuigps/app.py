"""tuigps — Terminal UI GPS monitor using gpsd."""

from __future__ import annotations

import logging
import math
import threading
import time
import webbrowser
from collections import deque
from contextlib import contextmanager

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
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

log = logging.getLogger(__name__)

# Coalesced UI updates: the gpsd thread never waits for the UI. It stores the
# latest snapshot (and queues NMEA sentences) and schedules at most one pending
# UI tick; ticks run at most UI_REFRESH_HZ times per second.
UI_REFRESH_HZ = 10.0
UI_MIN_INTERVAL = 1.0 / UI_REFRESH_HZ
NMEA_QUEUE_MAX = 5000  # sentences buffered between UI ticks (oldest dropped)


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

    def __init__(self, host: str | None = None, port: str | int | None = None):
        super().__init__()
        self._gpsd = GPSDClient(host=host, port=port)
        self._gps_data = GPSData()
        self._units = "metric"
        self._coord_format = "dd"
        self._theme_list: list[str] = []
        self._theme_index = 0
        self._enabled_gnss: set[str] = {"gps"}
        self._gps_logger = GPSLogger()
        self._hold = PositionHold()
        self._armed_clock_set = False
        self._last_tpv_seq = 0  # GPSData.tpv_seq last fed to hold/logger
        self._conn_state = ""  # last GPSData.connection_state seen
        # Cross-thread delivery (see _schedule_tick / _ui_tick)
        self._tick_lock = threading.Lock()
        self._tick_pending = False  # a UI tick is scheduled and has not run yet
        self._data_dirty = False  # _gps_data changed since the last tick
        self._nmea_queue: deque[str] = deque(maxlen=NMEA_QUEUE_MAX)
        self._last_tick = 0.0  # time.monotonic() of the last UI tick
        self._tick_handle = None  # asyncio TimerHandle of a delayed tick

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent("Dashboard", "Satellites", "Timing", "Device", "NMEA"):
            with TabPane("Dashboard", id="tab-dashboard"):
                with VerticalScroll(id="dashboard-scroll"):
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
                with VerticalScroll(id="satellites-container"):
                    yield ConstellationPanel(id="w-constellations")
                    yield SatelliteTable(id="w-sattable")
            with TabPane("Timing", id="tab-timing"):
                with VerticalScroll(id="timing-container"):
                    yield TimePanel(id="w-time-detail", show_pps=True)
                    with Horizontal(id="toff-controls"):
                        yield Button("Arm TOFF", id="btn-arm-toff", variant="warning")
                        yield Button("Clear TOFF", id="btn-clear-toff", variant="default")
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

    # ─── Cross-thread delivery ──────────────────────────────────

    def _call_soon(self, callback, *args) -> bool:
        """Run ``callback(*args)`` on the UI event loop without waiting for it.

        Thread-safe and non-blocking (unlike ``App.call_from_thread``, which
        waits for the callback to finish). Returns False if the app is not
        running (startup/shutdown).
        """
        loop = self._loop
        if loop is None or not self.is_running:
            return False
        try:
            loop.call_soon_threadsafe(self._run_ui_callback, callback, args)
        except RuntimeError:  # event loop closed during shutdown
            log.debug("UI delivery dropped: event loop closed")
            return False
        return True

    def _run_ui_callback(self, callback, args) -> None:
        """Run a scheduled callback on the UI loop, with the app context set."""
        if self._closing or not self.is_running:
            return
        with self._context():
            try:
                callback(*args)
            except Exception:
                log.exception("UI callback failed: %r", callback)

    def _schedule_tick(self) -> None:
        """Ask for a UI tick (thread-safe); at most one is ever pending."""
        with self._tick_lock:
            if self._tick_pending:
                return
            self._tick_pending = True
        if not self._call_soon(self._start_tick):
            with self._tick_lock:
                self._tick_pending = False

    def _start_tick(self) -> None:
        """Run the requested tick now, or once the minimum interval has passed."""
        delay = self._last_tick + UI_MIN_INTERVAL - time.monotonic()
        if delay <= 0:
            self._ui_tick()
        else:
            self._tick_handle = self._loop.call_later(
                delay, self._run_ui_callback, self._ui_tick, ()
            )

    def _ui_tick(self) -> None:
        """Deliver everything the gpsd thread produced since the last tick."""
        self._tick_handle = None
        self._last_tick = time.monotonic()
        # Clear the flags before reading the data: anything that arrives from
        # now on schedules another tick, so the final state is always shown.
        with self._tick_lock:
            self._tick_pending = False
        self._drain_nmea()
        if self._data_dirty:
            self._data_dirty = False
            self._refresh_ui()

    def _drain_nmea(self) -> None:
        """Hand all queued NMEA sentences to the viewer in one batch."""
        queue = self._nmea_queue
        if not queue:
            return
        batch = []
        try:
            while True:
                batch.append(queue.popleft())
        except IndexError:
            pass
        self._deliver_nmea_batch(batch)

    # ─── gpsd callbacks (run on the gpsd thread) ─────────────────

    def _on_gpsd_update(self, data: GPSData) -> None:
        """Called from the gpsd thread: store the snapshot, never block on the UI."""
        self._gps_data = data

        # Announce (re)connection once per transition into "connected"
        state = data.connection_state
        if state != self._conn_state:
            self._conn_state = state
            if state == "connected":
                self._call_soon(
                    self._notify, f"Connected to gpsd {self._gpsd.server}", {"timeout": 3}
                )

        # Every message (SKY, PPS, NMEA...) re-delivers the current fix; only
        # a new TPV epoch is a new fix for clock sync, logging and hold.
        if data.tpv_seq != self._last_tpv_seq:
            self._last_tpv_seq = data.tpv_seq
            self._on_new_tpv(data)

        self._data_dirty = True
        self._schedule_tick()

    def _notify(self, message: str, kwargs: dict) -> None:
        self.notify(message, **kwargs)

    def _on_new_tpv(self, data: GPSData) -> None:
        """Consume one TPV epoch (on the gpsd thread)."""
        # Armed clock set: fire immediately on gpsd thread for minimum latency.
        # The GPS time string is as fresh as possible — no event loop delay.
        if self._armed_clock_set and data.time:
            self._armed_clock_set = False
            try:
                msg = set_clock_from_gps(data.time, data.tpv_received)
            except Exception as e:
                msg = f"Clock sync error: {e}"
            self._call_soon(self._deliver_clock_result, msg)

        # Log and hold in the callback (already on gpsd thread, but data is fresh)
        if self._gps_logger.is_active:
            self._gps_logger.log_point(data)
        if self._hold.is_active and data.has_fix:
            self._hold.add_fix(data.latitude, data.longitude, data.alt_msl)

    def _on_gpsd_error(self, error: str) -> None:
        """Called from the gpsd thread when the connection fails or drops.

        The client reports each outage once (not every reconnect attempt).
        """
        self._call_soon(self._notify, f"gpsd: {error}", {"severity": "error", "timeout": 5})

    def _on_nmea(self, sentence: str) -> None:
        """Called from the gpsd thread with raw NMEA sentences (queued, batched)."""
        self._nmea_queue.append(sentence)
        self._schedule_tick()

    @contextmanager
    def _ui_guard(self, what: str):
        """Keep one failing widget update from crashing the app or the refresh."""
        try:
            yield
        except NoMatches:
            pass  # widget not on the active screen (e.g. a modal is open)
        except Exception:
            log.exception("UI update failed: %s", what)

    def _deliver_clock_result(self, message: str) -> None:
        """Show clock sync result in Device tab output and as notification."""
        with self._ui_guard("clock result"):
            config = self.query_one("#w-device-config", DeviceConfig)
            config._append_output(message)
            # Reset the arm button
            btn = config.query_one("#btn-arm-clock", Button)
            btn.variant = "warning"
            btn.label = "Arm Clock Sync"
        is_error = message.startswith("Error")
        self.notify(
            message.split("\n")[0],
            severity="error" if is_error else "information",
            timeout=4,
        )

    def _deliver_nmea_batch(self, sentences: list[str]) -> None:
        """Deliver queued NMEA sentences to the viewer widget (on Textual thread)."""
        with self._ui_guard("#w-nmea"):
            viewer = self.query_one("#w-nmea", NMEAViewer)
            for sentence in sentences:
                viewer.append_nmea(sentence)

    def _refresh_ui(self) -> None:
        """Push current GPS data to all widgets."""
        # Pass hold data to position panel
        with self._ui_guard("#w-position"):
            pos = self.query_one("#w-position", PositionPanel)
            pos.units = self._units
            pos.coord_format = self._coord_format
            pos.set_hold_data(self._hold.result if self._hold.is_active else None)
            pos.update_gps_data(self._gps_data)

        # Pass logging/hold state to connection status
        with self._ui_guard("#w-connection"):
            conn = self.query_one("#w-connection", ConnectionStatus)
            conn.logging_active = self._gps_logger.is_active
            conn.log_count = self._gps_logger.fix_count
            conn.hold_active = self._hold.is_active
            conn.hold_count = self._hold.fix_count
            conn.update_gps_data(self._gps_data)

        # Check if armed TOFF fired (single-shot)
        if not self._gpsd.toff_armed and math.isfinite(self._gps_data.toff_armed_offset):
            with self._ui_guard("#btn-arm-toff"):
                btn = self.query_one("#btn-arm-toff", Button)
                if btn.variant == "success":
                    offset_ms = self._gps_data.toff_armed_offset * 1000
                    btn.variant = "warning"
                    btn.label = f"Arm TOFF (delta: {offset_ms:+.1f} ms)"

        widget_ids = [
            "w-fix", "w-velocity", "w-skyplot", "w-signal",
            "w-errors", "w-device", "w-time",
            "w-constellations", "w-sattable", "w-time-detail", "w-device-detail",
            "w-device-config",
        ]
        for wid in widget_ids:
            with self._ui_guard(f"#{wid}"):
                widget = self.query_one(f"#{wid}")
                if hasattr(widget, "units"):
                    widget.units = self._units
                if hasattr(widget, "coord_format"):
                    widget.coord_format = self._coord_format
                if hasattr(widget, "update_gps_data"):
                    widget.update_gps_data(self._gps_data)

    def _heartbeat(self) -> None:
        """Periodic refresh for staleness detection and clock updates."""
        self._refresh_ui()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-arm-toff":
            self._arm_toff()
        elif btn_id == "btn-clear-toff":
            self._clear_toff()

    def _arm_toff(self) -> None:
        """Arm single-shot TOFF: fires on next TPV message with GPS time."""
        # Clear the previous armed result (in the client, then refresh the view)
        self._gpsd.arm_toff()
        self._gps_data = self._gpsd.data
        with self._ui_guard("#btn-arm-toff"):
            btn = self.query_one("#btn-arm-toff", Button)
            btn.variant = "success"
            btn.label = "ARMED — waiting..."
        self.notify("TOFF armed — waiting for next GPS message", timeout=2)

    def _clear_toff(self) -> None:
        """Clear TOFF history and armed result."""
        self._gpsd.clear_toff()
        self._gps_data = self._gpsd.data
        with self._ui_guard("#btn-arm-toff"):
            btn = self.query_one("#btn-arm-toff", Button)
            btn.variant = "warning"
            btn.label = "Arm TOFF"
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
                self._gps_data = self._gpsd.data
                self._last_tpv_seq = 0
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
        # Reset the client's data too, so the UI and the client agree
        self._gpsd.reset()
        self._gps_data = self._gpsd.data
        self._last_tpv_seq = 0
        self._gpsd.start(
            on_update=self._on_gpsd_update,
            on_error=self._on_gpsd_error,
            on_nmea=self._on_nmea,
        )
        self.notify("Reconnecting to gpsd...", timeout=3)

    def on_unmount(self) -> None:
        """Clean up on exit."""
        if self._tick_handle is not None:
            self._tick_handle.cancel()
            self._tick_handle = None
        self._gps_logger.stop()
        self._gpsd.stop()


def run(argv: list[str] | None = None) -> None:
    """Run tuigps (kept for compatibility; see tuigps.cli.main)."""
    from .cli import main

    main(argv)
