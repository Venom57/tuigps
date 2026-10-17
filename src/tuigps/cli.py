"""Command-line interface: ``tuigps [server] [--host HOST] [--port PORT]``."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence

from . import __version__

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2947


def parse_server(spec: str) -> tuple[str | None, str | None]:
    """Split ``host``, ``host:port``, ``[v6addr]``, ``[v6addr]:port`` or ``:port``.

    A bare IPv6 address (more than one colon, no brackets) is a host without
    a port. Returns ``(host, port)``; either may be None when not given.
    Raises ValueError for malformed input.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("empty server")
    if spec.startswith("["):
        end = spec.find("]")
        if end < 0:
            raise ValueError(f"missing ']' in {spec!r}")
        host, rest = spec[1:end], spec[end + 1:]
        if not host:
            raise ValueError(f"empty host in {spec!r}")
        if not rest:
            return host, None
        if not rest.startswith(":"):
            raise ValueError(f"unexpected {rest!r} after ']' in {spec!r}")
        return host, rest[1:]
    if spec.count(":") == 1:
        host, port = spec.split(":")
        return host or None, port
    return spec, None


def check_port(value: str | int) -> str:
    """Validate a TCP port (1-65535); returns it normalised as a string."""
    try:
        port = int(str(value).strip(), 10)
    except ValueError:
        raise ValueError(f"invalid port {value!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port {port} out of range (1-65535)")
    return str(port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuigps",
        description="Terminal UI GPS monitor for gpsd.",
        epilog=(
            "The server defaults to $GPSD_HOST / $GPSD_PORT, else "
            f"{DEFAULT_HOST}:{DEFAULT_PORT}. Examples: tuigps gpsbox, "
            "tuigps 192.168.1.20:2947, tuigps [::1]:2947"
        ),
    )
    parser.add_argument(
        "server", nargs="?", metavar="host[:port]",
        help="gpsd server: host, host:port or [ipv6]:port",
    )
    parser.add_argument("--host", help="gpsd host name or address")
    parser.add_argument("--port", help="gpsd TCP port (1-65535)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def parse_args(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> argparse.Namespace:
    """Parse arguments into ``host`` and ``port`` (a validated string).

    Precedence: --host/--port, then the positional server, then the
    GPSD_HOST / GPSD_PORT environment variables, then the defaults.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ

    pos_host = pos_port = None
    if args.server is not None:
        try:
            pos_host, pos_port = parse_server(args.server)
        except ValueError as exc:
            parser.error(f"invalid server {args.server!r}: {exc}")
    if args.host is not None and pos_host is not None:
        parser.error("give the host either as the server argument or with --host, not both")
    if args.port is not None and pos_port is not None:
        parser.error("give the port either in the server argument or with --port, not both")

    host = args.host or pos_host or env.get("GPSD_HOST") or DEFAULT_HOST
    if args.port is not None:
        port, source = args.port, "--port"
    elif pos_port is not None:
        port, source = pos_port, "server"
    elif env.get("GPSD_PORT"):
        port, source = env["GPSD_PORT"], "GPSD_PORT"
    else:
        port, source = DEFAULT_PORT, "default"
    try:
        port = check_port(port)
    except ValueError as exc:
        parser.error(f"{source}: {exc}")

    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]  # --host [::1]
    if not host:
        parser.error("empty host")
    return argparse.Namespace(host=host, port=port)


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for the ``tuigps`` command and ``python -m tuigps``."""
    args = parse_args(argv)
    from .app import TuiGPS  # imported late so --help/--version stay fast

    TuiGPS(host=args.host, port=args.port).run()
