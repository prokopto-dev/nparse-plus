"""GUI-thread benchmarks for the map canvas (moved here with the rest of the
suite in #132; the map is the heaviest widget in the app and the one that
coalesces its own renders, so it stays measured beside the bridge that feeds
it)."""

from __future__ import annotations

from datetime import datetime

import pytest
from tests.ui.test_maps_zfade import (  # reuse the synthetic-map harness
    make_canvas,
    synthetic_maps,  # noqa: F401 - pytest fixture
)

from nparseplus.parsers.maps.mapclasses import MapPoint

pytestmark = pytest.mark.benchmark


@pytest.mark.qt
@pytest.mark.benchmark(group="maps")
def test_bench_map_location_burst(qtbot, benchmark, synthetic_maps) -> None:  # noqa: F811
    """GUI-thread cost of 120 location fixes + one coalesced render."""
    canvas = make_canvas(qtbot, "fadezone")

    def burst() -> None:
        for i in range(120):
            canvas.add_player("__you__", datetime.now(), MapPoint(x=float(i), y=float(i), z=0.0))
        canvas.flush_pending_render()

    benchmark(burst)
