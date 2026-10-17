"""GPS data logger — GPX and CSV file output."""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .data_model import GPSData

# GPX 1.1 has no speed element; the Garmin TrackPointExtension v2 does.
GPXTPX_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v2"


LOG_FORMATS = ("gpx", "csv")


class GPSLogger:
    """Logs GPS fixes to GPX or CSV files.

    The directory (``~`` expanded, created if missing) and format apply to
    the next ``start()``; a file being written keeps its own format.
    """

    def __init__(self, directory: str | Path = ".", fmt: str = "gpx"):
        self._directory = Path(directory)
        self._format = fmt  # "gpx" or "csv": for the next start()
        self._check_format(fmt)
        self._file_format = fmt  # format of the open file
        self._file = None
        self._active = False
        self._count = 0
        self._start_time = 0.0
        self._filepath: Path | None = None
        self._last_time = ""
        # log_point() runs on the gpsd thread, start()/stop() on the UI thread
        self._lock = threading.Lock()

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def fix_count(self) -> int:
        return self._count

    @property
    def filepath(self) -> Path | None:
        return self._filepath

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def format(self) -> str:
        return self._format

    @staticmethod
    def _check_format(fmt: str) -> None:
        if fmt not in LOG_FORMATS:
            raise ValueError(f"unknown log format {fmt!r} (expected gpx or csv)")

    def configure(self, directory: str | Path | None = None, fmt: str | None = None) -> None:
        """Set the directory and/or format used by the next start()."""
        if fmt is not None:
            self._check_format(fmt)
        with self._lock:
            if directory is not None:
                self._directory = Path(directory)
            if fmt is not None:
                self._format = fmt

    @property
    def elapsed(self) -> float:
        if not self._active:
            return 0.0
        return time.time() - self._start_time

    def _open_new_file(self) -> None:
        """Create a new log file, never overwriting an existing one.

        Creates the directory if needed. Raises OSError on failure, leaving
        the logger inactive.
        """
        directory = self._directory.expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = self._format
        suffix = 0
        while True:
            name = f"tuigps_{ts}.{ext}" if suffix == 0 else f"tuigps_{ts}_{suffix}.{ext}"
            path = directory / name
            try:
                self._file = open(path, "x", encoding="utf-8")
            except FileExistsError:
                suffix += 1
                continue
            self._filepath = path.absolute()
            self._file_format = self._format
            return

    def start(self) -> Path:
        """Start logging to a new file and return its absolute path.

        If a log is already active it is finished and closed first, so a
        second start() rotates to a new file instead of leaking the old one.
        Raises OSError if the directory or file cannot be created.
        """
        with self._lock:
            self._stop()
            return self._start()

    def _start(self) -> Path:
        self._open_new_file()
        try:
            self._write_header()
        except OSError:
            self._file.close()
            self._file = None
            raise
        self._active = True
        self._count = 0
        self._start_time = time.time()
        self._last_time = ""
        return self._filepath

    def _write_header(self) -> None:
        if self._file_format == "gpx":
            self._file.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="tuigps"\n'
                '     xmlns="http://www.topografix.com/GPX/1/1"\n'
                f'     xmlns:gpxtpx="{GPXTPX_NS}">\n'
                "  <trk>\n"
                "    <name>tuigps log</name>\n"
                "    <trkseg>\n"
            )
        elif self._file_format == "csv":
            self._file.write(
                "timestamp,latitude,longitude,alt_msl,speed,track,"
                "mode,status,hdop,eph,epv,satellites_used\n"
            )
        self._file.flush()

    def stop(self) -> None:
        """Stop logging and close the file."""
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if not self._active:
            return
        if self._file:
            if self._file_format == "gpx":
                self._file.write(
                    "    </trkseg>\n"
                    "  </trk>\n"
                    "</gpx>\n"
                )
            self._file.flush()
            self._file.close()
            self._file = None
        self._active = False

    def log_point(self, data: GPSData) -> None:
        """Log a single GPS fix."""
        with self._lock:
            self._log_point(data)

    def _log_point(self, data: GPSData) -> None:
        if not self._active or not self._file:
            return
        if not data.has_fix:
            return
        # Deduplicate by timestamp (a fix without a timestamp can't be a repeat)
        if data.time:
            if data.time == self._last_time:
                return
            self._last_time = data.time

        if self._file_format == "gpx":
            self._write_gpx_point(data)
        elif self._file_format == "csv":
            self._write_csv_point(data)
        self._count += 1
        self._file.flush()

    def _write_gpx_point(self, d: GPSData) -> None:
        # Child order follows the GPX 1.1 wptType sequence:
        # ele, time, ..., sat, hdop, vdop, pdop, ..., extensions
        alt = f"      <ele>{d.alt_msl:.1f}</ele>\n" if math.isfinite(d.alt_msl) else ""
        time_str = f"      <time>{d.time}</time>\n" if d.time else ""
        sat = f"      <sat>{d.satellites_used}</sat>\n" if d.satellites_used > 0 else ""
        dops = "".join(
            f"      <{name}>{value:.1f}</{name}>\n"
            for name, value in (("hdop", d.dop.hdop), ("vdop", d.dop.vdop), ("pdop", d.dop.pdop))
            if math.isfinite(value)
        )
        ext = ""
        if math.isfinite(d.speed):
            ext = (
                "      <extensions><gpxtpx:TrackPointExtension>"
                f"<gpxtpx:speed>{d.speed:.2f}</gpxtpx:speed>"
                "</gpxtpx:TrackPointExtension></extensions>\n"
            )

        self._file.write(
            f'    <trkpt lat="{d.latitude:.8f}" lon="{d.longitude:.8f}">\n'
            f"{alt}{time_str}{sat}{dops}{ext}"
            f"    </trkpt>\n"
        )

    def _write_csv_point(self, d: GPSData) -> None:
        def fv(v: float) -> str:
            return f"{v:.6f}" if math.isfinite(v) else ""

        self._file.write(
            f"{d.time},{d.latitude:.8f},{d.longitude:.8f},"
            f"{fv(d.alt_msl)},{fv(d.speed)},{fv(d.track)},"
            f"{d.mode},{d.status},{fv(d.dop.hdop)},"
            f"{fv(d.errors.eph)},{fv(d.errors.epv)},{d.satellites_used}\n"
        )
