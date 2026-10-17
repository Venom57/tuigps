"""Tests for tuigps.gps_logger (GPX/CSV output)."""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET

import pytest

from tuigps.data_model import GPSData
from tuigps.gps_logger import GPSLogger

GPX_NS = {"g": "http://www.topografix.com/GPX/1/1"}


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

    def test_fix_without_timestamp_is_skipped(self, tmp_path):
        # Characterization: dedupe compares against the initial "" last-time,
        # so a fix with an empty timestamp is never logged.
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(GPSData(mode=2, latitude=1.0, longitude=2.0, time=""))
        lg.stop()
        assert lg.fix_count == 0
        assert _gpx_points(path) == []

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

    @pytest.mark.xfail(strict=True, reason="F6: <speed> is not a valid trkpt child in GPX 1.1")
    def test_gpx_1_1_has_no_speed_element(self, tmp_path, make_gps_data):
        lg = GPSLogger(directory=str(tmp_path), fmt="gpx")
        path = lg.start()
        lg.log_point(make_gps_data())
        lg.stop()
        (pt,) = _gpx_points(path)
        assert pt.find("g:speed", GPX_NS) is None


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
