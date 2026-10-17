"""A scriptable fake gpsd TCP server for tests.

It behaves like gpsd on the wire: on each connection it sends a VERSION
banner, records everything the client sends, and once the client has sent a
``?WATCH`` command it streams the scripted lines (CRLF-terminated, like gpsd).
"""

from __future__ import annotations

import json
import socket
import threading
import time

VERSION_BANNER = {
    "class": "VERSION", "release": "3.25", "rev": "3.25", "proto_major": 3, "proto_minor": 15,
}


def jline(obj: dict) -> bytes:
    """Encode one report exactly like gpsd: compact JSON + CRLF."""
    return json.dumps(obj, separators=(",", ":")).encode() + b"\r\n"


class FakeGpsd:
    def __init__(self, host: str = "127.0.0.1", banner: dict | None = VERSION_BANNER,
                 port: int = 0) -> None:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self._server = socket.socket(family, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(8)
        self._server.settimeout(0.1)
        self.host = host
        self.port = self._server.getsockname()[1]
        self.banner = banner
        self.script: list[bytes] = []  # sent to each client after its ?WATCH
        self.received = bytearray()  # everything any client sent
        self.connections = 0
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="fake-gpsd")
        self._thread.start()

    # ─── scripting ───────────────────────────────────────────────

    def add(self, *items: dict | str | bytes) -> None:
        """Append reports (dicts), raw lines (str) or raw bytes to the script."""
        for item in items:
            self.script.append(_encode(item))

    def send(self, *items: dict | str | bytes) -> None:
        """Send to all connected clients now."""
        data = b"".join(_encode(i) for i in items)
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            try:
                c.sendall(data)
            except OSError:
                pass

    def drop_clients(self) -> None:
        """Close every client connection (gpsd going away)."""
        with self._lock:
            clients, self._clients = self._clients, []
        for c in clients:
            _close(c)

    def wait_for(self, predicate, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)
        return True

    def wait_connections(self, n: int, timeout: float = 3.0) -> bool:
        return self.wait_for(lambda: self.connections >= n, timeout)

    def wait_watch(self, timeout: float = 3.0) -> bool:
        return self.wait_for(lambda: b"?WATCH" in self.received and b"\n" in self.received, timeout)

    def close(self) -> None:
        self._closed.set()
        _close(self._server)
        self.drop_clients()
        self._thread.join(2.0)

    # ─── server ──────────────────────────────────────────────────

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _addr = self._server.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            with self._cond:
                self._clients.append(conn)
                self.connections += 1
                self._cond.notify_all()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            if self.banner is not None:
                conn.sendall(jline(self.banner))
            pending = bytearray()
            watched = False
            conn.settimeout(0.1)
            while not self._closed.is_set():
                try:
                    data = conn.recv(4096)
                except (socket.timeout, TimeoutError):
                    continue
                if not data:
                    return
                with self._cond:
                    self.received += data
                    self._cond.notify_all()
                pending += data
                if not watched and b"?WATCH" in pending and b"\n" in pending:
                    watched = True
                    conn.sendall(b"".join(self.script))
        except OSError:
            pass
        finally:
            with self._lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            _close(conn)


def _encode(item: dict | str | bytes) -> bytes:
    if isinstance(item, dict):
        return jline(item)
    if isinstance(item, str):
        return item.encode() + b"\r\n"
    return item


def _close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
