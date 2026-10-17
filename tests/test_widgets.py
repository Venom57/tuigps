"""Render tests for the display widgets, mounted in a minimal headless app."""

from __future__ import annotations

import time
import types
from collections import deque

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Button, RichLog, Static

from tuigps.data_model import GPSData, PPSData, SatelliteInfo
from tuigps.position_hold import HoldResult
from tuigps.widgets.connection_status import ConnectionStatus
from tuigps.widgets.constellation_panel import ConstellationPanel
from tuigps.widgets.device_panel import DevicePanel
from tuigps.widgets.error_panel import ErrorPanel
from tuigps.widgets.fix_panel import FixPanel
from tuigps.widgets.nmea_viewer import NMEAViewer, _sentence_type
from tuigps.widgets.position_panel import PositionPanel
from tuigps.widgets.satellite_table import SatelliteTable
from tuigps.widgets.signal_chart import SignalChart
from tuigps.widgets.sky_plot import SkyPlot
from tuigps.widgets.time_panel import TimePanel
from tuigps.widgets.velocity_panel import VelocityPanel


class Harness(App):
    """Mounts a single widget, optionally with a fixed height."""

    def __init__(self, widget, height: int | None = None):
        super().__init__()
        self.widget = widget
        self._height = height

    def compose(self) -> ComposeResult:
        yield self.widget

    def on_mount(self) -> None:
        if self._height is not None:
            self.widget.styles.height = self._height


def partial_data() -> GPSData:
    """Connected, receiving time but no position fix."""
    return GPSData(connected=True, last_seen=time.time(), mode=1, time="2026-03-14T12:00:00Z")


# (factory, title, {state: [expected substrings]})
WIDGETS = {
    "position": (
        PositionPanel,
        "Position",
        {
            "none": ["No data"],
            "empty": ["Lat:  ---", "Alt MSL: ---"],
            "partial": ["Lat:  ---"],
            "full": ["47.606200° N", "122.332100° W", "50.2 m", "-19.8 m", "Open in Google Maps"],
        },
    ),
    "fix": (
        FixPanel,
        "Fix Quality",
        {
            "none": ["No data"],
            "empty": ["Unknown", "HDOP:   ---"],
            "partial": ["No Fix"],
            "full": ["3D Fix", "DGPS", "3 used / 7 vis", "0.9 (Ideal)", "2.1 (Good)"],
        },
    ),
    "velocity": (
        VelocityPanel,
        "Velocity",
        {
            "none": ["No data"],
            "empty": ["Speed:  ---", "Track:  ---"],
            "partial": ["Speed:  ---"],
            "full": ["5.4 km/h", "271.0° (W)", "255.0°", "0.25 m/s", "15.3° W"],
        },
    ),
    "skyplot": (SkyPlot, "", {"none": ["+", "N"], "empty": ["+"], "partial": ["+"], "full": ["#", "o"]}),
    "signal": (
        SignalChart,
        "Signal Strength",
        {
            "none": ["No satellites"],
            "empty": ["No satellites"],
            "partial": ["No satellites"],
            "full": ["•GP05", "GA11", " 42", "=Used"],
        },
    ),
    "errors": (
        ErrorPanel,
        "Error Estimates",
        {
            "none": ["No data"],
            "empty": ["EPH: ---"],
            "partial": ["EPH: ---"],
            "full": ["±3.1 m", "±0.0050 s", "±12.0°"],
        },
    ),
    "device": (
        DevicePanel,
        "Device",
        {
            "none": ["No device"],
            "empty": ["No device"],
            "partial": ["No device"],
            "full": ["/dev/ttyACM0", "u-blox", "9600", "1.0s", "3.25"],
        },
    ),
    "time": (
        TimePanel,
        "Time & Timing",
        {
            "none": ["No data"],
            "empty": ["Date:    ---"],
            "partial": ["2026-03-14", "12:00:00 UTC"],
            "full": ["2026-03-14", "12:00:00.250 UTC", "5.0 ms", "Leap:    18 s"],
        },
    ),
    "time_pps": (
        lambda: TimePanel(show_pps=True),
        "Time & Timing",
        {
            "none": ["No data"],
            "empty": ["No PPS data", "No TOFF data"],
            "partial": ["No PPS data"],
            "full": ["-1.500 us", "Good", "953.7 ns", "12.5 ns", "-12.000 ms", "(4 samples)", "TDOP:    0.8"],
        },
    ),
    "constellations": (
        ConstellationPanel,
        "Constellations",
        {
            "none": ["No satellites in view"],
            "empty": ["No satellites in view"],
            "partial": ["No satellites in view"],
            "full": ["GPS", " 2/3", "Galileo", "GLONASS", "Total:", "3 used / 7 visible"],
        },
    ),
    "sattable": (
        SatelliteTable,
        "Satellite Details",
        {
            "none": ["No satellites in view"],
            "empty": ["No satellites in view"],
            "partial": ["No satellites in view"],
            "full": ["GNSS", "PRN", "OK", "GL"],
        },
    ),
    "status": (
        ConnectionStatus,
        " q ",
        {
            "none": ["DISCONNECTED"],
            "empty": ["DISCONNECTED"],
            "partial": ["No Fix"],
            "full": ["2GP+1GA/7sv", "/dev/ttyACM0", "3D Fix", "DGPS"],
        },
    ),
}


@pytest.mark.parametrize("name", list(WIDGETS))
async def test_widget_renders(name, make_gps_data):
    """Render each widget with no data, empty, partial and full GPSData."""
    factory, title, expectations = WIDGETS[name]
    widget = factory()
    app = Harness(widget, height=30 if name in ("skyplot", "signal") else None)
    data_for = {"empty": GPSData, "partial": partial_data, "full": make_gps_data}
    async with app.run_test(size=(120, 40)) as pilot:
        for state in ("none", "empty", "partial", "full"):
            if state != "none":
                widget.update_gps_data(data_for[state]())
            await pilot.pause()
            rendered = widget.render()
            assert isinstance(rendered, Text)
            plain = rendered.plain
            if title:
                assert title in plain
            for expected in expectations[state]:
                assert expected in plain, f"[{state}] {expected!r} not in {plain!r}"


async def test_units_and_coord_format(make_gps_data):
    pos = PositionPanel(coord_format="dms", units="imperial")
    app = Harness(pos)
    async with app.run_test():
        pos.update_gps_data(make_gps_data())
        plain = pos.render().plain
        assert "47°36'22.32\"N" in plain
        assert "164.9 ft" in plain
        assert "Geoid:   -64.8 ft" in plain  # F10: geoid separation follows the units


async def test_velocity_units(make_gps_data):
    vel = VelocityPanel(units="nautical")
    app = Harness(vel)
    async with app.run_test():
        vel.update_gps_data(make_gps_data(magvar=3.0, track=float("nan")))
        plain = vel.render().plain
        assert "2.9 kn" in plain
        assert "3.0° E" in plain
        assert "Track:  ---" in plain


async def test_position_hold_section(make_gps_data):
    pos = PositionPanel()
    app = Harness(pos)
    async with app.run_test():
        pos.update_gps_data(make_gps_data())
        pos.set_hold_data(HoldResult(mean_lat=47.0, mean_lon=-122.0, mean_alt=10.0, std_north=0.5,
                                     std_east=0.7, std_alt=1.2, cep50=0.71, cep95=1.52,
                                     fix_count=12, duration=75.0))
        plain = pos.render().plain
        assert "Hold (12 fixes, 1:15)" in plain
        assert "CEP50:   0.710 m" in plain
        assert "Std Alt: 1.200 m" in plain
        pos.set_hold_data(HoldResult(mean_lat=47.0, mean_lon=-122.0, fix_count=1))
        plain = pos.render().plain
        assert "Hold (1 fixes" in plain and "CEP50" not in plain


async def test_device_panel_version_only():
    dev = DevicePanel()
    app = Harness(dev)
    async with app.run_test():
        d = GPSData()
        d.version.release = "3.25"
        d.version.proto_major, d.version.proto_minor = 3, 15
        dev.update_gps_data(d)
        assert "gpsd 3.25 (proto 3.15)" in dev.render().plain


@pytest.mark.parametrize(
    "clock_nsec, quality",
    [(0.5, "Excellent"), (5000.0, "Good"), (50_000.0, "Fair"), (5_000_000.0, "Poor")],
)
async def test_time_panel_pps_quality(clock_nsec, quality):
    tp = TimePanel(show_pps=True)
    app = Harness(tp)
    async with app.run_test():
        tp.update_gps_data(GPSData(pps=PPSData(real_sec=10.0, real_nsec=0.0, clock_sec=10.0,
                                               clock_nsec=clock_nsec)))
        assert f"Quality: {quality}" in tp.render().plain


async def test_time_panel_armed_and_single_sample(make_gps_data):
    tp = TimePanel(show_pps=True)
    app = Harness(tp)
    async with app.run_test():
        d = make_gps_data(toff_armed_offset=0.0042, toff_armed_gps_time="2026-03-14T12:00:00Z",
                          toff_armed_sys_time=1773489600.5, toff_samples=[0.001])
        tp.update_gps_data(d)
        plain = tp.render().plain
        assert "Armed Measurement" in plain
        assert "+4.200 ms" in plain
        assert "2026-03-14T12:00:00.500Z" in plain
        assert "1 sample" in plain


def test_time_panel_fmt_offset():
    assert TimePanel._fmt_offset(5e-10) == "+0.5 ns"
    assert TimePanel._fmt_offset(-2.5e-6) == "-2.500 us"
    assert TimePanel._fmt_offset(0.0123) == "+12.300 ms"


async def test_connection_status_badges_and_stale(make_gps_data):
    bar = ConnectionStatus()
    app = Harness(bar)
    async with app.run_test(size=(200, 10)):
        bar.logging_active, bar.log_count = True, 7
        bar.hold_active, bar.hold_count = True, 3
        bar.update_gps_data(make_gps_data(last_seen=time.time() - 30))
        plain = bar.render().plain
        assert " REC " in plain and " 7 " in plain
        assert " HOLD " in plain and " 3 " in plain
        assert "STALE (30s)" in plain or "STALE (29s)" in plain or "STALE (31s)" in plain


async def test_connection_status_connected_never_seen():
    """N7: connected but no message yet renders as WAITING, not STALE (999s)."""
    bar = ConnectionStatus()
    app = Harness(bar)
    async with app.run_test(size=(200, 10)):
        bar.update_gps_data(GPSData(connected=True))
        plain = bar.render().plain
        assert "WAITING" in plain
        assert "STALE" not in plain


async def test_connection_status_connecting_and_retry_countdown():
    bar = ConnectionStatus()
    app = Harness(bar)
    async with app.run_test(size=(200, 10)):
        bar.update_gps_data(GPSData(connection_state="connecting"))
        assert "CONNECTING\u2026" in bar.render().plain
        bar.update_gps_data(GPSData(connection_state="disconnected",
                                    next_retry_at=time.time() + 7.5, retry_delay=8.0))
        assert "DISCONNECTED \u2014 retry in 8s" in bar.render().plain
        bar.update_gps_data(GPSData(connection_state="disconnected",
                                    next_retry_at=time.time() - 1))
        assert "retry in 0s" in bar.render().plain
        bar.update_gps_data(GPSData(connection_state="disconnected"))
        plain = bar.render().plain
        assert "DISCONNECTED" in plain and "retry" not in plain


async def test_sky_plot_skips_below_horizon_and_nan():
    sky = SkyPlot()
    app = Harness(sky, height=20)
    async with app.run_test(size=(60, 24)):
        sats = [
            SatelliteInfo(svid=77, gnssid=0, elevation=-10.0, azimuth=90.0, used=True),
            SatelliteInfo(svid=88, gnssid=0),
        ]
        sky.update_gps_data(GPSData(satellites=sats))
        plain = sky.render().plain
        assert "#" not in plain and "77" not in plain and "88" not in plain


async def test_signal_chart_truncates_when_too_many():
    chart = SignalChart()
    app = Harness(chart, height=8)
    async with app.run_test(size=(80, 20)):
        sats = [SatelliteInfo(gnssid=0, svid=i, snr=40.0 - i, used=True) for i in range(1, 11)]
        chart.update_gps_data(GPSData(satellites=sats))
        plain = chart.render().plain
        assert "GP01" in plain
        assert "... +" in plain


async def test_signal_chart_budget_ignores_skipped_satellites():
    """F13: satellites without a signal are not rendered and use no rows."""
    chart = SignalChart()
    app = Harness(chart, height=8)
    async with app.run_test(size=(80, 20)):
        # Used satellites with no SNR sort first and are skipped, but consume budget
        sats = [SatelliteInfo(gnssid=0, svid=i, snr=0.0, used=True) for i in range(1, 6)]
        sats += [SatelliteInfo(gnssid=0, svid=20, snr=40.0), SatelliteInfo(gnssid=0, svid=21, snr=35.0)]
        chart.update_gps_data(GPSData(satellites=sats))
        plain = chart.render().plain
        assert "GP20" in plain and "GP21" in plain
        assert "more" not in plain


async def test_signal_chart_more_counts_only_drawable_satellites():
    chart = SignalChart()
    app = Harness(chart, height=8)
    async with app.run_test(size=(80, 20)):
        sats = [SatelliteInfo(gnssid=0, svid=i, snr=40.0 - i, used=True) for i in range(1, 11)]
        sats += [SatelliteInfo(gnssid=0, svid=50 + i, snr=float("nan")) for i in range(5)]
        chart.update_gps_data(GPSData(satellites=sats))
        plain = chart.render().plain
        rows = sum(1 for line in plain.splitlines() if line.startswith("\u2022GP"))
        assert f"... +{10 - rows} more" in plain


# ─── NMEA viewer ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sentence, expected",
    [("$GPGGA,1,2,3", "GGA"), ("$GNRMC,x", "RMC"), ("GPGGA,", ""), ("$GP", ""), ("", "")],
)
def test_sentence_type(sentence, expected):
    assert _sentence_type(sentence) == expected


SENTENCES = ["$GPGGA,a*00", "$GPRMC,b*00", "$GPGSV,c*00"]


def _log_lines(log) -> list[str]:
    return ["".join(seg.text for seg in strip).rstrip() for strip in log.lines]


async def _press(pilot, viewer, btn_id):
    viewer.query_one(f"#{btn_id}", Button).press()
    await pilot.pause()


async def test_nmea_viewer_append_filter_clear():
    viewer = NMEAViewer()
    app = Harness(viewer)
    async with app.run_test(size=(120, 30)) as pilot:
        log = viewer.query_one("#nmea-log", RichLog)
        for s in SENTENCES:
            viewer.append_nmea(s)
        await pilot.pause()
        assert len(log.lines) == 3

        # F7: changing the filter re-filters the existing lines
        await _press(pilot, viewer, "btn-nmea-gga")
        assert viewer._filter == "GGA"
        assert viewer.query_one("#btn-nmea-gga", Button).variant == "primary"
        assert viewer.query_one("#btn-nmea-all", Button).variant == "default"
        assert _log_lines(log) == ["$GPGGA,a*00"]
        viewer.append_nmea("$GPRMC,d*00")
        viewer.append_nmea("$GPGGA,e*00")
        await pilot.pause()
        assert _log_lines(log) == ["$GPGGA,a*00", "$GPGGA,e*00"]
        assert "GGA: 2" in viewer.stats_text()

        await _press(pilot, viewer, "btn-nmea-all")
        assert viewer._filter == ""
        assert _log_lines(log) == SENTENCES + ["$GPRMC,d*00", "$GPGGA,e*00"]

        await _press(pilot, viewer, "btn-nmea-clear")
        assert len(log.lines) == 0
        assert len(viewer._buffer) == 0
        assert viewer.stats_text().startswith("0 sentences")


async def test_nmea_viewer_pause_buffers():
    viewer = NMEAViewer()
    app = Harness(viewer)
    async with app.run_test(size=(120, 30)) as pilot:
        log = viewer.query_one("#nmea-log", RichLog)
        await _press(pilot, viewer, "btn-nmea-pause")
        btn = viewer.query_one("#btn-nmea-pause", Button)
        assert viewer._paused and str(btn.label) == "Resume"
        viewer.append_nmea(SENTENCES[0])
        await pilot.pause()
        assert len(log.lines) == 0
        await _press(pilot, viewer, "btn-nmea-pause")
        assert not viewer._paused and str(btn.label) == "Pause"
        assert len(log.lines) == 1


async def test_nmea_viewer_resume_does_not_duplicate():
    """F7: resume writes only the sentences received while paused."""
    viewer = NMEAViewer()
    app = Harness(viewer)
    async with app.run_test(size=(120, 30)) as pilot:
        log = viewer.query_one("#nmea-log", RichLog)
        for s in SENTENCES:
            viewer.append_nmea(s)
        await _press(pilot, viewer, "btn-nmea-pause")
        viewer.append_nmea("$GPVTG,z*00")
        assert "PAUSED (1 buffered)" in viewer.stats_text()
        await _press(pilot, viewer, "btn-nmea-pause")
        assert _log_lines(log) == SENTENCES + ["$GPVTG,z*00"]
        assert "PAUSED" not in viewer.stats_text()


async def test_nmea_viewer_filter_change_while_paused():
    """Re-filtering while paused shows only already-shown sentences; resume adds the rest once."""
    viewer = NMEAViewer()
    app = Harness(viewer)
    async with app.run_test(size=(120, 30)) as pilot:
        log = viewer.query_one("#nmea-log", RichLog)
        for s in SENTENCES:
            viewer.append_nmea(s)
        await _press(pilot, viewer, "btn-nmea-pause")
        viewer.append_nmea("$GPGGA,p*00")
        await _press(pilot, viewer, "btn-nmea-gga")
        assert _log_lines(log) == ["$GPGGA,a*00"]
        await _press(pilot, viewer, "btn-nmea-pause")
        assert _log_lines(log) == ["$GPGGA,a*00", "$GPGGA,p*00"]


async def test_nmea_viewer_pause_overflow_keeps_latest():
    viewer = NMEAViewer()
    viewer._buffer = deque(maxlen=5)
    app = Harness(viewer)
    async with app.run_test(size=(120, 30)) as pilot:
        log = viewer.query_one("#nmea-log", RichLog)
        viewer.append_nmea("$GPGGA,0*00")
        await _press(pilot, viewer, "btn-nmea-pause")
        for i in range(1, 9):
            viewer.append_nmea(f"$GPGGA,{i}*00")
        assert viewer._pending == 5
        await _press(pilot, viewer, "btn-nmea-pause")
        assert _log_lines(log) == ["$GPGGA,0*00"] + [f"$GPGGA,{i}*00" for i in range(4, 9)]


async def test_nmea_viewer_stats(monkeypatch):
    """F7: #nmea-stats shows the total and the per-second rate."""
    from tuigps.widgets import nmea_viewer

    now = {"t": 100.0}
    monkeypatch.setattr(nmea_viewer, "time", types.SimpleNamespace(monotonic=lambda: now["t"]))
    viewer = NMEAViewer()
    app = Harness(viewer)
    async with app.run_test(size=(160, 30)) as pilot:
        for i in range(10):
            now["t"] = 100.0 + i * 0.5
            viewer.append_nmea(SENTENCES[i % 3])
        await pilot.pause()
        stats = viewer.query_one("#nmea-stats", Static)
        assert str(stats.render()) == "10 sentences  2.0/s"
        now["t"] = 200.0
        assert viewer.stats_text() == "10 sentences  0.0/s"


async def test_nmea_viewer_batch_matches_single_appends():
    """P5: a batch gives the same log, counts and stats as one-by-one appends."""
    batch = ["$GPGGA,1*00", "$GPRMC,2*00", "$GPGSV,3*00", "$GPGGA,4*00", "bogus"]
    single, batched = NMEAViewer(id="single"), NMEAViewer(id="batched")

    class Both(App):
        def compose(self):
            yield single
            yield batched

    async with Both().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        for s in batch:
            single.append_nmea(s)
        batched.append_nmea_batch(batch)
        batched.append_nmea_batch([])  # no-op
        await pilot.pause()
        for v in (single, batched):
            assert _log_lines(v.query_one("#nmea-log", RichLog)) == batch
        assert batched._total == single._total == 5
        assert batched._type_counts == single._type_counts
        assert list(batched._buffer) == list(single._buffer)
        assert batched.stats_text().split("  ")[0] == "5 sentences"

        # Filtered and paused batches
        await _press(pilot, batched, "btn-nmea-gga")
        assert _log_lines(batched.query_one("#nmea-log", RichLog)) == ["$GPGGA,1*00", "$GPGGA,4*00"]
        batched.append_nmea_batch(["$GPRMC,5*00", "$GPGGA,6*00"])
        await pilot.pause()
        assert _log_lines(batched.query_one("#nmea-log", RichLog))[-1] == "$GPGGA,6*00"
        await _press(pilot, batched, "btn-nmea-pause")
        batched.append_nmea_batch(["$GPGGA,7*00", "$GPRMC,8*00", "$GPGGA,9*00"])
        assert batched._pending == 3
        assert "PAUSED (3 buffered)" in batched.stats_text()
        await _press(pilot, batched, "btn-nmea-pause")
        assert _log_lines(batched.query_one("#nmea-log", RichLog))[-2:] == ["$GPGGA,7*00", "$GPGGA,9*00"]
        assert batched._type_counts["GGA"] == 5 and batched._total == 10


# ─── Constellation display filter (F8) ──────────────────────────────────


async def test_sky_plot_hides_filtered_constellations(make_gps_data):
    sky = SkyPlot()
    app = Harness(sky, height=24)
    async with app.run_test(size=(80, 30)):
        sky.update_gps_data(make_gps_data())
        before = sky.render().plain
        assert before.count("#") == 3  # used: GP05, GP12, GA11
        assert before.count("o") == 2  # unused: GP25, GL08
        sky.hidden_gnss = frozenset({2, 6})
        after = sky.render().plain
        assert after.count("#") == 2 and after.count("o") == 1


async def test_signal_chart_all_hidden(make_gps_data):
    chart = SignalChart()
    app = Harness(chart, height=20)
    async with app.run_test(size=(80, 30)):
        chart.update_gps_data(make_gps_data())
        chart.hidden_gnss = frozenset(range(8))
        plain = chart.render().plain
        assert "No satellites" in plain and "(7 hidden by filter)" in plain
        chart.hidden_gnss = frozenset({0})
        plain = chart.render().plain
        assert "GP05" not in plain and "GA11" in plain
        assert "(3 hidden by filter)" in plain
        assert "GP " not in plain.split("=Used")[1]  # legend lists shown constellations only


async def test_filter_change_repaints_without_new_data(make_gps_data, monkeypatch):
    widgets = [SkyPlot(), SignalChart(), SatelliteTable(), ConstellationPanel()]

    class All(App):
        def compose(self):
            yield from widgets

    async with All().run_test(size=(100, 80)) as pilot:
        data = make_gps_data()
        for w in widgets:
            w.update_gps_data(data)
        await pilot.pause()
        repaints = {id(w): 0 for w in widgets}
        for w in widgets:
            monkeypatch.setattr(w, "refresh", lambda *a, _w=w, **k: repaints.__setitem__(id(_w), repaints[id(_w)] + 1))
            w.hidden_gnss = frozenset()  # unchanged: no repaint
        assert set(repaints.values()) == {0}
        for w in widgets:
            w.hidden_gnss = frozenset({6})
        assert all(n >= 1 for n in repaints.values())


async def test_satellite_table_and_constellation_panel_filter(make_gps_data):
    table, panel = SatelliteTable(), ConstellationPanel()

    class Both(App):
        def compose(self):
            yield panel
            yield table

    async with Both().run_test(size=(100, 60)):
        data = make_gps_data()
        table.update_gps_data(data)
        panel.update_gps_data(data)
        table.hidden_gnss = panel.hidden_gnss = frozenset({1})
        t = table.render().plain
        assert "SB " not in t and "1 satellite hidden" in t
        p = panel.render().plain
        assert "SBAS      0/1  (hidden)" in p
        assert "GPS       2/3" in p and "(hidden)" not in p.split("SBAS")[0]


# ─── Unit system in every widget (F10) ──────────────────────────────────


def _hold_result():
    return HoldResult(mean_lat=47.0, mean_lon=-122.0, mean_alt=10.0, std_north=0.5,
                      std_east=0.7, std_alt=1.2, cep50=0.71, cep95=1.52,
                      fix_count=12, duration=75.0)


UNIT_EXPECTATIONS = {
    "metric": {
        "position": ["Alt MSL: 50.2 m", "Geoid:   -19.8 m", "Avg Alt: 10.0 m",
                     "Std N/S: 0.500 m", "Std Alt: 1.200 m", "CEP50:   0.710 m", "CEP95:   1.520 m"],
        "velocity": ["5.4 km/h", "Climb:  0.25 m/s"],
        "errors": ["EPH: ±3.1 m", "EPV: ±4.2 m", "EPS: ±2.16 km/h", "EPC: ±0.80 m/s",
                   "EPX: ±2.1 m", "EPY: ±2.2 m", "SEP: ±5.3 m", "EPT: ±0.0050 s", "EPD: ±12.0°"],
    },
    "imperial": {
        "position": ["Alt MSL: 164.9 ft", "Geoid:   -64.8 ft", "Avg Alt: 32.8 ft",
                     "Std N/S: 1.640 ft", "Std Alt: 3.937 ft", "CEP50:   2.329 ft", "CEP95:   4.987 ft"],
        "velocity": ["3.4 mph", "Climb:  49 ft/min"],
        "errors": ["EPH: ±10.2 ft", "EPV: ±13.8 ft", "EPS: ±1.34 mph", "EPC: ±157 ft/min",
                   "EPX: ±6.9 ft", "EPY: ±7.2 ft", "SEP: ±17.4 ft", "EPT: ±0.0050 s"],
    },
    "nautical": {
        "position": ["Alt MSL: 164.9 ft", "Geoid:   -64.8 ft", "CEP50:   2.329 ft"],
        "velocity": ["2.9 kn", "Climb:  49 ft/min"],
        "errors": ["EPH: ±10.2 ft", "EPS: ±1.17 kn", "EPC: ±157 ft/min", "SEP: ±17.4 ft"],
    },
}


@pytest.mark.parametrize("units", list(UNIT_EXPECTATIONS))
async def test_every_unit_dependent_widget_follows_units(units, make_gps_data):
    pos, vel, err = PositionPanel(), VelocityPanel(), ErrorPanel()

    class All(App):
        def compose(self):
            yield pos
            yield vel
            yield err

    async with All().run_test(size=(100, 80)) as pilot:
        data = make_gps_data()
        for w in (pos, vel, err):
            w.update_gps_data(data)
        pos.set_hold_data(_hold_result())
        for w in (pos, vel, err):
            w.units = units  # reactive: repaints without new data
        await pilot.pause()
        rendered = {"position": pos.render().plain, "velocity": vel.render().plain,
                    "errors": err.render().plain}
        for name, expected in UNIT_EXPECTATIONS[units].items():
            for text in expected:
                assert text in rendered[name], f"[{units}/{name}] {text!r} not in {rendered[name]!r}"
        if units != "metric":
            for name, plain in rendered.items():
                assert " m\n" not in plain and "m/s" not in plain, (name, plain)


async def test_error_panel_hides_missing_optional_estimates():
    err = ErrorPanel(units="imperial")
    app = Harness(err)
    async with app.run_test():
        err.update_gps_data(GPSData())
        plain = err.render().plain
        assert "EPH: ---" in plain and "EPC: ---" in plain
        assert "EPX" not in plain and "SEP" not in plain
