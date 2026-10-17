"""Persistent user configuration (``$XDG_CONFIG_HOME/tuigps/config.json``).

Loading never fails: a missing file gives the defaults, and an unreadable,
corrupt or partly invalid file gives the defaults for the bad fields (each
problem is logged and kept in ``Config.load_warnings``). Unknown keys are
ignored. Saving is atomic (temp file + ``os.replace``).

``host`` and ``port`` are ``None`` unless the user chose a server in Settings;
the command line and ``GPSD_HOST`` / ``GPSD_PORT`` override them for one
session without being written back.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import GNSS_KEYS

log = logging.getLogger(__name__)

UNITS = ("metric", "imperial", "nautical")
COORD_FORMATS = ("dd", "dms", "ddm")
LOG_FORMATS = ("gpx", "csv")
DEFAULT_THEME = "textual-dark"
ALL_GNSS = tuple(GNSS_KEYS.values())  # display filter keys, in gnssid order


def default_path() -> Path:
    """``$XDG_CONFIG_HOME/tuigps/config.json`` (default ``~/.config``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or ""
    root = Path(base) if os.path.isabs(base) else Path.home() / ".config"
    return root / "tuigps" / "config.json"


# ─── Validators (shared with the CLI and the settings screen) ──────────


def check_host(value: Any) -> str:
    """A gpsd host: non-empty, no whitespace. Returns it stripped."""
    if not isinstance(value, str):
        raise ValueError("host must be a string")
    host = value.strip()
    if not host:
        raise ValueError("host is empty")
    if any(ch.isspace() for ch in host):
        raise ValueError("host must not contain spaces")
    return host


def check_port(value: str | int) -> str:
    """Validate a TCP port (1-65535); returns it normalised as a string."""
    if isinstance(value, bool):
        raise ValueError(f"invalid port {value!r}")
    try:
        port = int(str(value).strip(), 10)
    except ValueError:
        raise ValueError(f"invalid port {value!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port {port} out of range (1-65535)")
    return str(port)


def check_log_dir(value: Any) -> Path:
    """A log directory that exists (and is writable) or can be created.

    ``~`` is expanded. Nothing is created here; returns the expanded path.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("log directory is empty")
    path = Path(value.strip()).expanduser()
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"{path} is not a directory")
        if not os.access(path, os.W_OK | os.X_OK):
            raise ValueError(f"{path} is not writable")
        return path
    # The nearest existing ancestor must be a writable directory
    parent = path.absolute().parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"cannot create {path}: {parent} is not a directory")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError(f"cannot create {path}: {parent} is not writable")
    return path


def _check_choice(value: Any, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ValueError(f"expected one of {', '.join(choices)}")
    return value


def _check_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a non-empty string")
    return value.strip()


def _check_gnss(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("expected a list of constellations")
    keys = {str(k).lower() for k in value if isinstance(k, str)}
    unknown = sorted(keys - set(ALL_GNSS)) + [repr(k) for k in value if not isinstance(k, str)]
    if unknown:
        log.warning("config: ignoring unknown constellations %s", ", ".join(unknown))
    shown = [k for k in ALL_GNSS if k in keys]
    if not shown:
        raise ValueError("no known constellation selected")
    return shown


# ─── Config ────────────────────────────────────────────────────────────


@dataclass
class Config:
    host: str | None = None  # None: GPSD_HOST or 127.0.0.1
    port: int | None = None  # None: GPSD_PORT or 2947
    units: str = "metric"
    coord_format: str = "dd"
    theme: str = DEFAULT_THEME
    log_format: str = "gpx"
    log_dir: str = "."  # "~" is expanded when used
    display_gnss: list[str] = field(default_factory=lambda: list(ALL_GNSS))
    # Problems found by load() (not saved)
    load_warnings: list[str] = field(default_factory=list, compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "units": self.units,
            "coord_format": self.coord_format,
            "theme": self.theme,
            "log_format": self.log_format,
            "log_dir": self.log_dir,
            "display_gnss": [k for k in ALL_GNSS if k in set(self.display_gnss)],
        }

    @property
    def hidden_gnss_ids(self) -> frozenset[int]:
        """gnssids of the known constellations the display filter hides."""
        shown = set(self.display_gnss)
        return frozenset(gid for gid, key in GNSS_KEYS.items() if key not in shown)


_VALIDATORS = {
    "host": lambda v: None if v is None else check_host(v),
    "port": lambda v: None if v is None else int(check_port(v)),
    "units": lambda v: _check_choice(v, UNITS),
    "coord_format": lambda v: _check_choice(v, COORD_FORMATS),
    "theme": _check_text,
    "log_format": lambda v: _check_choice(v, LOG_FORMATS),
    "log_dir": _check_text,
    "display_gnss": _check_gnss,
}


def from_dict(raw: Any) -> Config:
    """Build a Config from parsed JSON; bad fields keep their defaults."""
    cfg = Config()
    if not isinstance(raw, dict):
        cfg.load_warnings.append("config file is not a JSON object; using defaults")
        return cfg
    for key, validate in _VALIDATORS.items():
        if key not in raw:
            continue
        try:
            setattr(cfg, key, validate(raw[key]))
        except (ValueError, TypeError) as exc:
            cfg.load_warnings.append(f"invalid {key} {raw[key]!r} ({exc}); using the default")
    return cfg


def load(path: str | os.PathLike | None = None) -> Config:
    """Load the config file; never raises (problems -> defaults + warnings)."""
    path = Path(path).expanduser() if path is not None else default_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError) as exc:
        cfg = Config()
        cfg.load_warnings.append(f"cannot read {path}: {exc}; using defaults")
    else:
        try:
            raw = json.loads(text)
        except ValueError as exc:
            cfg = Config()
            cfg.load_warnings.append(f"{path} is not valid JSON ({exc}); using defaults")
        else:
            cfg = from_dict(raw)
    for warning in cfg.load_warnings:
        log.warning("config: %s", warning)
    return cfg


def save(config: Config, path: str | os.PathLike | None = None) -> Path:
    """Write the config atomically (creating its directory). Raises OSError."""
    path = Path(path).expanduser() if path is not None else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(config.to_dict(), indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
