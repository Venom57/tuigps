"""Pilot tests for the responsive dashboard layout (U5)."""

from __future__ import annotations

import pytest

from tests.test_app_smoke import _idle_gpsd_client  # noqa: F401 (autouse fixture)
from tuigps.app import DASHBOARD_ORDER, TuiGPS

FILL_WIDGETS = {"w-skyplot", "w-signal"}  # draw to their size; never "truncated"


def check_nothing_truncated(app: TuiGPS) -> None:
    """Every text panel is tall enough for its content, and all of it is scrollable into view."""
    grid = app.query_one("#dashboard-grid")
    scroll = app.query_one("#dashboard-scroll")
    top = scroll.content_region.y
    for panel in grid.children:
        inner = panel.content_region
        if panel.id not in FILL_WIDGETS:
            needed = panel.get_content_height(panel.region.size, app.size, inner.width)
            assert needed <= inner.height, f"{panel.id}: {needed} lines in {inner.height}"
        assert panel.region.bottom - top <= scroll.virtual_size.height, panel.id
        assert panel.region.right <= scroll.content_region.right, panel.id


async def show(app, pilot, make_gps_data, hold: bool = False):
    app._gps_data = make_gps_data()
    if hold:
        app._hold.start()
        for i in range(3):
            app._hold.add_fix(47.6062 + i * 1e-6, -122.3321, 50.0 + i)
    app._refresh_ui()
    await pilot.pause()
    await pilot.pause()


@pytest.mark.parametrize(
    "size, layout, columns, scrolls",
    [
        ((70, 30), "-narrow", 1, True),
        ((100, 40), "-medium", 2, True),
        ((130, 45), "-wide", 3, False),
        ((200, 60), "-wide", 3, False),
    ],
)
async def test_dashboard_layout_for_size(size, layout, columns, scrolls, make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=size) as pilot:
        await show(app, pilot, make_gps_data)
        grid = app.query_one("#dashboard-grid")
        assert grid.has_class(layout)
        assert not any(grid.has_class(c) for c in ("-narrow", "-medium", "-wide") if c != layout)
        assert grid.styles.grid_size_columns == columns
        assert [w.id for w in grid.children] == list(DASHBOARD_ORDER[layout])
        # Distinct column positions actually used by the panels
        assert len({w.region.x for w in grid.children}) == columns
        sky = app.query_one("#w-skyplot")
        assert sky.content_region.height >= 14  # a usable sky plot in every layout
        if layout == "-wide":
            assert sky.region.width > app.query_one("#w-signal").region.width  # spans 2
        check_nothing_truncated(app)
        scroll = app.query_one("#dashboard-scroll")
        assert (scroll.max_scroll_y > 0) == scrolls
        if size == (200, 60):
            assert sky.content_region.height >= 25  # the sky plot takes the spare height


async def test_dashboard_rows_grow_with_content(make_gps_data):
    """A taller panel (position hold) grows its row instead of being clipped."""
    app = TuiGPS()
    async with app.run_test(size=(130, 45)) as pilot:
        await show(app, pilot, make_gps_data, hold=True)
        assert "CEP95" in app.query_one("#w-position").render().plain
        check_nothing_truncated(app)


async def test_dashboard_relayouts_on_resize(make_gps_data):
    app = TuiGPS()
    async with app.run_test(size=(130, 45)) as pilot:
        await show(app, pilot, make_gps_data)
        grid = app.query_one("#dashboard-grid")
        for size, layout in [((90, 40), "-medium"), ((60, 30), "-narrow"),
                             ((119, 40), "-medium"), ((120, 40), "-wide")]:
            await pilot.resize_terminal(*size)
            await pilot.pause()
            await pilot.pause()
            assert grid.has_class(layout), size
            assert [w.id for w in grid.children] == list(DASHBOARD_ORDER[layout])
            check_nothing_truncated(app)
        # Panels are the same widgets (refresh still reaches them)
        app._gps_data = make_gps_data(latitude=10.5)
        app._refresh_ui()
        assert "10.500000" in app.query_one("#w-position").render().plain


async def test_resize_while_a_dialog_is_open(make_gps_data):
    """The dashboard follows a resize that happens behind a modal (settings, help)."""
    from tuigps.screens.help_screen import HelpScreen

    app = TuiGPS()
    async with app.run_test(size=(130, 45)) as pilot:
        await show(app, pilot, make_gps_data)
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.resize_terminal(70, 30)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        grid = app.query_one("#dashboard-grid")
        assert grid.has_class("-narrow")
        assert [w.id for w in grid.children] == list(DASHBOARD_ORDER["-narrow"])
