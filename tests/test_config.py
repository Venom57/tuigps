"""Tests for tuigps.config (persistent settings) and its use by the CLI and app."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from tuigps import config as config_mod
from tuigps.config import (
    ALL_GNSS,
    Config,
    check_host,
    check_log_dir,
    check_port,
    default_path,
    load,
    save,
)


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


# ─── Paths ──────────────────────────────────────────────────────────────


def test_default_path_uses_xdg_config_home(config_home):
    assert default_path() == config_home / "tuigps" / "config.json"


def test_default_path_without_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_path() == tmp_path / ".config" / "tuigps" / "config.json"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")  # ignored per the XDG spec
    assert default_path() == tmp_path / ".config" / "tuigps" / "config.json"


# ─── load ───────────────────────────────────────────────────────────────


def test_missing_file_gives_defaults(config_file):
    cfg = load()
    assert cfg == Config()
    assert cfg.host is None and cfg.port is None
    assert cfg.units == "metric" and cfg.coord_format == "dd"
    assert cfg.theme == "textual-dark"
    assert cfg.log_format == "gpx" and cfg.log_dir == "."
    assert cfg.display_gnss == list(ALL_GNSS)  # every constellation shown
    assert cfg.hidden_gnss_ids == frozenset()
    assert cfg.load_warnings == []
    assert not config_file.exists()  # loading never creates the file


def test_save_load_round_trip(config_file):
    cfg = Config(host="gpsbox", port=3000, units="nautical", coord_format="dms",
                 theme="nord", log_format="csv", log_dir="~/logs",
                 display_gnss=["galileo", "gps"])
    assert save(cfg) == config_file
    raw = json.loads(config_file.read_text())
    assert raw["display_gnss"] == ["gps", "galileo"]  # canonical (gnssid) order
    assert "load_warnings" not in raw
    loaded = load()
    assert loaded == Config(host="gpsbox", port=3000, units="nautical", coord_format="dms",
                            theme="nord", log_format="csv", log_dir="~/logs",
                            display_gnss=["gps", "galileo"])
    assert loaded.hidden_gnss_ids == frozenset({1, 3, 4, 5, 6, 7})


@pytest.mark.parametrize("text", ["{not json", "", "\x00\x01", "[1, 2]", "42", "null"])
def test_corrupt_file_gives_defaults_with_warning(config_file, text, caplog):
    write(config_file, text)
    with caplog.at_level(logging.WARNING, logger="tuigps.config"):
        cfg = load()
    assert cfg == Config()
    assert len(cfg.load_warnings) == 1
    assert any("config" in r.getMessage() for r in caplog.records)


def test_unreadable_file_gives_defaults(config_file):
    config_file.mkdir(parents=True)  # a directory cannot be read as a file
    cfg = load()
    assert cfg == Config()
    assert "cannot read" in cfg.load_warnings[0]


def test_non_utf8_file_gives_defaults(config_file):
    config_file.parent.mkdir(parents=True)
    config_file.write_bytes(b'{"units": "\xff"}')
    cfg = load()
    assert cfg == Config()
    assert "cannot read" in cfg.load_warnings[0]


def test_invalid_fields_fall_back_individually(config_file):
    write(config_file, {
        "host": "has space", "port": 70000, "units": "furlongs", "coord_format": "utm",
        "theme": "", "log_format": "kml", "log_dir": 5, "display_gnss": "gps",
    })
    cfg = load()
    assert cfg == Config()
    assert len(cfg.load_warnings) == 8
    assert any("units" in w and "furlongs" in w for w in cfg.load_warnings)


def test_valid_fields_survive_next_to_invalid_ones(config_file):
    write(config_file, {"units": "imperial", "port": "abc", "host": " gpsbox ",
                        "unknown_key": {"x": 1}, "theme": "dracula"})
    cfg = load()
    assert cfg.units == "imperial" and cfg.host == "gpsbox" and cfg.theme == "dracula"
    assert cfg.port is None
    assert len(cfg.load_warnings) == 1  # unknown keys are ignored silently


def test_port_accepts_numeric_string_and_null(config_file):
    write(config_file, {"port": "2948", "host": None})
    cfg = load()
    assert cfg.port == 2948 and cfg.host is None
    write(config_file, {"port": True})
    assert load().port is None


def test_display_gnss_unknown_keys_dropped(config_file, caplog):
    write(config_file, {"display_gnss": ["GPS", "galileo", "martian", 3]})
    with caplog.at_level(logging.WARNING, logger="tuigps.config"):
        cfg = load()
    assert cfg.display_gnss == ["gps", "galileo"]
    assert "martian" in caplog.text


@pytest.mark.parametrize("value", [[], ["martian"], None])
def test_display_gnss_without_known_keys_shows_all(config_file, value):
    write(config_file, {"display_gnss": value})
    cfg = load()
    assert cfg.display_gnss == list(ALL_GNSS)
    assert cfg.load_warnings


def test_explicit_path(tmp_path, config_file):
    path = tmp_path / "custom.json"
    save(Config(units="imperial"), path)
    assert load(path).units == "imperial"
    assert not config_file.exists()


# ─── save ───────────────────────────────────────────────────────────────


def test_save_creates_directory(tmp_path):
    path = tmp_path / "a" / "b" / "config.json"
    save(Config(), path)
    assert json.loads(path.read_text())["units"] == "metric"


def test_save_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save(Config(units="imperial"), path)

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config_mod.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        save(Config(units="nautical"), path)
    # The old file is intact and no temp file is left behind
    assert load(path).units == "imperial"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_save_reports_the_write_error_when_cleanup_fails_too(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("disk full" if len(args) == 2 else "cleanup failed")

    monkeypatch.setattr(config_mod.os, "replace", fail)
    monkeypatch.setattr(config_mod.os, "unlink", fail)
    with pytest.raises(OSError, match="disk full"):
        save(Config(), tmp_path / "config.json")


def test_save_replaces_existing_file(tmp_path):
    path = tmp_path / "config.json"
    save(Config(units="imperial"), path)
    save(Config(units="nautical"), path)
    assert load(path).units == "nautical"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_save_unwritable_directory_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OSError):
        save(Config(), blocker / "config.json")


# ─── Validators ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [("gpsbox", "gpsbox"), (" ::1 ", "::1")])
def test_check_host(value, expected):
    assert check_host(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "two words", "tab\there", None, 5])
def test_check_host_invalid(value):
    with pytest.raises(ValueError):
        check_host(value)


@pytest.mark.parametrize("value", ["0", "65536", "-1", "abc", "29.47", "1.5", "", True])
def test_check_port_invalid(value):
    with pytest.raises(ValueError):
        check_port(value)


def test_check_log_dir_existing_and_creatable(tmp_path, monkeypatch):
    assert check_log_dir(str(tmp_path)) == tmp_path
    assert check_log_dir(str(tmp_path / "new" / "deeper")) == tmp_path / "new" / "deeper"
    assert not (tmp_path / "new").exists()  # validation creates nothing
    monkeypatch.setenv("HOME", str(tmp_path))
    assert check_log_dir("~/logs") == tmp_path / "logs"


def test_check_log_dir_invalid(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    for value in ("", "  ", None):
        with pytest.raises(ValueError, match="empty"):
            check_log_dir(value)
    with pytest.raises(ValueError, match="not a directory"):
        check_log_dir(str(f))
    with pytest.raises(ValueError, match="not a directory"):
        check_log_dir(str(f / "sub"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_check_log_dir_unwritable(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(ValueError, match="not writable"):
            check_log_dir(str(ro))
        with pytest.raises(ValueError, match="not writable"):
            check_log_dir(str(ro / "sub"))
    finally:
        ro.chmod(0o700)


# ─── CLI precedence ─────────────────────────────────────────────────────


from tuigps.cli import parse_args  # noqa: E402


def test_cli_uses_config_file_server(config_file):
    save(Config(host="cfghost", port=3001))
    ns = parse_args([], environ={})
    assert (ns.host, ns.port) == ("cfghost", "3001")
    assert ns.config.host == "cfghost"
    assert ns.config_path == config_file


def test_cli_env_and_args_override_config(config_file):
    save(Config(host="cfghost", port=3001))
    env = {"GPSD_HOST": "envhost", "GPSD_PORT": "4000"}
    assert vars(parse_args([], environ=env))["host"] == "envhost"
    assert parse_args([], environ=env).port == "4000"
    ns = parse_args(["clihost:5000"], environ=env)
    assert (ns.host, ns.port) == ("clihost", "5000")
    # The config itself is unchanged: overrides are for this session only
    assert (ns.config.host, ns.config.port) == ("cfghost", 3001)


def test_cli_config_option(tmp_path, config_file):
    path = tmp_path / "other.json"
    save(Config(host="otherhost", units="imperial"), path)
    save(Config(host="defaulthost"))
    ns = parse_args(["--config", str(path)], environ={})
    assert ns.host == "otherhost" and ns.config.units == "imperial"
    assert ns.config_path == path


def test_cli_config_option_missing_file(tmp_path):
    ns = parse_args(["--config", str(tmp_path / "nope.json")], environ={})
    assert (ns.host, ns.port) == ("127.0.0.1", "2947")
    assert ns.config == Config()


def test_cli_given_config_object_skips_loading(config_file):
    save(Config(host="filehost"))
    ns = parse_args([], environ={}, config=Config(host="objhost"))
    assert ns.host == "objhost"


def test_main_passes_config_to_app(tmp_path, monkeypatch):
    from tuigps import app, cli

    seen = {}

    class FakeApp:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            pass

    monkeypatch.setattr(app, "TuiGPS", FakeApp)
    monkeypatch.delenv("GPSD_HOST", raising=False)
    monkeypatch.delenv("GPSD_PORT", raising=False)
    path = tmp_path / "c.json"
    save(Config(units="nautical", port=2999), path)
    cli.main(["--config", str(path), "--host", "h"])
    assert seen["host"] == "h" and seen["port"] == "2999"
    assert seen["config"].units == "nautical"
    assert seen["config_path"] == path
