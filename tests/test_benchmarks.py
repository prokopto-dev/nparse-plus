"""Local-only perf benchmarks (excluded from the default run — see
pyproject addopts). Compare before/after with:

    QT_QPA_PLATFORM=offscreen uv run pytest -m benchmark --benchmark-only
"""

from __future__ import annotations

from datetime import datetime

import pytest
from tests.stress_log import raid_lines
from tests.ui.test_maps_zfade import (  # reuse the synthetic-map harness
    make_canvas,
    synthetic_maps,  # noqa: F401 - pytest fixture
)

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.parsers.maps.mapclasses import MapPoint

pytestmark = pytest.mark.benchmark


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_bench_pipeline_raid_replay(benchmark) -> None:
    """Worker-thread cost: full parser chain + handlers over a raid burst."""
    lines = raid_lines(200)

    def replay() -> None:
        backend = build_backend(Settings(), speaker=NullSpeaker())
        for raw in lines:
            backend.pipeline.process(raw)

    benchmark(replay)


@pytest.mark.qt
def test_bench_map_location_burst(qtbot, benchmark, synthetic_maps) -> None:  # noqa: F811
    """GUI-thread cost of 120 location fixes + one coalesced render."""
    canvas = make_canvas(qtbot, "fadezone")

    def burst() -> None:
        for i in range(120):
            canvas.add_player("__you__", datetime.now(), MapPoint(x=float(i), y=float(i), z=0.0))
        canvas.flush_pending_render()

    benchmark(burst)
