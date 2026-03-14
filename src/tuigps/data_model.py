"""Dataclasses representing the complete GPS state."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum


class FixMode(IntEnum):
    NO_FIX = 1
    FIX_2D = 2
    FIX_3D = 3


class FixStatus(IntEnum):
    UNKNOWN = 0
    GPS = 1
    DGPS = 2
    RTK_FIX = 3
    RTK_FLOAT = 4
    DR = 5
    GNSS_DR = 6
    TIME_ONLY = 7
    SIMULATED = 8
    PPS_FIX = 9


@dataclass
class SatelliteInfo:
    prn: int = 0
    gnssid: int = 0
    svid: int = 0
    elevation: float = float("nan")
    azimuth: float = float("nan")
    snr: float = float("nan")
    used: bool = False
    sigid: int = 0
    health: int = 0
    freqid: int = -1


@dataclass
class DOPValues:
    hdop: float = float("nan")
    vdop: float = float("nan")
    pdop: float = float("nan")
    gdop: float = float("nan")
    tdop: float = float("nan")
    xdop: float = float("nan")
    ydop: float = float("nan")


@dataclass
class ErrorEstimates:
    eph: float = float("nan")
    epv: float = float("nan")
    ept: float = float("nan")
    eps: float = float("nan")
    epd: float = float("nan")
    epc: float = float("nan")
    epx: float = float("nan")
    epy: float = float("nan")
    sep: float = float("nan")


@dataclass
class PPSData:
    real_sec: float = float("nan")
    real_nsec: float = float("nan")
    clock_sec: float = float("nan")
    clock_nsec: float = float("nan")
    precision: int = 0
    qerr: float = float("nan")


@dataclass
class TOFFData:
    real_sec: float = float("nan")
    real_nsec: float = float("nan")
    clock_sec: float = float("nan")
    clock_nsec: float = float("nan")


@dataclass
class DeviceInfo:
    path: str = ""
    driver: str = ""
    subtype: str = ""
    bps: int = 0
    cycle: float = float("nan")
    mincycle: float = float("nan")
    activated: str = ""
    native: int = 0


@dataclass
class VersionInfo:
    release: str = ""
    proto_major: int = 0
    proto_minor: int = 0


@dataclass
class GPSData:
    """Complete GPS state — single source of truth for all widgets."""

    # Connection state
    connected: bool = False
    last_seen: float = 0.0
    error_message: str = ""

    # TPV data
    latitude: float = float("nan")
    longitude: float = float("nan")
    alt_hae: float = float("nan")
    alt_msl: float = float("nan")
    geoid_sep: float = float("nan")
    speed: float = float("nan")
    track: float = float("nan")
    climb: float = float("nan")
    mode: int = 0
    status: int = 0
    time: str = ""
    magtrack: float = float("nan")
    magvar: float = float("nan")
    leapseconds: int = 0

    # ECEF coordinates
    ecefx: float = float("nan")
    ecefy: float = float("nan")
    ecefz: float = float("nan")
    ecefvx: float = float("nan")
    ecefvy: float = float("nan")
    ecefvz: float = float("nan")

    # Composites
    dop: DOPValues = field(default_factory=DOPValues)
    errors: ErrorEstimates = field(default_factory=ErrorEstimates)
    pps: PPSData = field(default_factory=PPSData)
    toff: TOFFData = field(default_factory=TOFFData)
    device: DeviceInfo = field(default_factory=DeviceInfo)
    version: VersionInfo = field(default_factory=VersionInfo)

    # TOFF history (last N offset samples in seconds, computed on gpsd thread)
    toff_samples: list[float] = field(default_factory=list)

    # Satellites
    satellites: list[SatelliteInfo] = field(default_factory=list)
    satellites_used: int = 0

    @property
    def constellation_counts(self) -> dict[int, tuple[int, int]]:
        """Return {gnssid: (visible, used)} for each constellation."""
        counts: dict[int, list[int]] = {}
        for sat in self.satellites:
            if sat.gnssid not in counts:
                counts[sat.gnssid] = [0, 0]
            counts[sat.gnssid][0] += 1
            if sat.used:
                counts[sat.gnssid][1] += 1
        return {k: (v[0], v[1]) for k, v in counts.items()}

    @property
    def has_fix(self) -> bool:
        return self.mode >= 2 and math.isfinite(self.latitude) and math.isfinite(self.longitude)

    @property
    def pps_offset_us(self) -> float:
        """PPS offset in microseconds (real - clock)."""
        if not math.isfinite(self.pps.real_sec) or not math.isfinite(self.pps.clock_sec):
            return float("nan")
        sec_diff = self.pps.real_sec - self.pps.clock_sec
        nsec_diff = self.pps.real_nsec - self.pps.clock_nsec
        return (sec_diff * 1_000_000) + (nsec_diff / 1_000)
