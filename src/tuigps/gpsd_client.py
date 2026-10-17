"""Threaded gpsd client that reads GPS data and populates GPSData."""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from collections import deque

from .data_model import (
    DeviceInfo,
    DOPValues,
    ErrorEstimates,
    GPSData,
    PPSData,
    SatelliteInfo,
    TOFFData,
    VersionInfo,
)
from .timeutil import parse_gps_time

# The python3-gps package is a system apt package, not on PyPI.
# When running in a venv it may not be on sys.path.
# We append (not insert) to avoid overriding venv packages like typing_extensions,
# and remove the path after import to prevent pollution.
try:
    import gps as _gps_module
except ImportError:
    _sys_path = "/usr/lib/python3/dist-packages"
    sys.path.append(_sys_path)
    try:
        import gps as _gps_module
    finally:
        try:
            sys.path.remove(_sys_path)
        except ValueError:
            pass

log = logging.getLogger(__name__)


class GPSDClient:
    """Connects to gpsd in a background thread and keeps GPSData updated."""

    RECONNECT_DELAY = 2.0  # seconds between connection attempts
    POLL_TIMEOUT = 0.5  # max wait per waiting() call; bounds stop() latency
    STOP_TIMEOUT = 1.0  # max time stop() waits for the thread to exit
    GPSD_TOFF_HOLD = 5.0  # prefer a gpsd TOFF message this recent over computed TOFF

    def __init__(self, host: str = "127.0.0.1", port: str = "2947"):
        self.host = host
        self.port = port
        self._session: _gps_module.gps | None = None
        self._thread: threading.Thread | None = None
        # Each run gets its own stop event, so a stopped thread can never be
        # revived by a later start() (see _running).
        self._stop_event = threading.Event()
        self._stop_event.set()
        self._data = GPSData()
        self._lock = threading.Lock()
        self._on_update: callable = None
        self._on_error: callable = None
        self._on_nmea: callable = None
        self._toff_buffer: deque[float] = deque(maxlen=20)
        self._receipt_time: float = 0.0  # time.time() at message receipt
        self._gpsd_toff_received: float = 0.0  # receipt time of last gpsd TOFF
        self.toff_armed = False  # armed: fire on next TPV with GPS time

    @property
    def data(self) -> GPSData:
        with self._lock:
            return self._data

    @property
    def _running(self) -> bool:
        """True while the current run has not been asked to stop."""
        return not self._stop_event.is_set()

    @_running.setter
    def _running(self, value: bool) -> None:
        if not value:
            self._stop_event.set()
        elif self._stop_event.is_set():
            self._stop_event = threading.Event()

    def start(self, on_update: callable = None, on_error: callable = None, on_nmea: callable = None) -> None:
        """Start the gpsd polling thread (stopping any previous one first)."""
        self.stop()
        self._on_update = on_update
        self._on_error = on_error
        self._on_nmea = on_nmea
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(self._stop_event,), daemon=True, name="gpsd-client"
        )
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Stop the polling thread and wait (bounded) for it to exit."""
        self._stop_event.set()
        session, self._session = self._session, None
        self._close_session(session)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(self.STOP_TIMEOUT if timeout is None else timeout)
            if thread.is_alive():
                log.warning("gpsd client thread did not exit within the stop timeout")

    def reset(self) -> None:
        """Discard all received data, e.g. before reconnecting."""
        with self._lock:
            self._data = GPSData()
        self._toff_buffer.clear()
        self._gpsd_toff_received = 0.0

    def _run(self, stop: threading.Event | None = None) -> None:
        """Main polling loop — runs in background thread."""
        if stop is None:
            stop = self._stop_event
        while not stop.is_set():
            session = None
            try:
                session = self._connect()
                self._poll_loop(session, stop)
            except Exception as exc:
                if stop.is_set():
                    break  # errors caused by stop() closing the session
                with self._lock:
                    self._data.connected = False
                    self._data.error_message = str(exc)
                self._call(self._on_error, str(exc))
            finally:
                # Close session before retrying
                if session is not None and self._session is session:
                    self._session = None
                self._close_session(session)

            # Wait before reconnecting (returns at once when stopped)
            stop.wait(self.RECONNECT_DELAY)

    @staticmethod
    def _close_session(session) -> None:
        if session is None:
            return
        try:
            session.close()
        except OSError as exc:
            log.debug("closing gpsd session failed: %s", exc)

    def _connect(self):
        """Establish connection to gpsd. Returns the new session."""
        watch_flags = (
            _gps_module.WATCH_ENABLE
            | _gps_module.WATCH_JSON
            | _gps_module.WATCH_PPS
            | _gps_module.WATCH_TIMING
            | _gps_module.WATCH_NMEA
        )
        session = _gps_module.gps(
            host=self.host,
            port=self.port,
            mode=watch_flags,
        )
        self._session = session
        with self._lock:
            self._data.connected = True
            self._data.error_message = ""
        self._notify_update()
        return session

    def _poll_loop(self, session, stop: threading.Event) -> None:
        """Read reports until disconnected or stopped."""
        while not stop.is_set():
            if not session.waiting(timeout=self.POLL_TIMEOUT):
                continue
            result = session.read()
            self._receipt_time = time.time()  # capture ASAP for precise TOFF
            if result == -1:
                raise ConnectionError("gpsd disconnected")
            if stop.is_set():
                break

            # Capture raw NMEA sentences before extracting structured data
            response = getattr(session, "response", "")
            if response and response.startswith("$"):
                self._call(self._on_nmea, response.rstrip())

            self._extract_data(session)
            self._notify_update()

    def _notify_update(self) -> None:
        self._call(self._on_update, self._data)

    @staticmethod
    def _call(callback, *args) -> None:
        """Invoke a user callback; its exceptions are not connection errors."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            log.exception("gpsd client callback %r failed", callback)

    def _safe_float(self, value) -> float:
        """Convert a value to float, returning NaN for invalid values."""
        try:
            f = float(value)
            return f
        except (TypeError, ValueError):
            return float("nan")

    def _extract_data(self, session=None) -> None:
        """Extract data from the gps session into our data model."""
        s = session if session is not None else self._session
        if s is None:
            return

        with self._lock:
            d = self._data
            d.connected = True
            d.last_seen = time.time()
            d.error_message = ""

            msg_class = s.data.get("class", "") if hasattr(s, "data") and hasattr(s.data, "get") else ""

            if msg_class == "TPV":
                self._extract_tpv(s, d)
            elif msg_class == "SKY":
                self._extract_sky(s, d)
            elif msg_class == "PPS":
                self._extract_pps(s, d)
            elif msg_class == "TOFF":
                self._extract_toff(s, d)
            elif msg_class == "DEVICE":
                self._extract_device(s, d)
            elif msg_class == "DEVICES":
                self._extract_devices(s, d)
            elif msg_class == "VERSION":
                self._extract_version(s, d)

    def _extract_tpv(self, s, d: GPSData) -> None:
        """Extract TPV (Time-Position-Velocity) data."""
        d.tpv_seq += 1
        d.tpv_received = self._receipt_time or time.time()
        f = s.fix
        d.latitude = self._safe_float(f.latitude)
        d.longitude = self._safe_float(f.longitude)
        d.alt_hae = self._safe_float(f.altHAE)
        d.alt_msl = self._safe_float(f.altMSL)
        d.geoid_sep = self._safe_float(f.geoidSep)
        d.speed = self._safe_float(f.speed)
        d.track = self._safe_float(f.track)
        d.climb = self._safe_float(f.climb)
        d.mode = int(f.mode) if f.mode else 0
        d.status = int(f.status) if f.status else 0
        d.magtrack = self._safe_float(f.magtrack)
        d.magvar = self._safe_float(f.magvar)

        # Time
        if f.time and not isinstance(f.time, float):
            d.time = str(f.time)
        elif s.utc:
            d.time = str(s.utc)
        else:
            d.time = ""

        # Leap seconds (from TPV raw data)
        raw = s.data if hasattr(s.data, "get") else {}
        ls = raw.get("leapseconds", 0)
        if ls:
            d.leapseconds = int(ls)

        # Error estimates
        d.errors = ErrorEstimates(
            eph=self._safe_float(f.eph),
            epv=self._safe_float(f.epv),
            ept=self._safe_float(f.ept),
            eps=self._safe_float(f.eps),
            epd=self._safe_float(f.epd),
            epc=self._safe_float(f.epc),
            epx=self._safe_float(f.epx),
            epy=self._safe_float(f.epy),
            sep=self._safe_float(f.sep),
        )

        # ECEF
        d.ecefx = self._safe_float(f.ecefx)
        d.ecefy = self._safe_float(f.ecefy)
        d.ecefz = self._safe_float(f.ecefz)
        d.ecefvx = self._safe_float(f.ecefvx)
        d.ecefvy = self._safe_float(f.ecefvy)
        d.ecefvz = self._safe_float(f.ecefvz)

        # Compute TOFF: GPS time vs system clock (captured at message receipt)
        # gpsd only sends TOFF messages with PPS hardware; we compute it here
        # so it always works when there's a GPS time in the TPV.
        gps_epoch = parse_gps_time(d.time) if d.time else None
        if gps_epoch is not None and self._receipt_time > 0:
            sys_epoch = self._receipt_time  # precise: captured right after s.read()

            # Current TOFF for display
            gps_sec = int(gps_epoch)
            gps_nsec = (gps_epoch - gps_sec) * 1e9
            sys_sec = int(sys_epoch)
            sys_nsec = (sys_epoch - sys_sec) * 1e9

            # A real gpsd TOFF message (PPS-capable devices) takes precedence
            gpsd_toff_age = sys_epoch - self._gpsd_toff_received
            if not (self._gpsd_toff_received > 0 and gpsd_toff_age < self.GPSD_TOFF_HOLD):
                d.toff = TOFFData(
                    real_sec=float(gps_sec),
                    real_nsec=gps_nsec,
                    clock_sec=float(sys_sec),
                    clock_nsec=sys_nsec,
                )

            # Accumulate TOFF sample (offset in seconds)
            offset = gps_epoch - sys_epoch
            self._toff_buffer.append(offset)
            d.toff_samples = list(self._toff_buffer)

            # Armed mode: single-shot on next TPV with GPS time
            if self.toff_armed:
                self.toff_armed = False
                d.toff_armed_offset = offset
                d.toff_armed_gps_time = d.time
                d.toff_armed_sys_time = sys_epoch

    def _extract_sky(self, s, d: GPSData) -> None:
        """Extract SKY (satellite) data."""
        # DOP values from the session object
        d.dop = DOPValues(
            hdop=self._safe_float(s.hdop),
            vdop=self._safe_float(s.vdop),
            pdop=self._safe_float(s.pdop),
            gdop=self._safe_float(s.gdop),
            tdop=self._safe_float(s.tdop),
            xdop=self._safe_float(s.xdop),
            ydop=self._safe_float(s.ydop),
        )

        # Satellite data — use raw data dict for gnssid/svid fields
        # Build list locally then assign atomically to avoid race with render()
        raw_sats = s.data.get("satellites", []) if hasattr(s.data, "get") else []
        new_sats = []
        for raw in raw_sats:
            sat = SatelliteInfo(
                prn=int(raw.get("PRN", 0)),
                gnssid=int(raw.get("gnssid", 0)),
                svid=int(raw.get("svid", 0)),
                elevation=self._safe_float(raw.get("el", float("nan"))),
                azimuth=self._safe_float(raw.get("az", float("nan"))),
                snr=self._safe_float(raw.get("ss", float("nan"))),
                used=bool(raw.get("used", False)),
                sigid=int(raw.get("sigid", 0)),
                health=int(raw.get("health", 0)),
                freqid=int(raw.get("freqid", -1)),
            )
            new_sats.append(sat)

        # Only replace satellite list if we got data; avoids clearing on
        # empty SKY messages that some receivers send between updates
        if new_sats:
            d.satellites = new_sats
            d.satellites_used = sum(1 for sat in new_sats if sat.used)

    def _extract_pps(self, s, d: GPSData) -> None:
        """Extract PPS (Pulse Per Second) timing data."""
        d.pps = PPSData(
            real_sec=self._safe_float(s.real_sec),
            real_nsec=self._safe_float(s.real_nsec),
            clock_sec=self._safe_float(s.clock_sec),
            clock_nsec=self._safe_float(s.clock_nsec),
            precision=int(s.precision) if s.precision else 0,
            qerr=self._safe_float(s.data.get("qErr", float("nan"))) if hasattr(s.data, "get") else float("nan"),
        )

    def _extract_toff(self, s, d: GPSData) -> None:
        """Extract TOFF (Time Offset) data."""
        self._gpsd_toff_received = self._receipt_time or time.time()
        raw = s.data if hasattr(s.data, "get") else {}
        d.toff = TOFFData(
            real_sec=self._safe_float(raw.get("real_sec", float("nan"))),
            real_nsec=self._safe_float(raw.get("real_nsec", float("nan"))),
            clock_sec=self._safe_float(raw.get("clock_sec", float("nan"))),
            clock_nsec=self._safe_float(raw.get("clock_nsec", float("nan"))),
        )

    def _extract_device(self, s, d: GPSData) -> None:
        """Extract DEVICE information."""
        raw = s.data if hasattr(s.data, "get") else {}
        d.device = DeviceInfo(
            path=str(raw.get("path", s.path or "")),
            driver=str(s.gps_id or ""),
            subtype=str(raw.get("subtype", "")),
            bps=int(s.baudrate) if s.baudrate else 0,
            cycle=self._safe_float(s.cycle),
            mincycle=self._safe_float(s.mincycle),
            activated=str(raw.get("activated", "")),
            native=int(raw.get("native", 0)),
        )

    def _extract_devices(self, s, d: GPSData) -> None:
        """Extract device info from a DEVICES (plural) message."""
        raw = s.data if hasattr(s.data, "get") else {}
        devices = raw.get("devices", [])
        if devices:
            dev = devices[0]
            dev_get = dev.get if hasattr(dev, "get") else lambda k, default=None: default
            d.device = DeviceInfo(
                path=str(dev_get("path", "")),
                driver=str(dev_get("driver", "")),
                subtype=str(dev_get("subtype", "")),
                bps=int(dev_get("bps", 0)),
                cycle=self._safe_float(dev_get("cycle", float("nan"))),
                mincycle=self._safe_float(dev_get("mincycle", float("nan"))),
                activated=str(dev_get("activated", "")),
                native=int(dev_get("native", 0)),
            )

    def _extract_version(self, s, d: GPSData) -> None:
        """Extract VERSION information."""
        v = s.version if s.version and hasattr(s.version, "get") else {}
        d.version = VersionInfo(
            release=str(v.get("release", "")),
            proto_major=int(v.get("proto_major", 0)),
            proto_minor=int(v.get("proto_minor", 0)),
        )
