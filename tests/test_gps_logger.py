"""Tests for tuigps.gps_logger (GPX/CSV output)."""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET

import pytest

from tuigps.data_model import GPSData
from tuigps.gps_logger import GPSLogger

GPX_NS = {"g": "http://www.topografix.com/GPX/1/1",
          "tpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v2"}

# GPX 1.1 wptType child sequence (all optional, order is mandatory)
WPT_SEQUENCE = [
    "ele", "time", "magvar", "geoidheight", "name", "cmt", "desc", "src", "link", "sym",
    "type", "fix", "sat", "hdop", "vdop", "pdop", "ageofdgpsdata", "dgpsid", "extensions",
]


def _gpx_points(path):
    root = ET.parse(path).getroot()
    return root.findall("g:trk/g:trkseg/g:trkpt", GPX_NS)


def test_initial_state(tmp_path):
    lg = GPSLogger(directory=str(tmp_path))
    assert not lg.is_active
    assert lg.fix_count == 0
    assert lg.filepath is None
    assert lg.elapsed == 0.0


def test_log_point_when_inactive_is_noop(tmp_path, make_gps_data):
    lg = GPSLogger(directory=str(tmp_path))
    lg.log_point(make_gps_data())
    assert lg.fix_count == 0
    assert list(tmp_path.iterdir()) == []


def test_stop_without_start_is_noop(tmp_path):
    GPSLogger(directory=str(tmp_path)).stop()


class TestGPX:
    def test_start_creates_file(self, tmp_path):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        assert lg.is_active
        assert path.parent == tmp_path
        assert path.name.startswith("tuigps_") and path.suffix == ".gpx"
        assert lg.filepath == path
        assert lg.elapsed >= 0.0
        text = path.read_text()
        assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert "<trkseg>" in text
        lg.stop()

    def test_points_written_and_well_formed(self, tmp_path, make_gps_data):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(make_gps_data(time="2026-03-14T12:00:00Z"))
        lg.log_point(make_gps_data(time="2026-03-14T12:00:01Z", latitude=47.7))
        assert lg.fix_count == 2
        lg.stop()
        assert not lg.is_active

        pts = _gpx_points(path)
        assert len(pts) == 2
        assert pts[0].get("lat") == "47.60620000"
        assert pts[0].get("lon") == "-122.33210000"
        assert pts[0].find("g:ele", GPX_NS).text == "50.2"  # alt_msl 50.25 -> .1f (banker's)
        assert pts[0].find("g:time", GPX_NS).text == "2026-03-14T12:00:00Z"
        assert pts[0].find("g:hdop", GPX_NS).text == "0.9"
        assert pts[1].get("lat") == "47.70000000"

    def test_optional_fields_omitted_when_nan(self, tmp_path):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(GPSData(mode=2, latitude=1.0, longitude=2.0, time="t1"))
        lg.stop()
        (pt,) = _gpx_points(path)
        assert [child.tag.split("}")[1] for child in pt] == ["time"]

    def test_fix_without_timestamp_is_logged(self, tmp_path):
        """N7: dedupe only applies to fixes with a timestamp."""
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(GPSData(mode=2, latitude=1.0, longitude=2.0, time=""))
        lg.log_point(GPSData(mode=2, latitude=1.5, longitude=2.0, time=""))
        lg.stop()
        assert lg.fix_count == 2
        pts = _gpx_points(path)
        assert len(pts) == 2 and pts[0].find("g:time", GPX_NS) is None

    def test_deduplicates_by_timestamp(self, tmp_path, make_gps_data):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        d = make_gps_data()
        for _ in range(5):
            lg.log_point(d)
        assert lg.fix_count == 1
        lg.stop()
        assert len(_gpx_points(path)) == 1

    @pytest.mark.parametrize(
        "overrides",
        [{"mode": 1}, {"mode": 0}, {"latitude": float("nan")}, {"longitude": float("nan")}],
    )
    def test_no_fix_skipped(self, tmp_path, make_gps_data, overrides):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(make_gps_data(**overrides))
        assert lg.fix_count == 0
        lg.stop()
        assert _gpx_points(path) == []

    def test_stop_is_idempotent(self, tmp_path, make_gps_data):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(make_gps_data())
        lg.stop()
        lg.stop()
        text = path.read_text()
        assert text.count("</gpx>") == 1
        # Logging after stop is ignored
        lg.log_point(make_gps_data(time="2026-03-14T13:00:00Z"))
        assert lg.fix_count == 1
        assert lg.elapsed == 0.0

    def test_restart_resets_count_and_dedupe(self, tmp_path, make_gps_data, monkeypatch):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        lg.start()
        lg.log_point(make_gps_data())
        lg.stop()
        lg.start()
        assert lg.fix_count == 0
        lg.log_point(make_gps_data())  # same timestamp as before, but new session
        assert lg.fix_count == 1
        lg.stop()

    def test_gpx_1_1_trkpt_children_are_valid_and_ordered(self, tmp_path, make_gps_data):
        """F6: only wptType children, in schema order; speed lives in an extension."""
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(make_gps_data())
        lg.stop()
        (pt,) = _gpx_points(path)
        tags = []
        for child in pt:
            ns, _, name = child.tag[1:].partition("}")
            assert ns == GPX_NS["g"], child.tag
            tags.append(name)
        assert tags == ["ele", "time", "sat", "hdop", "vdop", "pdop", "extensions"]
        assert tags == sorted(tags, key=WPT_SEQUENCE.index)
        assert pt.find("g:speed", GPX_NS) is None
        assert pt.find("g:sat", GPX_NS).text == "3"
        assert pt.find("g:vdop", GPX_NS).text == "1.4"
        assert pt.find("g:pdop", GPX_NS).text == "1.7"
        speed = pt.find("g:extensions/tpx:TrackPointExtension/tpx:speed", GPX_NS)
        assert speed.text == "1.50"
        float(pt.find("g:ele", GPX_NS).text)

    def test_gpx_without_speed_has_no_extensions(self, tmp_path, make_gps_data):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(make_gps_data(speed=float("nan"), satellites_used=0))
        lg.stop()
        (pt,) = _gpx_points(path)
        tags = [child.tag.split("}")[1] for child in pt]
        assert "extensions" not in tags and "sat" not in tags


def test_start_twice_in_same_second_does_not_overwrite(tmp_path, make_gps_data, monkeypatch):
    """N7: a second log started in the same second gets its own file."""
    from tuigps import gps_logger

    class FrozenDatetime:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 3, 14, 12, 0, 0)

    monkeypatch.setattr(gps_logger, "datetime", FrozenDatetime)
    lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
    first = lg.start()
    lg.log_point(make_gps_data())
    lg.stop()
    second = lg.start()
    lg.stop()
    third = lg.start()
    lg.stop()
    assert first.name == "tuigps_20260314_120000.gpx"
    assert second.name == "tuigps_20260314_120000_1.gpx"
    assert third.name == "tuigps_20260314_120000_2.gpx"
    assert len(_gpx_points(first)) == 1  # first log was not overwritten


def test_log_point_after_stop_from_other_thread_is_safe(tmp_path, make_gps_data):
    """log_point (gpsd thread) and stop (UI thread) are serialized."""
    import threading

    lg = GPSLogger(directory=str(tmp_path), fmt="csv")
    lg.start()
    errors = []

    def writer():
        try:
            for i in range(500):
                lg.log_point(make_gps_data(time=f"t{i}"))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    t = threading.Thread(target=writer)
    t.start()
    lg.stop()
    t.join()
    assert errors == []


class TestCSV:
    HEADER = [
        "timestamp", "latitude", "longitude", "alt_msl", "speed", "track",
        "mode", "status", "hdop", "eph", "epv", "satellites_used",
    ]

    def test_header_and_row(self, tmp_path, make_gps_data):
        lg = GPSLogger(directory=str(tmp_path), fmt="csv")
        path = lg.start()
        assert path.suffix == ".csv"
        lg.log_point(make_gps_data())
        lg.stop()
        rows = list(csv.reader(path.open()))
        assert rows[0] == self.HEADER
        assert len(rows) == 2
        row = dict(zip(self.HEADER, rows[1]))
        assert row["timestamp"] == "2026-03-14T12:00:00.250Z"
        assert row["latitude"] == "47.60620000"
        assert row["longitude"] == "-122.33210000"
        assert row["alt_msl"] == "50.250000"
        assert row["speed"] == "1.500000"
        assert row["mode"] == "3"
        assert row["status"] == "2"
        assert row["hdop"] == "0.900000"
        assert row["satellites_used"] == "3"

    def test_nan_fields_are_empty(self, tmp_path):
        lg = GPSLogger(directory=str(tmp_path), fmt="csv")
        path = lg.start()
        lg.log_point(GPSData(mode=2, latitude=1.0, longitude=2.0, time="t1"))
        lg.stop()
        rows = list(csv.reader(path.open()))
        row = dict(zip(self.HEADER, rows[1]))
        for key in ("alt_msl", "speed", "track", "hdop", "eph", "epv"):
            assert row[key] == ""
        assert row["satellites_used"] == "0"

    def test_csv_footer_not_written(self, tmp_path, make_gps_data):
        lg = GPSLogger(directory=str(tmp_path), fmt="csv")
        path = lg.start()
        lg.log_point(make_gps_data())
        lg.stop()
        lg.stop()
        assert "</gpx>" not in path.read_text()
        assert len(path.read_text().splitlines()) == 2


# ─── Directory, format and lifecycle (U8) ───────────────────────────────


def test_start_creates_missing_directory(tmp_path):
    target = tmp_path / "a" / "b"
    lg = GPSLogger(directory=target, fmt="csv")
    path = lg.start()
    lg.stop()
    assert path.parent == target and path.suffix == ".csv"
    assert path.is_absolute()


def test_directory_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    lg = GPSLogger(directory="~/gpslogs")
    path = lg.start()
    lg.stop()
    assert path.parent == tmp_path / "gpslogs"


def test_relative_directory_gives_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lg = GPSLogger(directory="logs")
    path = lg.start()
    lg.stop()
    assert path == tmp_path / "logs" / path.name


def test_unknown_format_rejected(tmp_path):
    with pytest.raises(ValueError):
        GPSLogger(directory=tmp_path, fmt="kml")
    lg = GPSLogger(directory=tmp_path)
    with pytest.raises(ValueError):
        lg.configure(fmt="kml")
    assert lg.format == "gpx"


def test_configure_applies_to_next_start(tmp_path, make_gps_data):
    lg = GPSLogger(directory=tmp_path / "one", fmt="gpx")
    first = lg.start()
    lg.configure(directory=tmp_path / "two", fmt="csv")
    assert (lg.directory, lg.format) == (tmp_path / "two", "csv")
    lg.log_point(make_gps_data())  # the open file keeps its own format
    lg.stop()
    ET.parse(first)  # still a valid GPX file
    second = lg.start()
    lg.stop()
    assert second.parent == tmp_path / "two" and second.suffix == ".csv"


def test_start_while_active_finishes_previous_file(tmp_path, make_gps_data):
    lg = GPSLogger(directory=tmp_path, fmt="gpx")
    first = lg.start()
    lg.log_point(make_gps_data())
    old_file = lg._file
    second = lg.start()
    assert second != first and lg.is_active and lg.fix_count == 0
    assert old_file.closed  # no leaked file handle
    assert len(_gpx_points(first)) == 1  # finalized, valid GPX
    lg.stop()
    assert _gpx_points(second) == []


def test_start_failure_leaves_logger_inactive(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    lg = GPSLogger(directory=blocker / "sub")
    with pytest.raises(OSError):
        lg.start()
    assert not lg.is_active and lg._file is None


def test_start_failure_after_active_stops_previous(tmp_path, make_gps_data):
    lg = GPSLogger(directory=tmp_path)
    first = lg.start()
    lg.configure(directory=first)  # a file, not a directory
    with pytest.raises(OSError):
        lg.start()
    assert not lg.is_active
    ET.parse(first)  # the previous log was still finished properly


def test_header_write_failure_closes_file(tmp_path, monkeypatch):
    lg = GPSLogger(directory=tmp_path)

    def boom(self):
        raise OSError("disk full")

    monkeypatch.setattr(GPSLogger, "_write_header", boom)
    with pytest.raises(OSError, match="disk full"):
        lg.start()
    assert not lg.is_active and lg._file is None


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root can write anywhere")
def test_permission_error(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        lg = GPSLogger(directory=ro)
        with pytest.raises(PermissionError):
            lg.start()
        assert not lg.is_active
    finally:
        ro.chmod(0o700)
