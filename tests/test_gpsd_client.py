"""Tests for tuigps.gpsd_client using the fake ``gps`` module from conftest."""

from __future__ import annotations

import math
import threading
import time as _real_time
import types
from datetime import datetime, timezone

import pytest

from tuigps import gpsd_client
from tuigps.data_model import GPSData
from tuigps.gpsd_client import GPSDClient

NAN = float("nan")
GPS_TIME = "2026-03-14T12:00:00.250Z"
GPS_EPOCH = datetime(2026, 3, 14, 12, 0, 0, 250000, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def fast_sleep(monkeypatch):
    """Make gpsd_client's reconnect delay short."""
    fake_time = types.SimpleNamespace(time=_real_time.time, sleep=lambda s: _real_time.sleep(0.02))
    monkeypatch.setattr(gpsd_client, "time", fake_time)
    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 0.02)
    return fake_time


def _client_with(session) -> GPSDClient:
    c = GPSDClient()
    c._session = session
    return c


def test_uses_fake_gps_module():
    assert getattr(gpsd_client._gps_module, "__fake__", False)


def test_defaults():
    c = GPSDClient()
    assert (c.host, c.port) == ("127.0.0.1", "2947")
    assert isinstance(c.data, GPSData)
    assert c.data is c._data
    assert not c.toff_armed


@pytest.mark.parametrize(
    "value, expected",
    [(1, 1.0), ("2.5", 2.5), (None, NAN), ("n/a", NAN), (NAN, NAN), ([], NAN)],
)
def test_safe_float(value, expected):
    result = GPSDClient()._safe_float(value)
    if math.isnan(expected):
        assert math.isnan(result)
    else:
        assert result == expected


# ─── Connection ─────────────────────────────────────────────────────────


def test_connect_uses_watch_flags_and_notifies(fake_gps, fake_session):
    session = fake_session()
    fake_gps.gps.factory = lambda **kw: session
    updates = []
    c = GPSDClient(host="gps.local", port="1234")
    c._on_update = updates.append
    c._data.error_message = "old"
    c._connect()

    (call,) = fake_gps.gps.calls
    assert call["host"] == "gps.local" and call["port"] == "1234"
    expected = (
        fake_gps.WATCH_ENABLE | fake_gps.WATCH_JSON | fake_gps.WATCH_PPS
        | fake_gps.WATCH_TIMING | fake_gps.WATCH_NMEA
    )
    assert call["mode"] == expected
    assert c._session is session
    assert c.data.connected and c.data.error_message == ""
    assert updates == [c._data]


def test_connect_refused_raises(fake_gps):
    with pytest.raises(ConnectionRefusedError):
        GPSDClient()._connect()


def test_stop_closes_session(fake_session):
    session = fake_session()
    c = _client_with(session)
    c._running = True
    c.stop()
    assert session.closed
    assert c._session is None and not c._running


def test_stop_swallows_close_errors(fake_session):
    session = fake_session()

    def boom():
        raise OSError("bad fd")

    session.close = boom
    c = _client_with(session)
    c.stop()
    assert c._session is None


def test_stop_without_session():
    GPSDClient().stop()


# ─── _extract_data dispatch ────────────────────────────────────────────


def test_extract_data_without_session_is_noop():
    c = GPSDClient()
    c._extract_data()
    assert c.data.last_seen == 0.0


def test_extract_data_unknown_class_marks_seen(fake_session):
    c = _client_with(fake_session(data={"class": "WATCH"}))
    c._data.error_message = "stale error"
    before = _real_time.time()
    c._extract_data()
    assert c.data.connected
    assert c.data.last_seen >= before
    assert c.data.error_message == ""
    assert c.data.mode == 0


def test_extract_data_non_dict_data(fake_session):
    c = _client_with(fake_session(data="garbage"))
    c._extract_data()
    assert c.data.connected


def test_last_seen_updated_by_non_tpv_messages(fake_session):
    """Characterization of F2: every message class advances last_seen."""
    c = _client_with(fake_session(data={"class": "SKY", "satellites": []}))
    c._extract_data()
    assert c.data.last_seen > 0


@pytest.mark.parametrize("cls", ["TPV", "SKY", "PPS", "TOFF", "DEVICE", "DEVICES", "VERSION"])
def test_extract_data_dispatches(cls, fake_session, monkeypatch):
    c = _client_with(fake_session(data={"class": cls}))
    called = []
    method = {
        "TPV": "_extract_tpv", "SKY": "_extract_sky", "PPS": "_extract_pps",
        "TOFF": "_extract_toff", "DEVICE": "_extract_device",
        "DEVICES": "_extract_devices", "VERSION": "_extract_version",
    }[cls]
    monkeypatch.setattr(c, method, lambda s, d: called.append((s, d)))
    c._extract_data()
    assert called == [(c._session, c._data)]


# ─── TPV ────────────────────────────────────────────────────────────────


def test_extract_tpv_full(tpv_session):
    s = tpv_session()
    c = _client_with(s)
    c._extract_data()
    d = c.data
    assert d.latitude == 47.6062 and d.longitude == -122.3321
    assert d.alt_hae == 30.5 and d.alt_msl == 50.25 and d.geoid_sep == -19.75
    assert d.speed == 1.5 and d.track == 271.0 and d.climb == 0.25
    assert d.mode == 3 and d.status == 2
    assert d.magtrack == 255.0 and d.magvar == 15.3
    assert d.time == GPS_TIME
    assert d.leapseconds == 18
    e = d.errors
    assert (e.eph, e.epv, e.ept, e.eps, e.epd, e.epc, e.epx, e.epy, e.sep) == (
        3.1, 4.2, 0.005, 0.6, 12.0, 0.8, 2.1, 2.2, 5.3,
    )
    assert (d.ecefx, d.ecefy, d.ecefz) == (-2304000.1, -3638000.2, 4688000.3)
    assert (d.ecefvx, d.ecefvy, d.ecefvz) == (0.1, 0.2, 0.3)
    assert d.has_fix


def test_extract_tpv_missing_values_are_nan(fake_session):
    s = fake_session(data={"class": "TPV"}, fix={"mode": None, "status": None, "latitude": "bad"})
    c = _client_with(s)
    c._extract_data()
    d = c.data
    assert d.mode == 0 and d.status == 0
    assert math.isnan(d.latitude) and math.isnan(d.alt_msl) and math.isnan(d.errors.eph)
    assert d.time == ""
    assert d.leapseconds == 0
    assert not d.has_fix


def test_extract_tpv_time_falls_back_to_utc(fake_session):
    # python-gps leaves fix.time as NaN (a float) when no time string was seen
    s = fake_session(data={"class": "TPV"}, utc="2026-01-01T00:00:00.000Z")
    c = _client_with(s)
    c._extract_data()
    assert c.data.time == "2026-01-01T00:00:00.000Z"


def test_extract_tpv_leapseconds_zero_keeps_previous(tpv_session):
    s = tpv_session()
    c = _client_with(s)
    c._extract_data()
    s.data = {"class": "TPV"}
    c._extract_data()
    assert c.data.leapseconds == 18


def test_tpv_computes_toff_from_receipt_time(tpv_session):
    c = _client_with(tpv_session())
    c._receipt_time = GPS_EPOCH + 0.012  # system clock 12 ms ahead of GPS
    c._extract_data()
    t = c.data.toff
    assert t.real_sec == float(int(GPS_EPOCH))
    assert t.real_nsec == pytest.approx(250e6, abs=1e3)
    assert t.clock_sec == float(int(GPS_EPOCH))
    assert t.clock_nsec == pytest.approx(262e6, abs=1e3)
    assert c.data.toff_samples == [pytest.approx(-0.012, abs=1e-6)]


def test_tpv_toff_not_computed_without_receipt_time(tpv_session):
    c = _client_with(tpv_session())
    c._extract_data()
    assert math.isnan(c.data.toff.real_sec)
    assert c.data.toff_samples == []


def test_tpv_toff_time_without_fraction(tpv_session):
    c = _client_with(tpv_session(time="2026-03-14T12:00:00Z"))
    epoch = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    c._receipt_time = epoch + 1.0
    c._extract_data()
    assert c.data.toff_samples == [pytest.approx(-1.0)]


def test_tpv_toff_unparseable_time_is_ignored(tpv_session):
    c = _client_with(tpv_session(time="garbage"))
    c._receipt_time = GPS_EPOCH
    c._extract_data()
    assert c.data.time == "garbage"
    assert math.isnan(c.data.toff.real_sec)
    assert c.data.toff_samples == []


def test_tpv_toff_buffer_is_bounded(tpv_session):
    c = _client_with(tpv_session())
    for i in range(25):
        c._receipt_time = GPS_EPOCH + i * 0.001
        c._extract_data()
    samples = c.data.toff_samples
    assert len(samples) == 20
    assert samples[-1] == pytest.approx(-0.024, abs=1e-6)
    assert samples[0] == pytest.approx(-0.005, abs=1e-6)


def test_toff_armed_single_shot(tpv_session):
    s = tpv_session()
    c = _client_with(s)
    c.toff_armed = True
    c._receipt_time = GPS_EPOCH - 0.005
    c._extract_data()
    d = c.data
    assert not c.toff_armed
    assert d.toff_armed_offset == pytest.approx(0.005, abs=1e-6)
    assert d.toff_armed_gps_time == GPS_TIME
    assert d.toff_armed_sys_time == GPS_EPOCH - 0.005

    # The next TPV does not overwrite the single-shot result
    c._receipt_time = GPS_EPOCH + 1.0
    c._extract_data()
    assert d.toff_armed_offset == pytest.approx(0.005, abs=1e-6)
    assert len(d.toff_samples) == 2


def test_gpsd_toff_message_takes_precedence(tpv_session, fake_session):
    """N7: a recent gpsd TOFF message is not overwritten by the TPV-computed TOFF."""
    c = _client_with(fake_session(data={"class": "TOFF", "real_sec": 10, "real_nsec": 20,
                                        "clock_sec": 10, "clock_nsec": 30}))
    c._receipt_time = GPS_EPOCH
    c._extract_data()
    c._session = tpv_session()
    c._receipt_time = GPS_EPOCH + 4.9
    c._extract_data()
    assert c.data.toff.real_sec == 10.0 and c.data.toff.clock_nsec == 30.0
    # TPV-derived samples are still accumulated
    assert len(c.data.toff_samples) == 1

    # Once the gpsd TOFF is older than GPSD_TOFF_HOLD the computed value is used
    c._receipt_time = GPS_EPOCH + 5.1
    c._extract_data()
    assert c.data.toff.real_sec == float(int(GPS_EPOCH))


def test_toff_armed_waits_for_tpv_with_time(fake_session):
    s = fake_session(data={"class": "TPV"}, fix={"mode": 1})
    c = _client_with(s)
    c.toff_armed = True
    c._receipt_time = GPS_EPOCH
    c._extract_data()
    assert c.toff_armed
    assert math.isnan(c.data.toff_armed_offset)


# ─── SKY ────────────────────────────────────────────────────────────────


def _sky_session(fake_session, sats):
    return fake_session(
        data={"class": "SKY", "satellites": sats},
        hdop=0.9, vdop=1.4, pdop=1.7, gdop=2.1, tdop=0.8, xdop=0.6, ydop=0.7,
    )


RAW_SATS = [
    {"PRN": 5, "gnssid": 0, "svid": 5, "el": 65, "az": 45, "ss": 42, "used": True,
     "sigid": 0, "health": 1},
    {"PRN": 72, "gnssid": 6, "svid": 8, "el": 20.5, "az": 270, "ss": 25, "used": False,
     "freqid": 3, "health": 2},
    {"PRN": 211},
]


def test_extract_sky(fake_session):
    c = _client_with(_sky_session(fake_session, RAW_SATS))
    c._extract_data()
    d = c.data
    assert (d.dop.hdop, d.dop.vdop, d.dop.pdop, d.dop.gdop, d.dop.tdop, d.dop.xdop, d.dop.ydop) == (
        0.9, 1.4, 1.7, 2.1, 0.8, 0.6, 0.7,
    )
    assert len(d.satellites) == 3
    assert d.satellites_used == 1
    s0, s1, s2 = d.satellites
    assert (s0.prn, s0.gnssid, s0.svid, s0.elevation, s0.azimuth, s0.snr, s0.used) == (
        5, 0, 5, 65.0, 45.0, 42.0, True,
    )
    assert s0.health == 1 and s0.freqid == -1
    assert s1.gnssid == 6 and s1.freqid == 3 and s1.elevation == 20.5 and not s1.used
    assert s2.prn == 211 and s2.gnssid == 0 and math.isnan(s2.elevation) and math.isnan(s2.snr)


def test_empty_sky_keeps_previous_satellites(fake_session):
    s = _sky_session(fake_session, RAW_SATS)
    c = _client_with(s)
    c._extract_data()
    prev = c.data.satellites
    s.data = {"class": "SKY", "satellites": []}
    s.hdop = 5.0
    c._extract_data()
    assert c.data.satellites is prev
    assert c.data.satellites_used == 1
    assert c.data.dop.hdop == 5.0  # DOPs still refresh


def test_sky_without_satellites_key(fake_session):
    s = fake_session(data={"class": "SKY"}, hdop=1.0)
    c = _client_with(s)
    c._extract_data()
    assert c.data.satellites == []
    assert c.data.dop.hdop == 1.0


# ─── PPS / TOFF / DEVICE / VERSION ─────────────────────────────────────


def test_extract_pps(fake_session):
    s = fake_session(
        data={"class": "PPS", "qErr": 12.5},
        real_sec=100, real_nsec=5, clock_sec=99, clock_nsec=999_999_000, precision=-20,
    )
    c = _client_with(s)
    c._extract_data()
    p = c.data.pps
    assert (p.real_sec, p.real_nsec, p.clock_sec, p.clock_nsec) == (100.0, 5.0, 99.0, 999_999_000.0)
    assert p.precision == -20
    assert p.qerr == 12.5
    assert c.data.pps_offset_us == pytest.approx(1.005)


def test_extract_pps_without_qerr(fake_session):
    c = _client_with(fake_session(data={"class": "PPS"}, real_sec=1, precision=None))
    c._extract_data()
    assert math.isnan(c.data.pps.qerr)
    assert c.data.pps.precision == 0


def test_extract_toff(fake_session):
    s = fake_session(data={"class": "TOFF", "real_sec": 10, "real_nsec": 20,
                           "clock_sec": 30, "clock_nsec": "x"})
    c = _client_with(s)
    c._extract_data()
    t = c.data.toff
    assert (t.real_sec, t.real_nsec, t.clock_sec) == (10.0, 20.0, 30.0)
    assert math.isnan(t.clock_nsec)


def test_extract_device(fake_session):
    s = fake_session(
        data={"class": "DEVICE", "path": "/dev/ttyACM0", "subtype": "PROTVER 18",
              "activated": "2026-03-14T11:00:00Z", "native": 1},
        gps_id="u-blox", baudrate=9600, cycle=1.0, mincycle=0.25,
    )
    c = _client_with(s)
    c._extract_data()
    dev = c.data.device
    assert dev.path == "/dev/ttyACM0" and dev.driver == "u-blox"
    assert dev.subtype == "PROTVER 18" and dev.bps == 9600
    assert dev.cycle == 1.0 and dev.mincycle == 0.25
    assert dev.activated == "2026-03-14T11:00:00Z" and dev.native == 1


def test_extract_device_path_from_session(fake_session):
    s = fake_session(data={"class": "DEVICE"}, path="/dev/ttyUSB0", gps_id=None, baudrate=None)
    c = _client_with(s)
    c._extract_data()
    dev = c.data.device
    assert dev.path == "/dev/ttyUSB0" and dev.driver == "" and dev.bps == 0
    assert math.isnan(dev.cycle)


def test_extract_devices(fake_session):
    s = fake_session(data={"class": "DEVICES", "devices": [
        {"path": "/dev/ttyACM0", "driver": "u-blox", "subtype": "x", "bps": 38400,
         "cycle": 1.0, "mincycle": 0.125, "activated": "a", "native": 1},
        {"path": "/dev/ttyACM1"},
    ]})
    c = _client_with(s)
    c._extract_data()
    dev = c.data.device
    assert dev.path == "/dev/ttyACM0" and dev.driver == "u-blox" and dev.bps == 38400
    assert dev.mincycle == 0.125 and dev.native == 1


def test_extract_devices_empty_keeps_previous(fake_session):
    c = _client_with(fake_session(data={"class": "DEVICES", "devices": []}))
    c._data.device.path = "/dev/keep"
    c._extract_data()
    assert c.data.device.path == "/dev/keep"


def test_extract_devices_non_dict_entry(fake_session):
    c = _client_with(fake_session(data={"class": "DEVICES", "devices": ["weird"]}))
    c._extract_data()
    dev = c.data.device
    assert dev.path == "" and dev.bps == 0 and math.isnan(dev.cycle)


def test_extract_version(fake_session):
    s = fake_session(data={"class": "VERSION"},
                     version={"release": "3.25", "proto_major": 3, "proto_minor": 15})
    c = _client_with(s)
    c._extract_data()
    v = c.data.version
    assert (v.release, v.proto_major, v.proto_minor) == ("3.25", 3, 15)


def test_extract_version_missing(fake_session):
    c = _client_with(fake_session(data={"class": "VERSION"}, version=None))
    c._extract_data()
    assert c.data.version.release == ""


# ─── Run / poll loop ────────────────────────────────────────────────────


class Recorder:
    def __init__(self, client: GPSDClient | None = None, stop_on_error: bool = True):
        self.client = client
        self.stop_on_error = stop_on_error
        self.updates: list[GPSData] = []
        self.errors: list[str] = []
        self.nmea: list[str] = []
        self.snapshots: list[tuple[str, int, bool]] = []

    def on_update(self, data):
        self.updates.append(data)
        self.snapshots.append((data.time, data.mode, data.connected))

    def on_error(self, msg):
        self.errors.append(msg)
        if self.stop_on_error and self.client is not None:
            self.client._running = False  # end _run() without the reconnect sleep

    def on_nmea(self, sentence):
        self.nmea.append(sentence)


def _scripted_client(fake_gps, session):
    fake_gps.gps.factory = lambda **kw: session
    c = GPSDClient()
    rec = Recorder(c)
    c._on_update, c._on_error, c._on_nmea = rec.on_update, rec.on_error, rec.on_nmea
    c._running = True
    return c, rec


def test_run_processes_messages_until_disconnect(fake_gps, fake_session, tpv_session):
    tpv = tpv_session()
    session = fake_session(
        messages=[
            {"data": {"class": "VERSION"}, "version": {"release": "3.25"},
             "response": '{"class":"VERSION"}\n'},
            {"data": {"class": "TPV", "leapseconds": 18}, "fix": vars(tpv.fix),
             "response": '{"class":"TPV"}\n'},
            {"data": {"class": "WATCH"}, "response": "$GPGGA,120000.00,4736.372,N*5C\r\n"},
        ],
        waiting_script=[False, True],
    )
    c, rec = _scripted_client(fake_gps, session)
    c._run()

    # connect notification + 3 messages
    assert len(rec.updates) == 4
    assert all(u is c._data for u in rec.updates)
    assert rec.snapshots[2] == (GPS_TIME, 3, True)
    assert rec.nmea == ["$GPGGA,120000.00,4736.372,N*5C"]
    assert rec.errors == ["gpsd disconnected"]
    assert c.data.connected is False
    assert c.data.error_message == "gpsd disconnected"
    assert c.data.version.release == "3.25"
    assert session.closed and c._session is None
    assert c._receipt_time > 0
    assert session.read_count == 4  # 3 messages + the -1 disconnect


def test_run_connect_failure_calls_on_error(fake_gps):
    c = GPSDClient()
    rec = Recorder(c)
    c._on_error = rec.on_error
    c._running = True
    c._run()
    assert len(rec.errors) == 1
    assert "Connection refused" in rec.errors[0]
    assert not c.data.connected
    assert "Connection refused" in c.data.error_message
    assert c._session is None


def test_run_reconnects_after_failure(fake_gps, fast_sleep):
    c = GPSDClient()
    attempts = []

    def on_error(msg):
        attempts.append(msg)
        if len(attempts) >= 3:
            c._running = False

    c._on_error = on_error
    c._running = True
    c._run()
    assert len(attempts) == 3
    assert len(fake_gps.gps.calls) == 3


def test_poll_loop_stops_when_not_running(fake_gps, fake_session):
    session = fake_session(messages=[{"data": {"class": "WATCH"}}] * 3)
    c, rec = _scripted_client(fake_gps, session)

    def on_update(data):
        rec.updates.append(data)
        if len(rec.updates) == 2:  # connect + first message
            c._running = False

    c._on_update = on_update
    c._run()
    assert rec.errors == []
    assert session.read_count == 1
    assert session.closed


def test_start_and_stop_thread(fake_gps, fast_sleep):
    c = GPSDClient()
    seen = threading.Event()

    def on_error(msg):
        c._running = False
        seen.set()

    c.start(on_error=on_error)
    assert c._thread is not None and c._thread.daemon
    assert c._thread.name == "gpsd-client"
    assert seen.wait(2.0)
    c._thread.join(2.0)
    assert not c._thread.is_alive()
    c.stop()


def test_update_callback_exception_does_not_drop_connection(
    fake_gps, fake_session, tpv_session, caplog
):
    """F3: a failing callback is logged; the connection survives."""
    tpv = tpv_session()
    session = fake_session(messages=[
        {"data": {"class": "TPV"}, "fix": vars(tpv.fix), "response": "$GPGGA,1*00"},
        {"data": {"class": "WATCH"}},
    ])
    c, rec = _scripted_client(fake_gps, session)

    def on_update(data):
        rec.updates.append(data)
        if data.mode == 3:
            raise RuntimeError("widget exploded")

    def on_nmea(sentence):
        raise ValueError("nmea exploded")

    c._on_update = on_update
    c._on_nmea = on_nmea
    c._run()
    # Both messages were processed; only the real disconnect is an error
    assert len(rec.updates) == 3
    assert session.read_count == 3
    assert rec.errors == ["gpsd disconnected"]
    logged = " ".join(r.getMessage() + str(r.exc_info) for r in caplog.records)
    assert "widget exploded" in logged and "nmea exploded" in logged


def test_error_callback_exception_is_contained(fake_gps, fast_sleep):
    c = GPSDClient()
    calls = []

    def on_error(msg):
        calls.append(msg)
        c._running = False
        raise RuntimeError("notify failed")

    c._on_error = on_error
    c._running = True
    c._run()  # must not raise
    assert len(calls) == 1


def _client_threads():
    return [t for t in threading.enumerate() if t.name == "gpsd-client" and t.is_alive()]


def test_restart_does_not_leave_old_thread_running(fake_gps, fast_sleep):
    """F4: stop() joins, and start() never leaves two polling threads."""
    c = GPSDClient()
    c.start()
    old = c._thread
    try:
        c.stop()
        assert not old.is_alive()
        c.start()
        assert c._thread is not old
        assert _client_threads() == [c._thread]
        # start() while running stops the previous run first
        second = c._thread
        c.start()
        assert not second.is_alive()
        assert _client_threads() == [c._thread]
    finally:
        c.stop()
    assert not c._thread.is_alive()
    assert not c._running


def test_stop_interrupts_reconnect_delay(fake_gps, monkeypatch):
    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 30.0)
    c = GPSDClient()
    failed = threading.Event()
    c.start(on_error=lambda msg: failed.set())
    assert failed.wait(2.0)
    t0 = _real_time.monotonic()
    c.stop()
    assert _real_time.monotonic() - t0 < 1.0
    assert not c._thread.is_alive()


def test_stop_interrupts_polling_session(fake_gps, fake_session):
    """A connected thread blocked in waiting() exits promptly on stop()."""

    class IdleSession(fake_session):
        def waiting(self, timeout=0):
            _real_time.sleep(min(timeout, 0.05))
            return False

    session = IdleSession()
    fake_gps.gps.factory = lambda **kw: session
    connected = threading.Event()
    c = GPSDClient()
    c.start(on_update=lambda d: connected.set())
    assert connected.wait(2.0)
    c.stop()
    assert not c._thread.is_alive()
    assert session.closed and c._session is None


def test_stop_from_callback_thread_does_not_self_join(fake_gps, fast_sleep):
    c = GPSDClient()
    done = threading.Event()

    def on_error(msg):
        c.stop()  # runs on the client thread itself
        done.set()

    c.start(on_error=on_error)
    assert done.wait(2.0)
    c._thread.join(2.0)
    assert not c._thread.is_alive()


def test_stopped_run_errors_are_not_reported(fake_gps, fake_session):
    """Errors caused by stop() closing the session are not reported."""
    c = GPSDClient()
    errors = []

    class ClosingSession(fake_session):
        def read(self):
            c._running = False
            raise OSError("socket closed")

    fake_gps.gps.factory = lambda **kw: ClosingSession()
    c._on_error = errors.append
    c._running = True
    c._run()
    assert errors == []


def test_reset_discards_data(tpv_session):
    c = _client_with(tpv_session())
    c._receipt_time = GPS_EPOCH
    c._extract_data()
    old = c.data
    assert old.has_fix and c._toff_buffer
    c.reset()
    assert c.data is not old
    assert not c.data.has_fix and not c._toff_buffer
    assert c._gpsd_toff_received == 0.0
