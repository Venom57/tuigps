"""Stale and disconnected data indication in the panels (U3)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_app_smoke import _idle_gpsd_client  # noqa: F401 (autouse fixture)
from tuigps import data_model
from tuigps.app import TAB_WIDGETS, TuiGPS
from tuigps.constants import STALE_AFTER
from tuigps.data_model import GPSData, staleness

PANEL_IDS = [wid for ids in TAB_WIDGETS.values() for wid in ids]
T0 = 1_800_000_000.0


@pytest.mark.parametrize(
    "data, now, expected",
    [
        (None, T0, "DISCONNECTED"),
        (GPSData(), T0, "DISCONNECTED"),
        (GPSData(connection_state="connecting"), T0, "CONNECTING"),
        (GPSData(connected=True, connection_state="connected"), T0, ""),  # waiting
        (GPSData(connected=True, connection_state="connected", last_seen=T0 - 3), T0, ""),
        (GPSData(connected=True, connection_state="connected", last_seen=T0 - STALE_AFTER), T0, ""),
        (GPSData(connected=True, connection_state="connected", last_seen=T0 - 23.7), T0,
         "STALE 23s"),
        # Old data from a lost connection is "disconnected", not merely stale
        (GPSData(connected=False, connection_state="disconnected", last_seen=T0 - 99), T0,
         "DISCONNECTED"),
    ],
)
def test_staleness(data, now, expected):
    assert staleness(data, now) == expected


def tags(app: TuiGPS) -> set[str]:
    """The distinct (stale class, title tag) states of every data panel."""
    states = set()
    for wid in PANEL_IDS:
        panel = app.query_one(f"#{wid}")
        title = str(panel.border_title or "")
        states.add((panel.has_class("stale"), title))
    assert len(states) == 1, f"panels disagree: {states}"  # consistent everywhere
    stale, title = states.pop()
    assert stale == bool(title)
    return title


async def test_stale_tag_transitions(make_gps_data, monkeypatch):
    clock = {"now": T0}
    monkeypatch.setattr(data_model, "time", SimpleNamespace(time=lambda: clock["now"]))
    app = TuiGPS()
    async with app.run_test(size=(130, 45)) as pilot:
        app._refresh_ui()
        assert tags(app) == "[DISCONNECTED]"  # nothing received yet

        app._gps_data = make_gps_data(last_seen=T0)  # connected, fresh
        app._refresh_ui()
        assert tags(app) == ""

        clock["now"] = T0 + 5  # still within STALE_AFTER: heartbeat changes nothing
        app._heartbeat()
        assert tags(app) == ""

        clock["now"] = T0 + 23  # no new data: the heartbeat marks the panels stale...
        app._heartbeat()
        assert tags(app) == "[STALE 23s]"
        clock["now"] = T0 + 24  # ...and counts up
        app._heartbeat()
        assert tags(app) == "[STALE 24s]"

        app._gps_data = make_gps_data(connected=False, connection_state="disconnected",
                                      last_seen=T0)
        app._refresh_ui()
        assert tags(app) == "[DISCONNECTED]"

        app._gps_data = make_gps_data(connected=False, connection_state="connecting",
                                      last_seen=T0)
        app._refresh_ui()
        assert tags(app) == "[CONNECTING]"

        app._gps_data = make_gps_data(last_seen=clock["now"])  # fresh data clears it
        app._refresh_ui()
        assert tags(app) == ""
        await pilot.pause()


async def test_stale_panels_are_dimmed(make_gps_data, monkeypatch):
    clock = {"now": T0}
    monkeypatch.setattr(data_model, "time", SimpleNamespace(time=lambda: clock["now"]))
    app = TuiGPS()
    async with app.run_test(size=(130, 45)) as pilot:
        app._gps_data = make_gps_data(last_seen=T0)
        app._refresh_ui()
        await pilot.pause()
        fix = app.query_one("#w-fix")
        assert fix.styles.text_opacity == 1.0
        clock["now"] = T0 + 60
        app._heartbeat()
        await pilot.pause()
        assert fix.styles.text_opacity < 1.0
        # Hidden tabs are tagged too, so switching to them shows the state at once
        assert app.query_one("#w-sattable").has_class("stale")
        assert app.query_one("#w-time-detail").has_class("stale")
