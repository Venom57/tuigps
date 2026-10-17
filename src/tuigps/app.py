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

from . import config as config_mod
from .clock_sync import set_clock_from_gps
from .config import Config
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

# Widgets refreshed per tab: only the active tab (plus the status bar and the
# non-rendering DeviceConfig) is updated on each refresh.
TAB_WIDGETS: dict[str, tuple[str, ...]] = {
    "tab-dashboard": (
        "w-position", "w-fix", "w-velocity", "w-skyplot",
        "w-signal", "w-errors", "w-device", "w-time",
    ),
    "tab-satellites": ("w-constellations", "w-sattable"),
    "tab-timing": ("w-time-detail", "w-device-detail"),
    "tab-device": (),  # DeviceConfig is always updated (see _refresh_ui)
    "tab-nmea": (),  # NMEA is delivered in batches (see _drain_nmea)
}


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

    def __init__(
        self,
        host: str | None = None,
        port: str | int | None = None,
        config: Config | None = None,
        config_path=None,
    ):
        """``host``/``port`` override the config file for this session only.

        ``config`` defaults to loading ``config_path`` (default: the XDG path);
        changes made in the app are saved back to ``config_path``.
        """
        super().__init__()
        self._config_path = config_path
        self._config = config_mod.load(config_path) if config is None else config
        cfg = self._config
        self._gpsd = GPSDClient(
            host=cfg.host if host is None else host,
            port=cfg.port if port is None else port,
        )
        self._gps_data = GPSData()
        self._units = cfg.units
        self._coord_format = cfg.coord_format
        self._hidden_gnss = cfg.hidden_gnss_ids  # constellation display filter
        self._theme_list: list[str] = []
        self._theme_index = 0
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
        # Widget references, cached after mount (see _cache_widgets)
        self._tab_widgets: dict[str, list] = {}
        self._tabs: TabbedContent | None = None
        self._status_bar: ConnectionStatus | None = None
        self._device_config: DeviceConfig | None = None
        self._nmea_viewer: NMEAViewer | None = None
        self._arm_toff_button: Button | None = None

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

    def _cache_widgets(self) -> None:
        """Look the refreshed widgets up once, instead of on every refresh."""
        self._tab_widgets = {
            tab: [self.query_one(f"#{wid}") for wid in ids] for tab, ids in TAB_WIDGETS.items()
        }
        self._tabs = self.query_one(TabbedContent)
        self._status_bar = self.query_one("#w-connection", ConnectionStatus)
        self._device_config = self.query_one("#w-device-config", DeviceConfig)
        self._nmea_viewer = self.query_one("#w-nmea", NMEAViewer)
        self._arm_toff_button = self.query_one("#btn-arm-toff", Button)

    def on_mount(self) -> None:
        self._cache_widgets()

        # Sorted theme list; start with the configured theme
        self._theme_list = sorted(self.available_themes.keys())
        theme = self._config.theme
        if theme not in self._theme_list:
            self._config.load_warnings.append(f"unknown theme {theme!r}; using the default")
            theme = config_mod.DEFAULT_THEME
        self._set_theme(theme)

        for warning in self._config.load_warnings:
            self.notify(f"Config: {warning}", severity="warning", timeout=6)

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
            config = self._device_config or self.query_one("#w-device-config", DeviceConfig)
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
        if self._nmea_viewer is None:
            return
        with self._ui_guard("#w-nmea"):
            self._nmea_viewer.append_nmea_batch(sentences)

    def _refresh_ui(self, heartbeat: bool = False) -> None:
        """Push the current GPS data to the status bar and the active tab.

        Widgets skip repainting for a snapshot they already show; ``heartbeat``
        also repaints the time-dependent status bar (staleness, retry countdown).
        """
        if self._tabs is None:
            return  # not mounted yet
        data = self._gps_data

        # Logging/hold state and GPS status in the status bar
        with self._ui_guard("#w-connection"):
            conn = self._status_bar
            conn.logging_active = self._gps_logger.is_active
            conn.log_count = self._gps_logger.fix_count
            conn.hold_active = self._hold.is_active
            conn.hold_count = self._hold.fix_count
            conn.update_gps_data(data)
            if heartbeat:
                conn.refresh()

        # Check if armed TOFF fired (single-shot)
        if not self._gpsd.toff_armed and math.isfinite(data.toff_armed_offset):
            with self._ui_guard("#btn-arm-toff"):
                btn = self._arm_toff_button
                if btn.variant == "success":
                    offset_ms = data.toff_armed_offset * 1000
                    btn.variant = "warning"
                    btn.label = f"Arm TOFF (delta: {offset_ms:+.1f} ms)"

        # DeviceConfig needs the latest data even when hidden (GPS time for
        # clock set, device path); its update_gps_data does not render.
        with self._ui_guard("#w-device-config"):
            self._device_config.update_gps_data(data)

        active = self._tabs.active
        self._refresh_tab(active)
        if heartbeat and active == "tab-nmea":
            with self._ui_guard("#w-nmea"):
                self._nmea_viewer._update_stats()  # the sentence rate decays over time

    def _refresh_tab(self, tab: str) -> None:
        """Update the widgets on one tab with the current data and settings."""
        data = self._gps_data
        for widget in self._tab_widgets.get(tab, ()):
            with self._ui_guard(f"#{widget.id}"):
                if isinstance(widget, PositionPanel):
                    widget.set_hold_data(self._hold.result if self._hold.is_active else None)
                if hasattr(widget, "units"):
                    widget.units = self._units
                if hasattr(widget, "coord_format"):
                    widget.coord_format = self._coord_format
                if hasattr(widget, "hidden_gnss"):
                    widget.hidden_gnss = self._hidden_gnss
                widget.update_gps_data(data)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Show the latest data as soon as a tab becomes visible."""
        if event.tabbed_content is not self._tabs or event.pane is None:
            return
        self._refresh_tab(event.pane.id)

    def _heartbeat(self) -> None:
        """Periodic refresh for staleness detection and clock updates."""
        self._refresh_ui(heartbeat=True)

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
            btn = self._arm_toff_button
            btn.variant = "success"
            btn.label = "ARMED — waiting..."
        self.notify("TOFF armed — waiting for next GPS message", timeout=2)

    def _clear_toff(self) -> None:
        """Clear TOFF history and armed result."""
        self._gpsd.clear_toff()
        self._gps_data = self._gpsd.data
        with self._ui_guard("#btn-arm-toff"):
            btn = self._arm_toff_button
            btn.variant = "warning"
            btn.label = "Arm TOFF"
        self._refresh_ui()

    # ─── Actions ─────────────────────────────────────────────

    # ─── Configuration ───────────────────────────────────────

    def _set_theme(self, theme: str) -> None:
        self.theme = theme
        if theme in self._theme_list:
            self._theme_index = self._theme_list.index(theme)

    def _save_config(self) -> None:
        """Write the current settings to the config file (errors are notified)."""
        cfg = self._config
        cfg.units = self._units
        cfg.coord_format = self._coord_format
        cfg.theme = self.theme
        try:
            config_mod.save(cfg, self._config_path)
        except OSError as exc:
            log.warning("cannot save config: %s", exc)
            self.notify(f"Cannot save settings: {exc}", severity="error", timeout=5)

    def action_cycle_theme(self) -> None:
        """Cycle through available Textual themes."""
        if not self._theme_list:
            return
        self._set_theme(self._theme_list[(self._theme_index + 1) % len(self._theme_list)])
        self._save_config()
        self.notify(f"Theme: {self.theme}", timeout=2)

    def action_toggle_dark(self) -> None:
        """Toggle between textual-dark and textual-light (and remember it)."""
        self._set_theme("textual-dark" if self.theme == "textual-light" else "textual-light")
        self._save_config()

    def action_cycle_units(self) -> None:
        """Cycle through unit systems."""
        cycle = list(config_mod.UNITS)
        idx = cycle.index(self._units) if self._units in cycle else 0
        self._units = cycle[(idx + 1) % len(cycle)]
        self._save_config()
        self.notify(f"Units: {self._units}", timeout=2)
        self._refresh_ui()

    def action_open_settings(self) -> None:
        """Open the settings modal."""
        session_host, session_port = self._gpsd.host, self._gpsd.port

        def on_settings_result(result: dict | None) -> None:
            if result is None:
                return
            cfg = self._config
            new_host = result.get("host", session_host)
            new_port = result.get("port", session_port)
            self._units = result.get("units", self._units)
            self._coord_format = result.get("coord_format", self._coord_format)
            cfg.display_gnss = list(result.get("display_gnss", cfg.display_gnss))
            cfg.log_format = result.get("log_format", cfg.log_format)
            cfg.log_dir = result.get("log_dir", cfg.log_dir)
            self._hidden_gnss = cfg.hidden_gnss_ids
            # A server given on the command line (or by GPSD_HOST/GPSD_PORT) is
            # only saved when the user actually changes it here.
            if new_host != session_host:
                cfg.host = new_host
            if new_port != session_port:
                cfg.port = int(new_port)
            self._save_config()

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
                host=session_host,
                port=session_port,
                units=self._units,
                coord_format=self._coord_format,
                log_format=self._config.log_format,
                log_dir=self._config.log_dir,
                display_gnss=self._config.display_gnss,
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
        self._refresh_ui()

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
        self._refresh_ui()

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
