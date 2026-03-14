"""GPS data logger — GPX and CSV file output."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .data_model import GPSData


class GPSLogger:
    """Logs GPS fixes to GPX or CSV files."""

    def __init__(self, directory: str = ".", fmt: str = "gpx"):
        self._directory = Path(directory)
        self._format = fmt  # "gpx" or "csv"
        self._file = None
        self._active = False
        self._count = 0
        self._start_time = 0.0
        self._filepath: Path | None = None
        self._last_time = ""

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
    def elapsed(self) -> float:
        if not self._active:
            return 0.0
        return time.time() - self._start_time

    def start(self) -> Path:
        """Start logging. Returns the file path."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = self._format
        self._filepath = self._directory / f"tuigps_{ts}.{ext}"
        self._file = open(self._filepath, "w")
        self._active = True
        self._count = 0
        self._start_time = time.time()
        self._last_time = ""

        if self._format == "gpx":
            self._file.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="tuigps"\n'
                '     xmlns="http://www.topografix.com/GPX/1/1">\n'
                "  <trk>\n"
                "    <name>tuigps log</name>\n"
                "    <trkseg>\n"
            )
        elif self._format == "csv":
            self._file.write(
                "timestamp,latitude,longitude,alt_msl,speed,track,"
                "mode,status,hdop,eph,epv,satellites_used\n"
            )
        self._file.flush()
        return self._filepath

    def stop(self) -> None:
        """Stop logging and close the file."""
        if not self._active:
            return
        if self._file:
            if self._format == "gpx":
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
        if not self._active or not self._file:
            return
        if not data.has_fix:
            return
        # Deduplicate by timestamp
        if data.time == self._last_time:
            return
        self._last_time = data.time

        if self._format == "gpx":
            self._write_gpx_point(data)
        elif self._format == "csv":
            self._write_csv_point(data)
        self._count += 1
        self._file.flush()

    def _write_gpx_point(self, d: GPSData) -> None:
        alt = f"      <ele>{d.alt_msl:.1f}</ele>\n" if math.isfinite(d.alt_msl) else ""
        time_str = f"      <time>{d.time}</time>\n" if d.time else ""
        speed = f"      <speed>{d.speed:.2f}</speed>\n" if math.isfinite(d.speed) else ""
        hdop = f"      <hdop>{d.dop.hdop:.1f}</hdop>\n" if math.isfinite(d.dop.hdop) else ""

        self._file.write(
            f'    <trkpt lat="{d.latitude:.8f}" lon="{d.longitude:.8f}">\n'
            f"{alt}{time_str}{speed}{hdop}"
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
