"""Native gpsd client: reads the gpsd JSON protocol in a background thread.

The client speaks the gpsd protocol directly over TCP (stdlib only): it sends
one ``?WATCH`` command after connecting and then reads newline-terminated
lines. JSON objects (``{...}``) are decoded into a private working
:class:`GPSData`; raw NMEA/AIS lines (``$...`` / ``!...``, enabled by
``"nmea":true``) are passed through to ``on_nmea``.

Every processed JSON message publishes a snapshot to ``on_update``: a shallow
copy of the working data. The client never mutates a nested object (DOP,
errors, PPS, TOFF, device, version) or a list (satellites, toff_samples) in
place - it always assigns a new one - so a published snapshot never changes
after it has been handed out.
"""

from __future__ import annotations

import copy
import errno
import json
import logging
import math
import os
import select
import socket
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

log = logging.getLogger(__name__)

NAN = float("nan")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "2947"

# The ?WATCH parameters, in the order python-gps (gps.client) emits them for
# WATCH_ENABLE | WATCH_JSON | WATCH_NMEA | WATCH_TIMING | WATCH_PPS.
WATCH_PARAMS: dict[str, bool] = {
    "enable": True,
    "json": True,
    "nmea": True,
    "timing": True,
    "pps": True,
}
WATCH_COMMAND = "?WATCH=" + json.dumps(WATCH_PARAMS, separators=(",", ":")) + "\n"

_IN_PROGRESS = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY}
_IN_PROGRESS.add(getattr(errno, "WSAEWOULDBLOCK", errno.EWOULDBLOCK))


class _Stopped(Exception):
    """Raised inside the client thread when stop() interrupts a connect."""


def format_server(host: str, port) -> str:
    """``host:port``, with brackets around IPv6 literals."""
    return f"[{host}]:{port}" if ":" in str(host) else f"{host}:{port}"


def _safe_float(value) -> float:
    """Convert a value to float, returning NaN for missing/invalid values."""
    if isinstance(value, bool):
        return NAN
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return NAN


def _safe_int(value, default: int = 0) -> int:
    """Convert a value to int, returning ``default`` for missing/invalid values."""
    if value is None or isinstance(value, bool):
        return default
    try:
        f = float(value)
        if not math.isfinite(f):
            return default
        return int(f)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_str(value) -> str:
    return "" if value is None else str(value)


class LineBuffer:
    """Reassembles newline-terminated lines from arbitrary recv() chunks."""

    MAX_LINE = 64 * 1024  # a partial line longer than this is discarded

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        """Add a chunk; return the complete, decoded, non-blank lines it finished."""
        self._buf += chunk
        lines: list[str] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            raw = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            line = raw.decode("utf-8", errors="replace").strip("\r").strip()
            if line:
                lines.append(line)
        if len(self._buf) > self.MAX_LINE:
            log.warning("discarding %d bytes of gpsd input without a newline", len(self._buf))
            self._buf.clear()
        return lines

    def clear(self) -> None:
        self._buf.clear()


class GPSDClient:
    """Connects to gpsd in a background thread and keeps GPSData updated."""

    RECONNECT_DELAY = 2.0  # first reconnect delay; doubles per failed attempt
    RECONNECT_MAX = 30.0  # reconnect backoff cap
    CONNECT_TIMEOUT = 5.0  # TCP connect timeout per address
    POLL_TIMEOUT = 0.5  # max wait per recv()/select(); bounds stop() latency
    STOP_TIMEOUT = 1.0  # max time stop() waits for the thread to exit
    RECV_SIZE = 65536
    GPSD_TOFF_HOLD = 5.0  # prefer a gpsd TOFF message this recent over computed TOFF

    def __init__(self, host: str | None = None, port: str | int | None = None):
        self.host = DEFAULT_HOST if host is None else str(host)
        self.port = DEFAULT_PORT if port is None else str(port)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        # Each run gets its own stop event, so a stopped thread can never be
        # revived by a later start() (see _running).
        self._stop_event = threading.Event()
        self._stop_event.set()
        self._data = GPSData()  # private working copy, guarded by _lock
        self._lock = threading.Lock()
        self._on_update: callable = None
        self._on_error: callable = None
        self._on_nmea: callable = None
        self._toff_buffer: deque[float] = deque(maxlen=20)
        self._gpsd_toff_received: float = 0.0  # receipt time of last gpsd TOFF
        self.toff_armed = False  # armed: fire on next TPV with GPS time
        self._got_data = False  # current connection has delivered a line

    @property
    def server(self) -> str:
        return format_server(self.host, self.port)

    @property
    def data(self) -> GPSData:
        """A snapshot of the current data (safe to read from any thread)."""
        with self._lock:
            return copy.copy(self._data)

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

    # ─── Lifecycle ───────────────────────────────────────────────────

    def start(self, on_update: callable = None, on_error: callable = None, on_nmea: callable = None) -> None:
        """Start the gpsd reader thread (stopping any previous one first)."""
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
        """Stop the reader thread and wait (bounded) for it to exit."""
        self._stop_event.set()
        sock, self._sock = self._sock, None
        self._close_socket(sock)
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

    def arm_toff(self) -> None:
        """Arm a single-shot TOFF measurement on the next TPV with a GPS time."""
        with self._lock:
            d = self._data
            d.toff_armed_offset = NAN
            d.toff_armed_gps_time = ""
            d.toff_armed_sys_time = 0.0
            self.toff_armed = True

    def clear_toff(self) -> None:
        """Clear the TOFF history and any armed result."""
        with self._lock:
            self.toff_armed = False
            self._toff_buffer.clear()
            d = self._data
            d.toff_samples = []
            d.toff_armed_offset = NAN
            d.toff_armed_gps_time = ""
            d.toff_armed_sys_time = 0.0

    # ─── Thread main loop ────────────────────────────────────────────

    def _run(self, stop: threading.Event | None = None) -> None:
        """Connect / read / reconnect loop - runs in the background thread.

        Reconnects with exponential backoff (RECONNECT_DELAY doubling up to
        RECONNECT_MAX), reset once a connection has delivered data. on_error
        fires only on a transition: the first failure of a run, or losing an
        established connection - not on every failed retry.
        """
        if stop is None:
            stop = self._stop_event
        delay = self.RECONNECT_DELAY
        error_reported = False  # on_error already fired for the current outage
        while not stop.is_set():
            sock = None
            self._got_data = False
            was_connected = False
            try:
                self._set_state("connecting")
                sock = self._open_socket(stop)
                self._sock = sock
                if stop.is_set():
                    break
                sock.sendall(WATCH_COMMAND.encode("ascii"))
                was_connected = True
                error_reported = False
                self._set_state("connected")
                self._read_loop(sock, stop)
            except _Stopped:
                break
            except Exception as exc:
                if stop.is_set():
                    break  # errors caused by stop() closing the socket
                if self._got_data:
                    delay = self.RECONNECT_DELAY  # a working connection resets the backoff
                message = self._describe_error(exc)
                log.info("gpsd connection to %s: %s (retry in %.0fs)", self.server, message, delay)
                self._set_state("disconnected", error=message, retry_delay=delay,
                                failed_attempt=not was_connected)
                if was_connected or not error_reported:
                    error_reported = True
                    self._call(self._on_error, message)
            finally:
                if sock is not None and self._sock is sock:
                    self._sock = None
                self._close_socket(sock)

            # Wait before reconnecting (returns at once when stopped)
            if stop.wait(delay):
                break
            delay = min(delay * 2, self.RECONNECT_MAX)

    def _set_state(self, state: str, error: str | None = None, retry_delay: float = 0.0,
                   failed_attempt: bool = False) -> None:
        """Record a connection state change and publish it."""
        with self._lock:
            d = self._data
            d.connection_state = state
            d.connected = state == "connected"
            if state == "connected":
                d.error_message = ""
                d.reconnect_attempts = 0
            if error is not None:
                d.error_message = error
            if failed_attempt:
                d.reconnect_attempts += 1
            d.retry_delay = retry_delay
            d.next_retry_at = time.time() + retry_delay if retry_delay > 0 else 0.0
        self._publish()

    def _describe_error(self, exc: BaseException) -> str:
        if isinstance(exc, ConnectionError) and str(exc) == "gpsd disconnected":
            return "gpsd disconnected"
        if isinstance(exc, socket.gaierror):
            return f"cannot resolve {self.host}: {exc.strerror or exc}"
        if isinstance(exc, OSError) and exc.strerror:
            return f"{exc.strerror} ({self.server})"
        return f"{exc or type(exc).__name__} ({self.server})"

    def _open_socket(self, stop: threading.Event) -> socket.socket:
        """Connect to gpsd (IPv4/IPv6/hostname), honouring stop and a timeout."""
        infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        last_exc: Exception = OSError(f"no address for {self.host}")
        for family, socktype, proto, _canon, addr in infos:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.setblocking(False)
                err = sock.connect_ex(addr)
                if err and err not in _IN_PROGRESS:
                    raise OSError(err, os.strerror(err))
                deadline = time.monotonic() + self.CONNECT_TIMEOUT
                while err:
                    if stop.is_set():
                        raise _Stopped()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(errno.ETIMEDOUT, "Connection timed out")
                    _r, w, x = select.select([], [sock], [sock], min(self.POLL_TIMEOUT, remaining))
                    if w or x:
                        err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        if err:
                            raise OSError(err, os.strerror(err))
                        break
                sock.settimeout(self.POLL_TIMEOUT)
                return sock
            except _Stopped:
                sock.close()
                raise
            except OSError as exc:
                sock.close()
                last_exc = exc
        raise last_exc

    @staticmethod
    def _close_socket(sock: socket.socket | None) -> None:
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)  # wakes a recv() blocked in another thread
        except OSError:
            pass
        try:
            sock.close()
        except OSError as exc:
            log.debug("closing gpsd socket failed: %s", exc)

    def _read_loop(self, sock: socket.socket, stop: threading.Event) -> None:
        """Read and process lines until disconnected or stopped."""
        buffer = LineBuffer()
        while not stop.is_set():
            try:
                chunk = sock.recv(self.RECV_SIZE)
            except (socket.timeout, TimeoutError):
                continue
            received = time.time()  # capture ASAP: receipt time for TOFF / fix age
            if not chunk:
                raise ConnectionError("gpsd disconnected")
            if stop.is_set():
                break
            for line in buffer.feed(chunk):
                if stop.is_set():
                    return
                self._got_data = True
                self._handle_line(line, received)

    # ─── Message processing ──────────────────────────────────────────

    def _handle_line(self, line: str, received: float | None = None) -> None:
        """Process one protocol line (JSON report or raw NMEA/AIS sentence)."""
        if received is None:
            received = time.time()
        if line[:1] in ("$", "!"):
            with self._lock:
                self._data.last_seen = received
            self._call(self._on_nmea, line.rstrip())
            return
        if line[:1] != "{":
            log.debug("ignoring non-JSON gpsd line: %.80r", line)
            return
        try:
            msg = json.loads(line)
        except ValueError:
            log.debug("ignoring malformed gpsd JSON: %.80r", line)
            return
        if not isinstance(msg, dict):
            log.debug("ignoring non-object gpsd JSON: %.80r", line)
            return
        try:
            error = self._process_message(msg, received)
        except Exception:
            log.exception("failed to process gpsd %s message", msg.get("class"))
            return
        self._publish()
        if error:
            self._call(self._on_error, error)

    def _process_message(self, msg: dict, received: float) -> str | None:
        """Apply one decoded JSON report to the working data.

        Returns an error message to surface (gpsd ERROR reports), else None.
        """
        cls = msg.get("class", "")
        with self._lock:
            d = self._data
            d.last_seen = received
            if cls == "TPV":
                self._apply_tpv(msg, d, received)
            elif cls == "SKY":
                self._apply_sky(msg, d)
            elif cls == "PPS":
                self._apply_pps(msg, d)
            elif cls == "TOFF":
                self._apply_toff(msg, d, received)
            elif cls == "DEVICE":
                self._apply_device(msg, d)
            elif cls == "DEVICES":
                self._apply_devices(msg, d)
            elif cls == "VERSION":
                self._apply_version(msg, d)
            elif cls == "ERROR":
                message = _safe_str(msg.get("message")) or "unknown error"
                log.warning("gpsd error: %s", message)
                return f"gpsd error: {message}"
            # WATCH, GST, ATT, IMU, POLL, RAW, AIS, ...: not displayed
        return None

    def _publish(self) -> None:
        """Hand a snapshot of the working data to on_update."""
        if self._on_update is None:
            return
        with self._lock:
            snapshot = copy.copy(self._data)
        self._call(self._on_update, snapshot)

    @staticmethod
    def _call(callback, *args) -> None:
        """Invoke a user callback; its exceptions are not connection errors."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            log.exception("gpsd client callback %r failed", callback)

    # Every _apply_* method assigns new nested objects/lists rather than
    # mutating existing ones, so published snapshots stay unchanged.

    def _apply_tpv(self, m: dict, d: GPSData, received: float) -> None:
        """TPV (time-position-velocity). gpsd omits fields it doesn't know."""
        d.tpv_seq += 1
        d.tpv_received = received
        d.mode = _safe_int(m.get("mode"), 0)
        # gpsd only reports status when it is DGPS or better; absent means a
        # plain GPS fix (python-gps defaults it to STATUS_GPS) - or no fix.
        default_status = 1 if d.mode >= 2 else 0
        d.status = _safe_int(m.get("status"), default_status)
        d.time = _safe_str(m.get("time"))
        d.latitude = _safe_float(m.get("lat"))
        d.longitude = _safe_float(m.get("lon"))

        alt_hae = _safe_float(m.get("altHAE"))
        alt_msl = _safe_float(m.get("altMSL"))
        geoid_sep = _safe_float(m.get("geoidSep"))
        if "altMSL" not in m and "altHAE" not in m:
            # gpsd < 3.20 only sends "alt" (MSL). Newer gpsd also sends the
            # deprecated "alt", but altMSL/altHAE then take precedence.
            alt_msl = _safe_float(m.get("alt"))
        if not math.isfinite(alt_hae) and math.isfinite(alt_msl) and math.isfinite(geoid_sep):
            alt_hae = alt_msl + geoid_sep
        d.alt_hae = alt_hae
        d.alt_msl = alt_msl
        d.geoid_sep = geoid_sep

        d.speed = _safe_float(m.get("speed"))
        d.track = _safe_float(m.get("track"))
        d.climb = _safe_float(m.get("climb"))
        d.magtrack = _safe_float(m.get("magtrack"))
        d.magvar = _safe_float(m.get("magvar"))
        if "leapseconds" in m:
            d.leapseconds = _safe_int(m.get("leapseconds"), d.leapseconds)

        d.errors = ErrorEstimates(
            eph=_safe_float(m.get("eph")),
            epv=_safe_float(m.get("epv")),
            ept=_safe_float(m.get("ept")),
            eps=_safe_float(m.get("eps")),
            epd=_safe_float(m.get("epd")),
            epc=_safe_float(m.get("epc")),
            epx=_safe_float(m.get("epx")),
            epy=_safe_float(m.get("epy")),
            sep=_safe_float(m.get("sep")),
        )

        d.ecefx = _safe_float(m.get("ecefx"))
        d.ecefy = _safe_float(m.get("ecefy"))
        d.ecefz = _safe_float(m.get("ecefz"))
        d.ecefvx = _safe_float(m.get("ecefvx"))
        d.ecefvy = _safe_float(m.get("ecefvy"))
        d.ecefvz = _safe_float(m.get("ecefvz"))

        self._compute_toff(d, received)

    def _compute_toff(self, d: GPSData, received: float) -> None:
        """TOFF from the TPV time vs. the system clock at receipt.

        gpsd only sends TOFF reports with PPS-capable devices; computing it
        here makes it work whenever a TPV carries a GPS time.
        """
        gps_epoch = parse_gps_time(d.time) if d.time else None
        if gps_epoch is None or received <= 0:
            return
        sys_epoch = received

        # A real gpsd TOFF message (PPS-capable devices) takes precedence
        gpsd_toff_age = sys_epoch - self._gpsd_toff_received
        if not (self._gpsd_toff_received > 0 and gpsd_toff_age < self.GPSD_TOFF_HOLD):
            gps_sec = int(gps_epoch)
            sys_sec = int(sys_epoch)
            d.toff = TOFFData(
                real_sec=float(gps_sec),
                real_nsec=(gps_epoch - gps_sec) * 1e9,
                clock_sec=float(sys_sec),
                clock_nsec=(sys_epoch - sys_sec) * 1e9,
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

    _DOP_KEYS = ("hdop", "vdop", "pdop", "gdop", "tdop", "xdop", "ydop")

    def _apply_sky(self, m: dict, d: GPSData) -> None:
        """SKY (satellites and DOPs).

        gpsd >= 3.23 may send SKY reports with only DOPs, or only nSat/uSat,
        so values that are absent keep their previous state.
        """
        if any(k in m for k in self._DOP_KEYS):
            d.dop = DOPValues(**{k: _safe_float(m.get(k)) for k in self._DOP_KEYS})

        raw_sats = m.get("satellites")
        new_sats: list[SatelliteInfo] = []
        if isinstance(raw_sats, list):
            for raw in raw_sats:
                if not isinstance(raw, dict):
                    continue
                new_sats.append(SatelliteInfo(
                    prn=_safe_int(raw.get("PRN"), 0),
                    gnssid=_safe_int(raw.get("gnssid"), 0),
                    svid=_safe_int(raw.get("svid"), 0),
                    elevation=_safe_float(raw.get("el")),
                    azimuth=_safe_float(raw.get("az")),
                    snr=_safe_float(raw.get("ss")),
                    used=bool(raw.get("used", False)),
                    sigid=_safe_int(raw.get("sigid"), 0),
                    health=_safe_int(raw.get("health"), 0),
                    freqid=_safe_int(raw.get("freqid"), -1),
                ))

        if new_sats:
            d.satellites = new_sats
            d.satellites_used = sum(1 for sat in new_sats if sat.used)
        elif "nSat" in m and _safe_int(m.get("nSat"), -1) == 0:
            # gpsd explicitly reports an empty sky view
            d.satellites = []
            d.satellites_used = 0
        elif "uSat" in m and not d.satellites:
            # No satellite list, only the GGA "satellites used" count
            d.satellites_used = _safe_int(m.get("uSat"), d.satellites_used)

    def _apply_pps(self, m: dict, d: GPSData) -> None:
        d.pps = PPSData(
            real_sec=_safe_float(m.get("real_sec")),
            real_nsec=_safe_float(m.get("real_nsec")),
            clock_sec=_safe_float(m.get("clock_sec")),
            clock_nsec=_safe_float(m.get("clock_nsec")),
            precision=_safe_int(m.get("precision"), 0),
            qerr=_safe_float(m.get("qErr")),
        )

    def _apply_toff(self, m: dict, d: GPSData, received: float) -> None:
        self._gpsd_toff_received = received
        d.toff = TOFFData(
            real_sec=_safe_float(m.get("real_sec")),
            real_nsec=_safe_float(m.get("real_nsec")),
            clock_sec=_safe_float(m.get("clock_sec")),
            clock_nsec=_safe_float(m.get("clock_nsec")),
        )

    @staticmethod
    def _device_info(dev: dict) -> DeviceInfo:
        return DeviceInfo(
            path=_safe_str(dev.get("path")),
            driver=_safe_str(dev.get("driver")),
            subtype=_safe_str(dev.get("subtype")),
            bps=_safe_int(dev.get("bps"), 0),
            cycle=_safe_float(dev.get("cycle")),
            mincycle=_safe_float(dev.get("mincycle")),
            activated=_safe_str(dev.get("activated")),
            native=_safe_int(dev.get("native"), 0),
        )

    def _apply_device(self, m: dict, d: GPSData) -> None:
        """DEVICE: a device was added/changed. Fields absent keep their value."""
        prev = d.device
        path = _safe_str(m.get("path"))
        if prev.path and path and path != prev.path:
            prev = DeviceInfo()  # a different device: don't mix fields
        new = self._device_info(m)
        d.device = DeviceInfo(
            path=path or prev.path,
            driver=new.driver if "driver" in m else prev.driver,
            subtype=new.subtype if "subtype" in m else prev.subtype,
            bps=new.bps if "bps" in m else prev.bps,
            cycle=new.cycle if "cycle" in m else prev.cycle,
            mincycle=new.mincycle if "mincycle" in m else prev.mincycle,
            activated=new.activated if "activated" in m else prev.activated,
            native=new.native if "native" in m else prev.native,
        )

    def _apply_devices(self, m: dict, d: GPSData) -> None:
        """DEVICES: the device list; show the first device that has a path."""
        devices = [dev for dev in m.get("devices") or [] if isinstance(dev, dict)]
        if not devices:
            return
        chosen = next((dev for dev in devices if dev.get("path")), devices[0])
        d.device = self._device_info(chosen)

    def _apply_version(self, m: dict, d: GPSData) -> None:
        d.version = VersionInfo(
            release=_safe_str(m.get("release")),
            proto_major=_safe_int(m.get("proto_major"), 0),
            proto_minor=_safe_int(m.get("proto_minor"), 0),
        )
