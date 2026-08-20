"""The Phase 0 benchmark suite (#132, epic #131).

Local-only by default — ``pyproject`` deselects the ``benchmark`` marker,
because CI timing is far too noisy to gate a PR on. Run them with::

    QT_QPA_PLATFORM=offscreen uv run pytest -m benchmark --benchmark-only

and, to feed the nightly dashboard::

    QT_QPA_PLATFORM=offscreen uv run pytest -m benchmark --benchmark-only \
        --benchmark-json=perf.json
    uv run python tools/perf_report.py record perf.json ...

What each group answers, and why it is here rather than a profiler run:

``bus``
    ``EventBus.publish`` at 0/1/10/50 subscribers. The floor for everything
    else — every parser that matches ends in a publish, and #131 §4.7 wants
    to know what the defensive ``list()`` snapshot costs before replacing it
    with copy-on-write. Measured with no plugin machinery at all.

``pipeline``
    ``LogPipeline.process`` over the three traffic profiles plus the
    EQtoolsTests capture, through a fully wired ``build_backend`` — the
    parser chain AND every handler subscribed to it, which is what the
    driver thread actually runs per line.

``plugin-dispatch``
    The same publish at 1/10/50 *plugin* subscribers, registered through
    ``HostPluginContext`` so the guard wrapper and the telemetry gate are in
    the measurement. Run with collection on and off, which is what turns
    "gate it if it costs anything" into a number.

``plugin-parser``
    A plugin parser at the end of the chain, ditto.

``qt-bridge``
    ``QtEventBridge`` under a raid burst: the buffer-and-coalesce path from
    the driver thread plus the GUI-thread flush that re-emits it.

``latency``
    Log append -> parsed domain event -> UI-visible update, end to end,
    through a real ``LogDriver`` tailing a real file. The one number here
    that a user would recognise.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.perf.profiles import corpus_lines, profile

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.driver import LogDriver
from nparseplus.core.events import AfterPlayerChangedEvent, DamageEvent, LineEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.plugins.context import HostPluginContext, _OwnedNet
from nparseplus.core.plugins.storage import JsonPluginStorage
from nparseplus.core.plugins.telemetry import MetricsCollector
from nparseplus_sdk import PluginMeta

pytestmark = pytest.mark.benchmark

# One second of raid combat, to hand the bus something with the shape of a
# real event rather than a bare object().
SAMPLE_EVENT = DamageEvent(
    timestamp=datetime(2026, 7, 15, 21, 0, 0),
    line="Whitewitch slices Lord Nagafen for 91 points of damage.",
    line_number=1,
    target_name="Lord Nagafen",
    attacker_name="Whitewitch",
    damage_done=91,
    damage_type="slices",
)

SUBSCRIBER_COUNTS = (0, 1, 10, 50)
PLUGIN_SUBSCRIBER_COUNTS = (1, 10, 50)
PROFILE_NAMES = ("solo", "group", "raid")


def _sink(state: list[int]):
    """A subscriber that does the least a real one can do: touch the event."""

    def handle(event: object) -> None:
        state[0] += 1

    return handle


# --- bus --------------------------------------------------------------------
@pytest.mark.benchmark(group="bus")
@pytest.mark.parametrize("subscribers", SUBSCRIBER_COUNTS)
def test_bench_bus_publish(benchmark, subscribers: int) -> None:
    bus = EventBus()
    state = [0]
    for _ in range(subscribers):
        bus.subscribe(DamageEvent, _sink(state))
    benchmark(bus.publish, SAMPLE_EVENT)


@pytest.mark.benchmark(group="bus")
def test_bench_bus_publish_to_firehose(benchmark) -> None:
    """One ``subscribe_all`` — what the Qt bridge and console cost the bus."""
    bus = EventBus()
    state = [0]
    bus.subscribe_all(_sink(state))
    benchmark(bus.publish, SAMPLE_EVENT)


# --- pipeline ---------------------------------------------------------------
def _replay(backend, lines: list[str]) -> None:
    for raw in lines:
        backend.pipeline.process(raw)


def _fresh_backend_setup():
    """``pedantic`` setup: a new backend per round, outside the timed section.

    Not an optimisation — a correctness requirement. Replaying the same
    burst into one backend accumulates fights, timer rows and DPS history
    round after round, so the measured cost grows with the round count and
    two runs that happened to settle on different round counts are not
    comparable. A fresh backend makes every round measure the same work.
    """
    return (build_backend(Settings(), speaker=NullSpeaker()),), {}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.benchmark(group="pipeline")
@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_bench_pipeline_profile(benchmark, name: str) -> None:
    """60 in-game seconds of each profile through the whole backend."""
    lines = profile(name, 60)
    benchmark.extra_info["lines"] = len(lines)
    benchmark.pedantic(
        lambda backend: _replay(backend, lines),
        setup=_fresh_backend_setup,
        rounds=20,
        iterations=1,
    )


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.benchmark(group="pipeline")
def test_bench_pipeline_corpus(benchmark) -> None:
    """The EQtoolsTests capture — real traffic nobody arranged."""
    lines = corpus_lines()
    benchmark.extra_info["lines"] = len(lines)
    benchmark.pedantic(
        lambda backend: _replay(backend, lines),
        setup=_fresh_backend_setup,
        rounds=20,
        iterations=1,
    )


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.benchmark(group="pipeline")
def test_bench_pipeline_cold_backend(benchmark) -> None:
    """Backend construction + a raid burst, the shape #131's note assumes.

    Kept from the pre-#132 suite because it is the only benchmark that
    includes ``build_backend``, and a regression there is a launch-time
    regression nothing else would catch.
    """
    lines = profile("raid", 30)

    def replay() -> None:
        backend = build_backend(Settings(), speaker=NullSpeaker())
        _replay(backend, lines)

    # No ``lines`` extra_info on purpose: most of this number is
    # build_backend, so a per-line figure derived from it would be a lie.
    benchmark.pedantic(replay, rounds=20, iterations=1)


# --- plugin dispatch --------------------------------------------------------
class _CountingParser:
    """A plugin parser that never consumes — worst case for the chain."""

    def __init__(self) -> None:
        self.seen = 0

    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        self.seen += 1
        return False


def _plugin_ctx(backend, tmp_path: Path, *, collecting: bool) -> HostPluginContext:
    meta = PluginMeta(id="bench-plugin", name="Bench Plugin")
    collector = MetricsCollector(enabled=collecting)
    return HostPluginContext(
        meta,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / meta.id),
        _OwnedNet(backend),
        metrics=collector.for_plugin(meta.id),
    )


@pytest.mark.benchmark(group="plugin-dispatch")
@pytest.mark.parametrize("collecting", [False, True], ids=["off", "on"])
@pytest.mark.parametrize("subscribers", PLUGIN_SUBSCRIBER_COUNTS)
def test_bench_plugin_dispatch(benchmark, tmp_path, subscribers: int, collecting: bool) -> None:
    """N plugin handlers on one event type, with collection off and on.

    The delta between the two ids is the whole cost of #132's measurement on
    the dispatch path; compare against ``bus`` at the same subscriber count
    for what the guard wrapper costs on top of a bare subscription.

    The bus is a bare one, not the backend's, and ``subscribe`` is the only
    capability used — so the number is plugin dispatch and nothing else. On
    the backend's bus a ``DamageEvent`` also runs the fight tracker, whose
    per-round state growth would swamp the microseconds being measured.
    """
    backend = SimpleNamespace(bus=EventBus())
    ctx = _plugin_ctx(backend, tmp_path, collecting=collecting)
    state = [0]
    for _ in range(subscribers):
        ctx.subscribe(DamageEvent, _sink(state))
    benchmark(backend.bus.publish, SAMPLE_EVENT)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.benchmark(group="plugin-parser")
@pytest.mark.parametrize("collecting", [False, True], ids=["off", "on"])
def test_bench_plugin_parser(benchmark, tmp_path, collecting: bool) -> None:
    """One plugin parser at the end of the chain, over a raid burst."""
    lines = profile("raid", 30)

    def setup():
        backend = build_backend(Settings(), speaker=NullSpeaker())
        ctx = _plugin_ctx(backend, tmp_path, collecting=collecting)
        ctx.add_parser(_CountingParser())
        return (backend,), {}

    benchmark.extra_info["lines"] = len(lines)
    benchmark.pedantic(
        lambda backend: _replay(backend, lines), setup=setup, rounds=20, iterations=1
    )


# --- Qt bridge --------------------------------------------------------------
@pytest.mark.qt
@pytest.mark.benchmark(group="qt-bridge")
def test_bench_qt_bridge_burst(qtbot, benchmark) -> None:
    """Buffer 1000 events off-thread, then one coalesced GUI-thread flush.

    ``event_received`` has a real slot connected, because the bridge re-emits
    per event after the batch signal and a bridge nobody listens to would
    measure the buffer alone.
    """
    from nparseplus.ui.qtbridge import QtEventBridge

    bus = EventBus()
    bridge = QtEventBridge(bus)
    qtbot.addWidget  # noqa: B018 - keeps the qtbot fixture obviously in play
    state = [0]
    bridge.event_received.connect(lambda _event: state.__setitem__(0, state[0] + 1))
    events = [SAMPLE_EVENT] * 1000

    def burst() -> None:
        for event in events:
            bus.publish(event)
        bridge.flush_now()

    benchmark(burst)
    bridge.detach()


@pytest.mark.qt
@pytest.mark.benchmark(group="qt-bridge")
def test_bench_qt_bridge_batch_only(qtbot, benchmark) -> None:
    """The same burst with only ``events_batch`` connected — the console's
    shape, and the cheaper half of what ``_flush`` does."""
    from nparseplus.ui.qtbridge import QtEventBridge

    bus = EventBus()
    bridge = QtEventBridge(bus)
    state = [0]
    bridge.events_batch.connect(lambda batch: state.__setitem__(0, state[0] + len(batch)))
    events = [SAMPLE_EVENT] * 1000

    def burst() -> None:
        for event in events:
            bus.publish(event)
        bridge.flush_now()

    benchmark(burst)
    bridge.detach()


# --- end to end -------------------------------------------------------------
_LATENCY_LINE = "[Wed Jul 15 21:00:00 2026] You crush a shadowed man for {n} points of damage."


def _pump_until(flag: threading.Event) -> None:
    """Spin the GUI event loop until ``flag`` is set.

    Pumped rather than waited on: ``qtbot.waitUntil`` sleeps between polls and
    the sleep would land inside the number. A busy pump measures the app's
    latency and not our patience.
    """
    from PySide6.QtWidgets import QApplication

    while not flag.is_set():
        QApplication.processEvents()


@pytest.mark.qt
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.benchmark(group="latency")
def test_bench_latency_append_to_ui(qtbot, benchmark, tmp_path) -> None:
    """Log append -> parsed domain event -> a slot on the GUI thread.

    A real ``LogDriver`` tails a real file, so this includes the 100 ms poll
    (``POLL_INTERVAL_S``) that dominates it. It reports close to a FULL poll
    rather than the half a random append would average, because each round
    appends the instant the previous line was delivered — which is to say
    immediately after a poll — so this reads as the worst case. That is the
    more useful bound anyway: it is the longest a user waits, and no phase of
    #131 proposes to change the poll. The benchmark below it isolates the
    part that could move.

    ``pedantic`` with one iteration per round, because a round here is one
    append — the figure is per-append latency, not throughput.
    """
    from nparseplus.ui.qtbridge import QtEventBridge

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "eqlog_Bench_P1999Green.txt"
    log_file.write_text("", encoding="utf-8")

    settings = Settings()
    settings.general.eq_log_dir = log_dir
    backend = build_backend(settings, speaker=NullSpeaker())
    bridge = QtEventBridge(backend.bus)
    seen = threading.Event()
    attached = threading.Event()
    bridge.event_received.connect(
        lambda event: seen.set() if isinstance(event, LineEvent) else None
    )
    bridge.event_received.connect(
        lambda event: attached.set() if isinstance(event, AfterPlayerChangedEvent) else None
    )
    driver: LogDriver = backend.driver
    driver.start()
    counter = [0]

    def append_and_wait() -> None:
        counter[0] += 1
        seen.clear()
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(_LATENCY_LINE.format(n=counter[0] % 90 + 1) + "\n")
            handle.flush()
        _pump_until(seen)

    try:
        # Wait for the tail to attach BEFORE the first append. LogTail.attach
        # seeks to the end of the file, so a line written into the gap between
        # start() and attach is skipped — and that first append would then
        # never arrive at all.
        _pump_until(attached)
        append_and_wait()  # warm-up: first parse of each shape, cold caches
        benchmark.pedantic(append_and_wait, rounds=25, iterations=1, warmup_rounds=0)
    finally:
        driver.stop()
        bridge.detach()


@pytest.mark.qt
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.benchmark(group="latency")
def test_bench_latency_parse_to_ui(qtbot, benchmark, tmp_path) -> None:
    """The same trip with the poll taken out: parse -> bus -> bridge -> slot.

    A worker thread hands one line to ``LogPipeline.process`` exactly as the
    driver would, and the GUI thread is pumped until the slot fires. Everything
    the poll interval hides is in here — the parser chain, every handler, the
    bridge's buffer-and-coalesce, and the queued-signal hop across threads —
    and it is the part any of #131's phases could actually move.
    """
    from nparseplus.ui.qtbridge import QtEventBridge

    backend = build_backend(Settings(), speaker=NullSpeaker())
    bridge = QtEventBridge(backend.bus)
    seen = threading.Event()
    bridge.event_received.connect(
        lambda event: seen.set() if isinstance(event, LineEvent) else None
    )
    counter = [0]

    def publish_and_wait() -> None:
        counter[0] += 1
        seen.clear()
        worker = threading.Thread(
            target=backend.pipeline.process,
            args=(_LATENCY_LINE.format(n=counter[0] % 90 + 1),),
            daemon=True,
        )
        worker.start()
        _pump_until(seen)
        worker.join()

    try:
        publish_and_wait()  # warm-up
        benchmark.pedantic(publish_and_wait, rounds=50, iterations=1, warmup_rounds=0)
    finally:
        bridge.detach()
