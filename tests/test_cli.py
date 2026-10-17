"""Tests for the command line interface (tuigps.cli)."""

from __future__ import annotations

import runpy
import sys

import pytest

from tuigps import __version__, cli
from tuigps.cli import check_port, parse_args, parse_server


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("gpsbox", ("gpsbox", None)),
        ("gpsbox:3000", ("gpsbox", "3000")),
        ("192.168.1.20:2947", ("192.168.1.20", "2947")),
        ("[::1]:2947", ("::1", "2947")),
        ("[fe80::1%eth0]", ("fe80::1%eth0", None)),
        ("::1", ("::1", None)),
        ("2001:db8::5", ("2001:db8::5", None)),
        (":3000", (None, "3000")),
    ],
)
def test_parse_server(spec, expected):
    assert parse_server(spec) == expected


@pytest.mark.parametrize("spec", ["", "[::1", "[]:2947", "[::1]2947"])
def test_parse_server_invalid(spec):
    with pytest.raises(ValueError):
        parse_server(spec)


@pytest.mark.parametrize("value, expected", [("1", "1"), (2947, "2947"), (" 65535 ", "65535")])
def test_check_port(value, expected):
    assert check_port(value) == expected


@pytest.mark.parametrize("value", ["0", "65536", "-1", "abc", "29.47", ""])
def test_check_port_invalid(value):
    with pytest.raises(ValueError):
        check_port(value)


def args(*argv, env=None):
    ns = parse_args(list(argv), environ=env or {})
    return ns.host, ns.port


def test_defaults():
    assert args() == ("127.0.0.1", "2947")


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["gpsbox"], ("gpsbox", "2947")),
        (["gpsbox:3000"], ("gpsbox", "3000")),
        (["[::1]:2947"], ("::1", "2947")),
        (["::1"], ("::1", "2947")),
        ([":4000"], ("127.0.0.1", "4000")),
        (["--host", "10.0.0.5"], ("10.0.0.5", "2947")),
        (["--host", "[::1]", "--port", "3000"], ("::1", "3000")),
        (["gpsbox", "--port", "3000"], ("gpsbox", "3000")),
        (["--port", "3000"], ("127.0.0.1", "3000")),
    ],
)
def test_arguments(argv, expected):
    assert args(*argv) == expected


def test_environment_fallback():
    env = {"GPSD_HOST": "envhost", "GPSD_PORT": "4000"}
    assert args(env=env) == ("envhost", "4000")
    assert args("cli", env=env) == ("cli", "4000")
    assert args("cli:5000", env=env) == ("cli", "5000")
    assert args("--host", "h", "--port", "1", env=env) == ("h", "1")


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--port", "0"], "out of range"),
        (["--port", "70000"], "out of range"),
        (["--port", "http"], "invalid port"),
        (["gpsbox:99999"], "out of range"),
        (["[::1"], "invalid server"),
        (["gpsbox", "--host", "other"], "not both"),
        (["gpsbox:1", "--port", "2"], "not both"),
        (["--host", " "], "empty host"),
    ],
)
def test_invalid_arguments_exit_cleanly(argv, message, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(argv, environ={})
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert message in err and "usage: tuigps" in err


def test_invalid_env_port(capsys):
    with pytest.raises(SystemExit):
        parse_args([], environ={"GPSD_PORT": "nope"})
    assert "GPSD_PORT" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"tuigps {__version__}"


class FakeApp:
    instances: list[FakeApp] = []

    def __init__(self, host=None, port=None, config=None, config_path=None):
        self.host, self.port, self.ran = host, port, False
        self.config, self.config_path = config, config_path
        FakeApp.instances.append(self)

    def run(self):
        self.ran = True


@pytest.fixture
def fake_app(monkeypatch):
    from tuigps import app

    FakeApp.instances = []
    monkeypatch.setattr(app, "TuiGPS", FakeApp)
    monkeypatch.delenv("GPSD_HOST", raising=False)
    monkeypatch.delenv("GPSD_PORT", raising=False)
    return FakeApp


def test_main_runs_app_with_host_and_port(fake_app):
    cli.main(["[::1]:3000"])
    (inst,) = fake_app.instances
    assert (inst.host, inst.port, inst.ran) == ("::1", "3000", True)


def test_app_run_delegates_to_cli(fake_app):
    from tuigps.app import run

    run(["gpsbox"])
    assert (fake_app.instances[0].host, fake_app.instances[0].port) == ("gpsbox", "2947")


def test_python_dash_m_uses_cli(fake_app, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tuigps", "otherhost:1234"])
    runpy.run_module("tuigps", run_name="__main__")
    (inst,) = fake_app.instances
    assert (inst.host, inst.port, inst.ran) == ("otherhost", "1234", True)


def test_tuigps_constructor_passes_server():
    from tuigps.app import TuiGPS

    app = TuiGPS(host="::1", port=3000)
    assert (app._gpsd.host, app._gpsd.port) == ("::1", "3000")
    assert app._gpsd.server == "[::1]:3000"
