"""Tests for tuigps.gpsd_client: protocol parsing and the client thread.

Parsing tests feed raw gpsd lines (samples in the format gpsd 3.25 and the
legacy 3.17 emit) through ``_handle_line``; end-to-end tests run the client
against the fake gpsd TCP server in ``tests/fake_gpsd.py``.
"""

from __future__ import annotations

import errno
import json
import math
import threading
import time as _real_time
from datetime import datetime, timezone

import pytest

from tests.fake_gpsd import jline
from tuigps import gpsd_client
from tuigps.data_model import GPSData
from tuigps.gpsd_client import (
    WATCH_COMMAND,
    WATCH_PARAMS,
    GPSDClient,
    LineBuffer,
    _safe_float,
    _safe_int,
    format_server,
)

NAN = float("nan")
GPS_TIME = "2026-03-14T12:00:00.250Z"
GPS_EPOCH = datetime(2026, 3, 14, 12, 0, 0, 250000, tzinfo=timezone.utc).timestamp()

# ─── gpsd sample reports ────────────────────────────────────────────────

VERSION_325 = ('{"class":"VERSION","release":"3.25","rev":"3.25","proto_major":3,'
               '"proto_minor":15}')
DEVICES_325 = (
    '{"class":"DEVICES","devices":[{"class":"DEVICE","path":"/dev/ttyACM0","driver":"u-blox",'
    '"subtype":"SW ROM CORE 3.01 (107888),HW 00080000","subtype1":"FWVER=SPG 3.01,PROTVER=18.00",'
    '"activated":"2026-03-14T11:00:00.000Z","flags":1,"native":1,"bps":9600,"parity":"N",'
    '"stopbits":1,"cycle":1.00,"mincycle":0.25}]}'
)
WATCH_325 = ('{"class":"WATCH","enable":true,"json":true,"nmea":true,"raw":0,"scaled":false,'
             '"timing":true,"split24":false,"pps":true}')
TPV_325 = (
    '{"class":"TPV","device":"/dev/ttyACM0","status":2,"mode":3,"time":"2026-03-14T12:00:00.250Z",'
    '"leapseconds":18,"ept":0.005,"lat":47.606200000,"lon":-122.332100000,"altHAE":30.5000,'
    '"altMSL":50.2500,"alt":50.2500,"epx":2.100,"epy":2.200,"epv":4.200,"track":271.0000,'
    '"magtrack":255.0000,"magvar":15.3,"speed":1.500,"climb":0.250,"epd":12.0000,"eps":0.60,'
    '"epc":0.80,"ecefx":-2304000.1000,"ecefy":-3638000.2000,"ecefz":4688000.3000,"ecefvx":0.10,'
    '"ecefvy":0.20,"ecefvz":0.30,"ecefpAcc":5.30,"ecefvAcc":0.60,"velN":0.026,"velE":-1.500,'
    '"velD":-0.250,"geoidSep":-19.750,"eph":3.100,"sep":5.300,"rtime":1773489600.262,"sor":0,'
    '"chars":12345,"sats":7,"week":2410,"tow":43218.250,"rollovers":2}'
)
SKY_325 = (
    '{"class":"SKY","device":"/dev/ttyACM0","time":"2026-03-14T12:00:00.000Z","xdop":0.60,'
    '"ydop":0.70,"vdop":1.40,"tdop":0.80,"hdop":0.90,"gdop":2.10,"pdop":1.70,"nSat":3,"uSat":1,'
    '"satellites":[{"PRN":5,"gnssid":0,"svid":5,"az":45.0,"el":65.0,"ss":42.0,"used":true,'
    '"health":1},{"PRN":72,"gnssid":6,"svid":8,"freqid":3,"az":270.0,"el":20.5,"ss":25.0,'
    '"used":false,"health":2},{"PRN":211,"used":false}]}'
)
PPS_325 = ('{"class":"PPS","device":"/dev/pps0","real_sec":1773489600,"real_nsec":5,'
           '"clock_sec":1773489599,"clock_nsec":999999000,"precision":-20,"shm":"NTP2","qErr":12.5}')
TOFF_325 = ('{"class":"TOFF","device":"/dev/ttyACM0","real_sec":10,"real_nsec":20,'
            '"clock_sec":10,"clock_nsec":30,"precision":-1,"shm":"NTP0"}')
TPV_MODE1 = ('{"class":"TPV","device":"/dev/ttyACM0","mode":1,"time":"2026-03-14T12:00:05.000Z",'
             '"leapseconds":18}')
VERSION_317 = '{"class":"VERSION","release":"3.17","rev":"3.17","proto_major":3,"proto_minor":12}'
TPV_317 = ('{"class":"TPV","device":"/dev/ttyUSB0","mode":3,"time":"2018-01-01T00:00:00.000Z",'
           '"ept":0.005,"lat":47.0,"lon":-122.0,"alt":100.5,"epx":5.0,"epy":5.0,"epv":10.0,'
           '"track":0.0,"speed":0.0,"climb":0.0,"eps":10.0,"epc":20.0}')
GGA = "$GPGGA,120000.25,4736.372,N,12219.926,W,2,07,0.9,50.2,M,-19.8,M,,*5C"
AIVDM = "!AIVDM,1,1,,A,13aEOK?P00PD2wVMdLDRhgvL289?,0*26"


def tpv(**fields) -> str:
    """A TPV line based on TPV_325 with fields overridden (None removes one)."""
    msg = json.loads(TPV_325)
    for key, value in fields.items():
        if value is None:
            msg.pop(key, None)
        else:
            msg[key] = value
    return json.dumps(msg)


def feed(client: GPSDClient, *lines: str, received: float | None = None) -> GPSDClient:
    for line in lines:
        client._handle_line(line, received)
    return client


@pytest.fixture
def client() -> GPSDClient:
    return GPSDClient()


# ─── Helpers / constants ────────────────────────────────────────────────


def test_watch_command_matches_python_gps():
    """Exactly what python-gps sends for ENABLE|JSON|NMEA|TIMING|PPS."""
    assert WATCH_COMMAND == '?WATCH={"enable":true,"json":true,"nmea":true,"timing":true,"pps":true}\n'
    assert list(WATCH_PARAMS) == ["enable", "json", "nmea", "timing", "pps"]


def test_defaults(client):
    assert client.host == "127.0.0.1"
    assert client.port == gpsd_client.DEFAULT_PORT  # patched to a refusing port in tests
    assert GPSDClient(port=2947).port == "2947"
    assert isinstance(client.data, GPSData)
    assert client.data is not client._data  # a snapshot
    assert not client.toff_armed


@pytest.mark.parametrize(
    "host, port, expected",
    [("127.0.0.1", "2947", "127.0.0.1:2947"), ("::1", 2947, "[::1]:2947"), ("gps", "1", "gps:1")],
)
def test_format_server(host, port, expected):
    assert format_server(host, port) == expected
    assert GPSDClient(host, port).server == expected


@pytest.mark.parametrize(
    "value, expected",
    [(1, 1.0), ("2.5", 2.5), (None, NAN), ("n/a", NAN), (NAN, NAN), ([], NAN), (True, NAN)],
)
def test_safe_float(value, expected):
    result = _safe_float(value)
    if math.isnan(expected):
        assert math.isnan(result)
    else:
        assert result == expected


@pytest.mark.parametrize(
    "value, expected",
    [(3, 3), ("4", 4), (2.9, 2), (None, -1), ("x", -1), (NAN, -1), (float("inf"), -1),
     ({}, -1), (False, -1), ("1e400", -1)],
)
def test_safe_int(value, expected):
    assert _safe_int(value, -1) == expected


# ─── Line buffering ─────────────────────────────────────────────────────


def test_line_buffer_partial_lines_and_crlf():
    buf = LineBuffer()
    assert buf.feed(b'{"class":"TP') == []
    assert buf.feed(b'V"}\r\n$GPGGA,1*00\r') == ['{"class":"TPV"}']
    assert buf.feed(b"\n\r\n\n  \n!AIVDM,x\n") == ["$GPGGA,1*00", "!AIVDM,x"]


def test_line_buffer_invalid_utf8_is_replaced():
    assert LineBuffer().feed(b"$GP\xff\xfeGGA\n") == ["$GP��GGA"]


def test_line_buffer_discards_runaway_line(monkeypatch):
    monkeypatch.setattr(LineBuffer, "MAX_LINE", 16)
    buf = LineBuffer()
    assert buf.feed(b"x" * 20) == []
    assert buf.feed(b"tail\n$OK\n") == ["tail", "$OK"]  # garbage tail, then normal again
    buf.feed(b"abc")
    buf.clear()
    assert buf.feed(b"\n") == []


# ─── Message dispatch ───────────────────────────────────────────────────


def test_garbage_lines_are_ignored(client):
    updates = []
    client._on_update = updates.append
    feed(client, "{not json", "[1,2]", '"str"', "hello", "{}", '{"class":"WEIRD"}', received=5.0)
    # Only the two JSON objects are reports; neither has displayable data
    assert len(updates) == 2
    assert client.data.mode == 0 and client.data.last_seen == 5.0


def test_unknown_class_marks_seen(client):
    client._data.error_message = "stale error"
    feed(client, WATCH_325, '{"class":"GST","rms":1.0}', '{"class":"ATT"}', received=7.0)
    d = client.data
    assert d.last_seen == 7.0 and d.error_message == "stale error"
    assert d.tpv_seq == 0


def test_processing_failure_is_logged_not_fatal(client, monkeypatch, caplog):
    def boom(*a):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(client, "_apply_sky", boom)
    updates = []
    client._on_update = updates.append
    feed(client, SKY_325, TPV_325)
    assert client.data.tpv_seq == 1
    assert len(updates) == 1
    assert "parser exploded" in caplog.text


def test_nmea_and_ais_lines_go_to_on_nmea(client):
    got, updates = [], []
    client._on_nmea = got.append
    client._on_update = updates.append
    feed(client, GGA + "  ", AIVDM, received=9.0)
    assert got == [GGA, AIVDM]
    assert updates == []  # raw sentences don't publish snapshots
    assert client.data.last_seen == 9.0


def test_error_report_surfaces_without_disconnect(client):
    errors = []
    client._on_error = errors.append
    feed(client, '{"class":"ERROR","message":"Unrecognized request \'FOO\'"}')
    assert errors == ["gpsd error: Unrecognized request 'FOO'"]
    assert client.data.error_message == ""


# ─── TPV ────────────────────────────────────────────────────────────────


def test_tpv_full(client):
    d = feed(client, TPV_325).data
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


def test_tpv_status_absent_means_gps_fix(client):
    """gpsd omits status unless DGPS or better (python-gps defaults it to GPS)."""
    assert feed(client, tpv(status=None)).data.status == 1
    assert feed(client, TPV_MODE1).data.status == 0  # no fix: unknown
    assert feed(client, tpv(status=9)).data.status == 9


def test_tpv_mode1_clears_position(client):
    feed(client, TPV_325)
    d = feed(client, TPV_MODE1).data
    assert d.mode == 1 and d.time == "2026-03-14T12:00:05.000Z"
    for value in (d.latitude, d.longitude, d.alt_hae, d.alt_msl, d.geoid_sep, d.speed,
                  d.track, d.climb, d.errors.eph, d.ecefx, d.ecefvz):
        assert math.isnan(value)
    assert d.leapseconds == 18
    assert not d.has_fix


def test_tpv_legacy_317_alt_is_msl(client):
    d = feed(client, VERSION_317, TPV_317).data
    assert d.version.release == "3.17" and d.version.proto_minor == 12
    assert d.alt_msl == 100.5
    assert math.isnan(d.alt_hae) and math.isnan(d.geoid_sep)
    assert d.status == 1 and d.mode == 3
    assert d.errors.epx == 5.0 and math.isnan(d.errors.eph)


def test_tpv_deprecated_alt_ignored_when_hae_given(client):
    # Newer gpsd: "alt" equals altHAE when there is no altMSL - not MSL
    d = feed(client, tpv(altMSL=None, alt=30.5, geoidSep=None)).data
    assert d.alt_hae == 30.5 and math.isnan(d.alt_msl)


def test_tpv_hae_computed_from_msl_and_geoid(client):
    d = feed(client, tpv(altHAE=None)).data
    assert d.alt_hae == pytest.approx(50.25 - 19.75)


def test_tpv_bad_values_are_nan(client):
    d = feed(client, tpv(mode="x", status="bad", lat="n/a", leapseconds="?", time=None)).data
    assert d.mode == 0 and d.status == 0
    assert math.isnan(d.latitude) and d.longitude == -122.3321
    assert d.time == ""
    assert d.leapseconds == 0


def test_tpv_epoch_tracking(client):
    """F1/F2: tpv_seq counts TPV messages; tpv_received is the TPV receipt time."""
    feed(client, TPV_325, received=1000.0)
    assert (client.data.tpv_seq, client.data.tpv_received) == (1, 1000.0)
    for i, line in enumerate((SKY_325, PPS_325, TOFF_325, WATCH_325, GGA)):
        feed(client, line, received=1000.1 + i)
    d = client.data
    assert (d.tpv_seq, d.tpv_received) == (1, 1000.0)
    assert d.last_seen == pytest.approx(1004.1)
    feed(client, TPV_325, received=1010.0)
    assert (client.data.tpv_seq, client.data.tpv_received) == (2, 1010.0)


def test_tpv_computes_toff_from_receipt_time(client):
    feed(client, tpv(status=None), received=GPS_EPOCH + 0.012)  # clock 12 ms ahead
    t = client.data.toff
    assert t.real_sec == float(int(GPS_EPOCH))
    assert t.real_nsec == pytest.approx(250e6, abs=1e3)
    assert t.clock_sec == float(int(GPS_EPOCH))
    assert t.clock_nsec == pytest.approx(262e6, abs=1e3)
    assert client.data.toff_samples == [pytest.approx(-0.012, abs=1e-6)]
    assert client.data.toff_source == "computed"


def test_tpv_toff_time_without_fraction(client):
    epoch = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    feed(client, tpv(time="2026-03-14T12:00:00Z"), received=epoch + 1.0)
    assert client.data.toff_samples == [pytest.approx(-1.0)]


@pytest.mark.parametrize("time_value", ["garbage", None])
def test_tpv_toff_needs_parseable_time(client, time_value):
    feed(client, tpv(time=time_value), received=GPS_EPOCH)
    assert math.isnan(client.data.toff.real_sec)
    assert client.data.toff_samples == []
    assert client.data.toff_source == ""


def test_tpv_toff_buffer_is_bounded(client):
    for i in range(25):
        feed(client, TPV_325, received=GPS_EPOCH + i * 0.001)
    samples = client.data.toff_samples
    assert len(samples) == 20
    assert samples[-1] == pytest.approx(-0.024, abs=1e-6)
    assert samples[0] == pytest.approx(-0.005, abs=1e-6)


def test_toff_armed_single_shot(client):
    client.arm_toff()
    assert client.toff_armed
    feed(client, TPV_MODE1.replace('"time":"2026-03-14T12:00:05.000Z",', ""), received=GPS_EPOCH)
    assert client.toff_armed  # waits for a TPV with a GPS time
    feed(client, TPV_325, received=GPS_EPOCH - 0.005)
    d = client.data
    assert not client.toff_armed
    assert d.toff_armed_offset == pytest.approx(0.005, abs=1e-6)
    assert d.toff_armed_gps_time == GPS_TIME
    assert d.toff_armed_sys_time == GPS_EPOCH - 0.005

    # The next TPV does not overwrite the single-shot result
    feed(client, TPV_325, received=GPS_EPOCH + 1.0)
    assert client.data.toff_armed_offset == pytest.approx(0.005, abs=1e-6)
    assert len(client.data.toff_samples) == 2

    # Re-arming clears the previous result; clear_toff clears everything
    client.arm_toff()
    assert math.isnan(client.data.toff_armed_offset)
    client.clear_toff()
    d = client.data
    assert not client.toff_armed and d.toff_samples == [] and not client._toff_buffer
    assert math.isnan(d.toff_armed_offset) and d.toff_armed_gps_time == ""


def test_gpsd_toff_message_takes_precedence(client):
    """N7: a recent gpsd TOFF message is not overwritten by the TPV-computed TOFF."""
    feed(client, TOFF_325, received=GPS_EPOCH)
    assert client.data.toff_source == "gpsd"
    feed(client, TPV_325, received=GPS_EPOCH + 4.9)
    assert client.data.toff.real_sec == 10.0 and client.data.toff.clock_nsec == 30.0
    assert client.data.toff_source == "gpsd"
    assert len(client.data.toff_samples) == 1  # TPV-derived samples still accumulate
    # Once the gpsd TOFF is older than GPSD_TOFF_HOLD the computed value is used
    feed(client, TPV_325, received=GPS_EPOCH + 5.1)
    assert client.data.toff.real_sec == float(int(GPS_EPOCH))
    assert client.data.toff_source == "computed"
    client.reset()
    assert client.data.toff_source == ""


# ─── SKY ────────────────────────────────────────────────────────────────


def test_sky(client):
    d = feed(client, SKY_325).data
    assert (d.dop.hdop, d.dop.vdop, d.dop.pdop, d.dop.gdop, d.dop.tdop, d.dop.xdop, d.dop.ydop) == (
        0.9, 1.4, 1.7, 2.1, 0.8, 0.6, 0.7,
    )
    assert len(d.satellites) == 3 and d.satellites_used == 1
    s0, s1, s2 = d.satellites
    assert (s0.prn, s0.gnssid, s0.svid, s0.elevation, s0.azimuth, s0.snr, s0.used) == (
        5, 0, 5, 65.0, 45.0, 42.0, True,
    )
    assert s0.health == 1 and s0.freqid == -1 and s0.sigid == 0
    assert s1.gnssid == 6 and s1.freqid == 3 and s1.elevation == 20.5 and not s1.used
    assert s2.prn == 211 and s2.gnssid == 0 and math.isnan(s2.elevation) and math.isnan(s2.snr)


def test_sky_without_satellites_keeps_previous(client):
    feed(client, SKY_325)
    prev = client._data.satellites
    feed(client, '{"class":"SKY","device":"/dev/ttyACM0","hdop":5.0,"pdop":6.0}')
    d = client.data
    assert d.satellites is prev and d.satellites_used == 1
    assert d.dop.hdop == 5.0 and math.isnan(d.dop.vdop)  # DOPs refresh as a set


def test_sky_with_only_satellites_keeps_dops(client):
    feed(client, SKY_325)
    feed(client, '{"class":"SKY","nSat":1,"uSat":1,"satellites":[{"PRN":9,"used":true}]}')
    d = client.data
    assert d.dop.hdop == 0.9
    assert [s.prn for s in d.satellites] == [9] and d.satellites_used == 1


def test_sky_empty_list_keeps_previous_but_nsat0_clears(client):
    feed(client, SKY_325)
    feed(client, '{"class":"SKY","satellites":[]}')
    assert len(client.data.satellites) == 3
    feed(client, '{"class":"SKY","nSat":0,"uSat":0}')
    assert client.data.satellites == [] and client.data.satellites_used == 0


def test_sky_usat_only(client):
    d = feed(client, '{"class":"SKY","uSat":7}').data
    assert d.satellites == [] and d.satellites_used == 7


def test_sky_malformed_entries(client):
    d = feed(client, '{"class":"SKY","satellites":["x",{"PRN":"7","el":"bad","used":1}]}').data
    (sat,) = d.satellites
    assert sat.prn == 7 and math.isnan(sat.elevation) and sat.used


# ─── PPS / TOFF / DEVICE(S) / VERSION ──────────────────────────────────


def test_pps(client):
    p = feed(client, PPS_325).data.pps
    assert (p.real_sec, p.real_nsec, p.clock_sec, p.clock_nsec) == (
        1773489600.0, 5.0, 1773489599.0, 999_999_000.0,
    )
    assert p.precision == -20 and p.qerr == 12.5
    assert client.data.pps_offset_us == pytest.approx(1_000_000 - 999_999 + 0.005)


def test_pps_without_qerr(client):
    p = feed(client, '{"class":"PPS","real_sec":1}').data.pps
    assert math.isnan(p.qerr) and p.precision == 0 and math.isnan(p.clock_sec)


def test_toff(client):
    t = feed(client, '{"class":"TOFF","real_sec":10,"real_nsec":20,"clock_sec":30,'
                     '"clock_nsec":"x"}').data.toff
    assert (t.real_sec, t.real_nsec, t.clock_sec) == (10.0, 20.0, 30.0)
    assert math.isnan(t.clock_nsec)


def test_devices(client):
    dev = feed(client, DEVICES_325).data.device
    assert dev.path == "/dev/ttyACM0" and dev.driver == "u-blox"
    assert dev.subtype.startswith("SW ROM CORE 3.01") and dev.bps == 9600
    assert dev.cycle == 1.0 and dev.mincycle == 0.25
    assert dev.activated == "2026-03-14T11:00:00.000Z" and dev.native == 1


def test_devices_prefers_entry_with_path(client):
    dev = feed(client, '{"class":"DEVICES","devices":["weird",{"driver":"x"},'
                       '{"path":"/dev/ttyUSB1","bps":"4800"}]}').data.device
    assert dev.path == "/dev/ttyUSB1" and dev.bps == 4800 and math.isnan(dev.cycle)


def test_devices_empty_or_invalid_keeps_previous(client):
    feed(client, DEVICES_325)
    feed(client, '{"class":"DEVICES","devices":[]}', '{"class":"DEVICES","devices":null}')
    assert client.data.device.path == "/dev/ttyACM0"


def test_device_updates_merge_for_same_path(client):
    feed(client, DEVICES_325)
    feed(client, '{"class":"DEVICE","path":"/dev/ttyACM0","bps":115200,"cycle":0.5}')
    dev = client.data.device
    assert dev.bps == 115200 and dev.cycle == 0.5
    assert dev.driver == "u-blox" and dev.mincycle == 0.25  # kept
    # A different device does not inherit the old one's fields
    dev = feed(client, '{"class":"DEVICE","path":"/dev/ttyUSB0","driver":"NMEA0183"}').data.device
    assert dev.path == "/dev/ttyUSB0" and dev.driver == "NMEA0183" and dev.bps == 0


def test_version(client):
    v = feed(client, VERSION_325).data.version
    assert (v.release, v.proto_major, v.proto_minor) == ("3.25", 3, 15)
    v = feed(client, '{"class":"VERSION"}').data.version
    assert (v.release, v.proto_major) == ("", 0)


# ─── Snapshots (F5) ─────────────────────────────────────────────────────


def test_published_snapshots_never_change(client):
    snapshots: list[GPSData] = []
    client._on_update = snapshots.append
    feed(client, VERSION_325, DEVICES_325, TPV_325, SKY_325, PPS_325, TOFF_325,
         received=GPS_EPOCH)
    first = snapshots[-1]
    frozen = repr(first)
    sats = list(first.satellites)
    samples = list(first.toff_samples)
    # Lots more traffic, including in-place-looking updates of every kind
    feed(client, TPV_MODE1, '{"class":"SKY","hdop":9.9,"satellites":[{"PRN":1,"used":true}]}',
         '{"class":"PPS","real_sec":1}', '{"class":"TOFF","real_sec":2}',
         '{"class":"DEVICE","path":"/dev/ttyACM0","bps":1}', '{"class":"VERSION","release":"x"}',
         TPV_325, received=GPS_EPOCH + 1)
    client.arm_toff()
    feed(client, TPV_325, received=GPS_EPOCH + 2)
    client.clear_toff()
    client.reset()
    assert repr(first) == frozen
    assert first.satellites == sats and first.toff_samples == samples
    assert snapshots[-1] is not first and snapshots[-1].tpv_seq == 4


def test_data_property_is_a_snapshot(client):
    snap = client.data
    snap.latitude = 1.0
    assert math.isnan(client._data.latitude)
    feed(client, TPV_325)
    assert snap.mode == 0 and client.data.mode == 3


def test_reset_discards_data(client):
    feed(client, TPV_325, TOFF_325, received=GPS_EPOCH)
    assert client.data.has_fix and client._toff_buffer
    client.reset()
    assert not client.data.has_fix and not client._toff_buffer
    assert client._gpsd_toff_received == 0.0


# ─── End to end against the fake gpsd server ───────────────────────────


class Recorder:
    def __init__(self) -> None:
        self.updates: list[GPSData] = []
        self.errors: list[str] = []
        self.nmea: list[str] = []
        self.cond = threading.Condition()

    def on_update(self, data):
        with self.cond:
            self.updates.append(data)
            self.cond.notify_all()

    def on_error(self, msg):
        with self.cond:
            self.errors.append(msg)
            self.cond.notify_all()

    def on_nmea(self, sentence):
        with self.cond:
            self.nmea.append(sentence)
            self.cond.notify_all()

    def wait(self, predicate, timeout: float = 3.0) -> bool:
        with self.cond:
            return self.cond.wait_for(predicate, timeout)

    def start(self, client: GPSDClient) -> None:
        client.start(on_update=self.on_update, on_error=self.on_error, on_nmea=self.on_nmea)

    @property
    def last(self) -> GPSData:
        return self.updates[-1]


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 0.05)
    monkeypatch.setattr(GPSDClient, "POLL_TIMEOUT", 0.05)


def _client_threads():
    return [t for t in threading.enumerate() if t.name == "gpsd-client" and t.is_alive()]


@pytest.fixture
def running():
    """Track started clients and make sure they are stopped."""
    clients: list[GPSDClient] = []

    def _make(*args, **kwargs) -> GPSDClient:
        c = GPSDClient(*args, **kwargs)
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.stop()


def test_end_to_end_stream(fake_gpsd, running):
    fake_gpsd.add(DEVICES_325, WATCH_325, TPV_325, GGA, SKY_325, AIVDM, PPS_325)
    c = running("127.0.0.1", fake_gpsd.port)
    rec = Recorder()
    before = _real_time.time()
    rec.start(c)
    assert rec.wait(lambda: rec.updates and rec.last.pps.precision == -20)
    assert fake_gpsd.wait_watch()
    # Exactly one command, exactly what python-gps would send
    assert bytes(fake_gpsd.received) == WATCH_COMMAND.encode()
    d = rec.last
    assert d.connected and d.version.release == "3.25"
    assert d.device.path == "/dev/ttyACM0"
    assert d.has_fix and d.latitude == 47.6062 and d.status == 2
    assert len(d.satellites) == 3
    assert d.tpv_seq == 1 and before <= d.tpv_received <= d.last_seen
    assert rec.wait(lambda: len(rec.nmea) == 2)
    assert rec.nmea == [GGA, AIVDM]
    assert rec.errors == []
    # Each snapshot is distinct
    assert len({id(u) for u in rec.updates}) == len(rec.updates)


def test_end_to_end_split_and_garbage(fake_gpsd, running):
    """Lines split across TCP segments, garbage and blank lines."""
    c = running("127.0.0.1", fake_gpsd.port)
    rec = Recorder()
    rec.start(c)
    assert fake_gpsd.wait_watch()
    line = jline(json.loads(TPV_325))
    fake_gpsd.send(b"garbage{{\r\n\r\n" + line[:40])
    _real_time.sleep(0.05)
    fake_gpsd.send(line[40:-1])
    _real_time.sleep(0.05)
    fake_gpsd.send(line[-1:])
    assert rec.wait(lambda: rec.updates and rec.last.tpv_seq == 1)
    assert rec.last.latitude == 47.6062 and rec.errors == []


def test_ipv6_loopback(running):
    from tests.fake_gpsd import FakeGpsd

    try:
        server = FakeGpsd(host="::1")
    except OSError:
        pytest.skip("IPv6 loopback unavailable")
    try:
        server.add(TPV_325)
        c = running("::1", server.port)
        rec = Recorder()
        rec.start(c)
        assert rec.wait(lambda: rec.updates and rec.last.has_fix)
    finally:
        server.close()


def test_connection_refused(running, refused_port):
    c = running("127.0.0.1", refused_port)
    rec = Recorder()
    rec.start(c)
    assert rec.wait(lambda: rec.errors)
    assert "Connection refused" in rec.errors[0] and str(refused_port) in rec.errors[0]
    d = c.data
    assert not d.connected and "Connection refused" in d.error_message


def test_unresolvable_host(running, monkeypatch):
    def fail(*a, **k):
        import socket

        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(gpsd_client.socket, "getaddrinfo", fail)
    c = running("no-such-host.invalid", 2947)
    rec = Recorder()
    rec.start(c)
    assert rec.wait(lambda: rec.errors)
    assert rec.errors[0] == "cannot resolve no-such-host.invalid: Name or service not known"


def _idle_select(r, w, x, timeout):
    _real_time.sleep(timeout)
    return [], [], []


def test_connect_timeout(running, monkeypatch):
    """A connect that never completes times out (blackholed host)."""
    monkeypatch.setattr(GPSDClient, "CONNECT_TIMEOUT", 0.1)
    monkeypatch.setattr(gpsd_client.select, "select", _idle_select)
    monkeypatch.setattr(gpsd_client.socket.socket, "connect_ex", lambda self, addr: errno.EINPROGRESS)
    c = running("127.0.0.1", 9)
    rec = Recorder()
    rec.start(c)
    assert rec.wait(lambda: rec.errors)
    assert "timed out" in rec.errors[0]


def test_stop_interrupts_pending_connect(running, monkeypatch):
    monkeypatch.setattr(GPSDClient, "CONNECT_TIMEOUT", 30.0)
    monkeypatch.setattr(GPSDClient, "POLL_TIMEOUT", 0.05)
    monkeypatch.setattr(gpsd_client.select, "select", _idle_select)
    monkeypatch.setattr(gpsd_client.socket.socket, "connect_ex", lambda self, addr: errno.EINPROGRESS)
    c = running("127.0.0.1", 9)
    rec = Recorder()
    rec.start(c)
    _real_time.sleep(0.1)
    t0 = _real_time.monotonic()
    c.stop()
    assert _real_time.monotonic() - t0 < 1.0
    assert not c._thread.is_alive()
    assert rec.errors == []


def test_server_close_reports_disconnect_and_reconnects(fake_gpsd, running, fast_retry):
    fake_gpsd.add(TPV_325)
    c = running("127.0.0.1", fake_gpsd.port)
    rec = Recorder()
    rec.start(c)
    assert rec.wait(lambda: rec.updates and rec.last.has_fix)
    fake_gpsd.drop_clients()
    assert rec.wait(lambda: rec.errors)
    assert rec.errors[0] == "gpsd disconnected"
    assert fake_gpsd.wait_connections(2)
    assert rec.wait(lambda: rec.last.connected and rec.last.tpv_seq == 2)


def test_stop_is_prompt_while_connected(fake_gpsd, running):
    c = running("127.0.0.1", fake_gpsd.port)
    rec = Recorder()
    rec.start(c)
    assert fake_gpsd.wait_watch()
    assert rec.wait(lambda: rec.updates and rec.last.connected)
    t0 = _real_time.monotonic()
    c.stop()
    assert _real_time.monotonic() - t0 < 1.0
    assert not c._thread.is_alive() and c._sock is None
    assert rec.errors == []
    assert _client_threads() == []


def test_stop_interrupts_reconnect_delay(monkeypatch, running, refused_port):
    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 30.0)
    c = running("127.0.0.1", refused_port)
    rec = Recorder()
    rec.start(c)
    assert rec.wait(lambda: rec.errors)
    t0 = _real_time.monotonic()
    c.stop()
    assert _real_time.monotonic() - t0 < 1.0
    assert not c._thread.is_alive()


def test_start_and_restart_threads(fake_gpsd, running):
    """F4: stop() joins, and start() never leaves two reader threads."""
    c = running("127.0.0.1", fake_gpsd.port)
    c.start()
    old = c._thread
    assert old.daemon and old.name == "gpsd-client"
    c.stop()
    assert not old.is_alive()
    c.start()
    second = c._thread
    assert second is not old and _client_threads() == [second]
    c.start()  # start() while running stops the previous run first
    assert not second.is_alive() and _client_threads() == [c._thread]
    c.stop()
    assert not c._thread.is_alive() and not c._running


def test_stop_from_callback_thread_does_not_self_join(running, refused_port):
    c = running("127.0.0.1", refused_port)
    done = threading.Event()

    def on_error(msg):
        c.stop()  # runs on the client thread itself
        done.set()

    c.start(on_error=on_error)
    assert done.wait(2.0)
    c._thread.join(2.0)
    assert not c._thread.is_alive()


def test_callback_exceptions_do_not_drop_connection(fake_gpsd, running, caplog):
    """F3: a failing callback is logged; the connection survives."""
    fake_gpsd.add(TPV_325, GGA, SKY_325)
    c = running("127.0.0.1", fake_gpsd.port)
    rec = Recorder()

    def on_update(data):
        rec.on_update(data)
        raise RuntimeError("widget exploded")

    def on_nmea(sentence):
        raise ValueError("nmea exploded")

    c.start(on_update=on_update, on_error=rec.on_error, on_nmea=on_nmea)
    assert rec.wait(lambda: rec.updates and rec.last.satellites)
    assert rec.errors == []
    assert "widget exploded" in caplog.text and "nmea exploded" in caplog.text


def test_error_callback_exception_is_contained(running, refused_port, fast_retry):
    c = running("127.0.0.1", refused_port)
    calls = []

    def on_error(msg):
        calls.append(msg)
        raise RuntimeError("notify failed")

    c.start(on_error=on_error)
    deadline = _real_time.monotonic() + 2
    while not calls and _real_time.monotonic() < deadline:
        _real_time.sleep(0.01)
    assert calls and c._thread.is_alive()


def test_stopped_run_errors_are_not_reported(running):
    c = running()
    errors = []
    c._on_error = errors.append

    def boom(stop):
        c._running = False
        raise OSError("socket closed")

    c._open_socket = boom
    c._running = True
    c._run()
    assert errors == []


# ─── Connection state and reconnect backoff (U2) ───────────────────────


def test_states_and_backoff_while_gpsd_is_down(running, refused_port, monkeypatch):
    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 0.01)
    monkeypatch.setattr(GPSDClient, "RECONNECT_MAX", 0.08)
    c = running("127.0.0.1", refused_port)
    rec = Recorder()
    rec.start(c)
    down = lambda: [u for u in rec.updates if u.connection_state == "disconnected"]  # noqa: E731
    assert rec.wait(lambda: len(down()) >= 6)
    c.stop()
    delays = [u.retry_delay for u in down()][:6]
    assert delays == [0.01, 0.02, 0.04, 0.08, 0.08, 0.08]
    assert [u.reconnect_attempts for u in down()][:6] == [1, 2, 3, 4, 5, 6]
    first = down()[0]
    assert not first.connected and "Connection refused" in first.error_message
    assert first.next_retry_at == pytest.approx(_real_time.time(), abs=1.0)
    assert rec.updates[0].connection_state == "connecting"
    # One notification per outage, not one per attempt
    assert len(rec.errors) == 1


def test_default_backoff_constants():
    assert GPSDClient.RECONNECT_DELAY == 2.0 and GPSDClient.RECONNECT_MAX == 30.0


def test_outage_and_recovery(fake_gpsd, running, monkeypatch):
    """connected -> disconnected (one error) -> retries -> connected again."""
    from tests.fake_gpsd import FakeGpsd

    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 0.02)
    monkeypatch.setattr(GPSDClient, "RECONNECT_MAX", 0.05)
    port = fake_gpsd.port
    fake_gpsd.add(TPV_325)
    c = running("127.0.0.1", port)
    rec = Recorder()
    rec.start(c)
    assert rec.wait(lambda: rec.updates and rec.last.has_fix)
    assert rec.last.connection_state == "connected" and rec.last.connected

    fake_gpsd.close()  # gpsd goes away entirely: further attempts are refused
    assert rec.wait(lambda: rec.last.reconnect_attempts >= 3)
    lost = next(u for u in rec.updates if u.connection_state == "disconnected")
    assert lost.error_message == "gpsd disconnected"
    assert lost.retry_delay == 0.02  # the connection delivered data: backoff reset
    assert lost.reconnect_attempts == 0  # losing a connection is not a failed attempt
    assert lost.has_fix  # last known data is kept while disconnected
    assert rec.errors == ["gpsd disconnected"]

    server = FakeGpsd(port=port)  # gpsd is back
    try:
        server.add(tpv(lat=10.0))
        assert rec.wait(lambda: rec.last.connected and rec.last.latitude == 10.0)
        d = rec.last
        assert d.connection_state == "connected" and d.error_message == ""
        assert d.reconnect_attempts == 0 and d.next_retry_at == 0.0
        assert rec.errors == ["gpsd disconnected"]
    finally:
        c.stop()
        server.close()


def test_backoff_grows_when_connections_deliver_nothing(running, monkeypatch):
    """gpsd accepting and closing at once (no data) does not reset the backoff."""
    from tests.fake_gpsd import FakeGpsd

    monkeypatch.setattr(GPSDClient, "RECONNECT_DELAY", 0.01)
    monkeypatch.setattr(GPSDClient, "RECONNECT_MAX", 0.04)
    server = FakeGpsd(banner=None)
    stop_dropping = threading.Event()

    def dropper():
        while not stop_dropping.is_set():
            server.drop_clients()
            _real_time.sleep(0.002)

    threading.Thread(target=dropper, daemon=True).start()
    try:
        c = running("127.0.0.1", server.port)
        rec = Recorder()
        rec.start(c)
        down = lambda: [u for u in rec.updates if u.connection_state == "disconnected"]  # noqa: E731
        assert rec.wait(lambda: len(down()) >= 4)
        c.stop()
        assert [u.retry_delay for u in down()][:4] == [0.01, 0.02, 0.04, 0.04]
        # connected -> disconnected is reported each time it happens
        assert all(e == "gpsd disconnected" for e in rec.errors)
    finally:
        stop_dropping.set()
        server.close()
