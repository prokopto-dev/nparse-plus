"""The measurement must not cost what it measures (#132).

``core/driver.py`` is explicit that the no-plugin case avoids per-callback
clock reads on the app's hottest loop, and #132 asks for that property to
survive the telemetry it adds. These tests pin it by counting clock reads
rather than by timing anything — a timing assertion on CI is noise, and this
is a structural claim, not a performance one.

Three claims, one test each:

1. With no plugins, nothing anywhere reads a clock per callback.
2. With a plugin loaded and collection OFF, a handler costs no clock reads.
3. With collection ON, a handler costs exactly two — start and stop — and a
   tick costs none of its own, because the driver's budget already read them.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core import driver as driver_module
from nparseplus.core.events import LineEvent
from nparseplus.core.plugins import context as context_module
from nparseplus.core.plugins.context import HostPluginContext, _OwnedNet
from nparseplus.core.plugins.storage import JsonPluginStorage
from nparseplus.core.plugins.telemetry import MetricsCollector
from nparseplus_sdk import PluginMeta

SRC = Path(__file__).resolve().parents[3] / "src" / "nparseplus"

META = PluginMeta(id="cost-test", name="Cost Test")

SAMPLE = LineEvent(
    timestamp=datetime(2026, 7, 15, 21, 0, 0),
    line="You crush a shadowed man for 1 point of damage.",
    line_number=1,
)


class _ClockSpy:
    """Stands in for a module's ``perf_counter``; counts, then answers."""

    def __init__(self) -> None:
        self.reads = 0
        self._now = 1000.0

    def __call__(self) -> float:
        self.reads += 1
        self._now += 0.001
        return self._now


def _ctx(backend, tmp_path, *, collecting: bool | None) -> HostPluginContext:
    """``collecting=None`` means "no metrics at all", the pre-#132 shape."""
    metrics = None
    if collecting is not None:
        metrics = MetricsCollector(enabled=collecting).for_plugin(META.id)
    return HostPluginContext(
        META,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / META.id),
        _OwnedNet(backend),
        metrics=metrics,
    )


# --- 1. the no-plugin case --------------------------------------------------
def test_the_bus_never_reads_a_clock() -> None:
    """``EventBus`` must not import a clock at all.

    The bus is on the path of every parsed line. Asserted on the source
    rather than by patching, because the claim is that there is nothing to
    patch: a future ``import time`` here is exactly the regression that
    would otherwise pass unnoticed.
    """
    tree = ast.parse((SRC / "core" / "bus.py").read_text(encoding="utf-8"))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "time" not in imported
    assert "perf_counter" not in (SRC / "core" / "bus.py").read_text(encoding="utf-8")


def test_the_driver_tick_loop_reads_no_clock_per_callback(monkeypatch, tmp_path) -> None:
    """One iteration with app-owned ticks only takes no per-tick timing.

    The driver reads ``time.monotonic`` once for the log-switch check and
    ``datetime.now`` once for the tick stamp; what must not appear is a
    ``perf_counter`` pair around each callback.
    """
    backend = build_backend(Settings(), speaker=NullSpeaker())
    spy = _ClockSpy()
    monkeypatch.setattr(driver_module.time, "perf_counter", spy)
    assert backend.driver.on_tick, "the app registers its own ticks"

    backend.driver._iterate()

    assert spy.reads == 0


def test_a_backend_with_no_plugins_has_no_supervised_ticks() -> None:
    """The branch that does the timing is not even reachable without one."""
    backend = build_backend(Settings(), speaker=NullSpeaker())
    assert backend.driver._supervised == {}


# --- 2 & 3. the plugin case -------------------------------------------------
@pytest.mark.parametrize(
    ("collecting", "expected"),
    [
        # No metrics object at all: the pre-#132 wrapper, byte for byte.
        (None, 0),
        # Metrics present but collection off: the gate is an attribute read.
        (False, 0),
        # On: exactly one pair, start and stop. Not three, not one per
        # subscriber-list walk — the wrapper is the only thing timing.
        (True, 2),
    ],
)
def test_handler_clock_reads(monkeypatch, tmp_path, collecting: bool | None, expected: int) -> None:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    ctx = _ctx(backend, tmp_path, collecting=collecting)
    ctx.subscribe(LineEvent, lambda event: None)
    spy = _ClockSpy()
    monkeypatch.setattr(context_module, "perf_counter", spy)

    backend.bus.publish(SAMPLE)

    assert spy.reads == expected


def test_a_raising_handler_still_records_its_cost(tmp_path) -> None:
    """The expensive case is the one that must not be exempt."""
    backend = build_backend(Settings(), speaker=NullSpeaker())
    metrics = MetricsCollector(enabled=True).for_plugin(META.id)
    ctx = HostPluginContext(
        META,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / META.id),
        _OwnedNet(backend),
        metrics=metrics,
    )

    def boom(event: object) -> None:
        raise RuntimeError("no")

    ctx.subscribe(LineEvent, boom)
    backend.bus.publish(SAMPLE)

    snapshot = metrics.handlers.snapshot()
    assert snapshot.calls == 1
    assert snapshot.errors == 1


def test_a_raising_tick_is_counted_as_an_error_and_still_timed(tmp_path) -> None:
    """Every channel reports errors, and the tick channel is no exception.

    Only this guard sees the raise — the driver's watchdog measures duration
    and knows nothing about it — so a tick throwing on every iteration would
    otherwise read as a tick with no errors at all.
    """
    backend = build_backend(Settings(), speaker=NullSpeaker())
    metrics = MetricsCollector(enabled=True).for_plugin(META.id)
    ctx = HostPluginContext(
        META,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / META.id),
        _OwnedNet(backend),
        metrics=metrics,
    )

    def boom(now: datetime) -> None:
        raise RuntimeError("no")

    ctx.add_tick(boom)
    backend.driver._run_supervised_ticks(datetime(2026, 7, 15, 21, 0, 0))
    backend.driver._run_supervised_ticks(datetime(2026, 7, 15, 21, 0, 1))

    snapshot = metrics.ticks.snapshot()
    assert snapshot.errors == 2
    assert snapshot.calls == 2  # a raising tick still cost what it cost
    # Raising is not overrunning: the watchdog evicts a tick for being slow,
    # never for throwing, so this one is still registered.
    assert len(backend.driver._supervised) == 1


def test_a_tick_with_no_metrics_still_swallows_its_exception(tmp_path) -> None:
    """The pre-#132 path: guarded, logged, never counted, never propagated."""
    backend = build_backend(Settings(), speaker=NullSpeaker())
    ctx = _ctx(backend, tmp_path, collecting=None)

    def boom(now: datetime) -> None:
        raise RuntimeError("no")

    ctx.add_tick(boom)
    backend.driver._run_supervised_ticks(datetime(2026, 7, 15, 21, 0, 0))


def test_a_plugin_tick_adds_no_clock_reads_of_its_own(monkeypatch, tmp_path) -> None:
    """The driver's budget already timed it; the channel takes that value.

    So the count here is what the watchdog costs and not a byte more — the
    pair the driver takes, and nothing from ``context``.
    """
    backend = build_backend(Settings(), speaker=NullSpeaker())
    metrics = MetricsCollector(enabled=True).for_plugin(META.id)
    ctx = HostPluginContext(
        META,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / META.id),
        _OwnedNet(backend),
        metrics=metrics,
    )
    ctx.add_tick(lambda now: None)
    context_spy = _ClockSpy()
    monkeypatch.setattr(context_module, "perf_counter", context_spy)
    driver_spy = _ClockSpy()
    monkeypatch.setattr(driver_module.time, "perf_counter", driver_spy)

    backend.driver._run_supervised_ticks(datetime(2026, 7, 15, 21, 0, 0))

    assert context_spy.reads == 0
    assert driver_spy.reads == 2  # the watchdog's own pair, as before #132
    assert metrics.ticks.snapshot().calls == 1


def test_app_owned_ticks_stay_untimed_beside_a_plugin_one(monkeypatch, tmp_path) -> None:
    """Adding a plugin must not start timing the app's own callbacks."""
    backend = build_backend(Settings(), speaker=NullSpeaker())
    ctx = _ctx(backend, tmp_path, collecting=True)
    ctx.add_tick(lambda now: None)
    app_ticks = len(backend.driver.on_tick) - 1
    assert app_ticks > 0
    spy = _ClockSpy()
    monkeypatch.setattr(driver_module.time, "perf_counter", spy)

    backend.driver._run_supervised_ticks(datetime(2026, 7, 15, 21, 0, 0))

    # One pair for the single supervised tick, regardless of how many
    # app-owned ticks ran beside it.
    assert spy.reads == 2


def test_a_plugin_parser_only_times_itself(monkeypatch, tmp_path) -> None:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    metrics = MetricsCollector(enabled=True).for_plugin(META.id)
    ctx = HostPluginContext(
        META,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / META.id),
        _OwnedNet(backend),
        metrics=metrics,
    )

    class _Parser:
        def handle(self, line, context) -> bool:
            return False

    ctx.add_parser(_Parser())
    spy = _ClockSpy()
    monkeypatch.setattr(context_module, "perf_counter", spy)

    # A line no built-in parser consumes, so it reaches the plugin's.
    backend.pipeline.process("[Wed Jul 15 21:00:00 2026] A gnoll pup scratches its ear.")

    assert spy.reads == 2
    assert metrics.parsers.snapshot().calls == 1


def test_a_disabled_collector_still_lets_the_parser_run(tmp_path) -> None:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    metrics = MetricsCollector(enabled=False).for_plugin(META.id)
    ctx = HostPluginContext(
        META,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / META.id),
        _OwnedNet(backend),
        metrics=metrics,
    )
    seen: list[str] = []

    class _Parser:
        def handle(self, line, context) -> bool:
            seen.append(line.message)
            return False

    ctx.add_parser(_Parser())
    backend.pipeline.process("[Wed Jul 15 21:00:00 2026] A gnoll pup scratches its ear.")

    assert seen == ["A gnoll pup scratches its ear."]
    assert metrics.parsers.snapshot().calls == 0


def test_the_wrapper_keeps_the_real_parser_name_in_logs(tmp_path) -> None:
    """``describe_parser`` is what stops the wrapper eating the log line."""
    from nparseplus.core.parsers.base import describe_parser

    backend = build_backend(Settings(), speaker=NullSpeaker())
    metrics = MetricsCollector(enabled=True).for_plugin(META.id)
    ctx = HostPluginContext(
        META,
        backend,
        "0.0.0",
        JsonPluginStorage(tmp_path / "plugin-data" / META.id),
        _OwnedNet(backend),
        metrics=metrics,
    )

    class LoudParser:
        def handle(self, line, context) -> bool:
            raise RuntimeError("nope")

    ctx.add_parser(LoudParser())
    registered = ctx._parsers[-1]
    assert "LoudParser" in describe_parser(registered)
    assert META.id in describe_parser(registered)


def test_a_raising_plugin_parser_still_does_not_break_the_chain(tmp_path, caplog) -> None:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    ctx = _ctx(backend, tmp_path, collecting=True)

    class LoudParser:
        def handle(self, line, context) -> bool:
            raise RuntimeError("nope")

    ctx.add_parser(LoudParser())
    events: list[object] = []
    backend.bus.subscribe(LineEvent, events.append)
    backend.pipeline.process("[Wed Jul 15 21:00:00 2026] A gnoll pup scratches its ear.")

    assert len(events) == 1  # the firehose still fired
    assert ctx._metrics is not None
    assert ctx._metrics.parsers.snapshot().errors == 1


def test_unwind_removes_the_wrapper_not_the_bare_parser(tmp_path) -> None:
    """The chain holds the wrapper, so that is what removal has to find."""
    backend = build_backend(Settings(), speaker=NullSpeaker())
    ctx = _ctx(backend, tmp_path, collecting=True)
    before = len(backend.pipeline._parsers)

    class _Parser:
        def handle(self, line, context) -> bool:
            return False

    ctx.add_parser(_Parser())
    assert len(backend.pipeline._parsers) == before + 1
    ctx.unwind()
    assert len(backend.pipeline._parsers) == before


def test_telemetry_lives_inside_the_plugins_namespace() -> None:
    """So the master toggle's poisoned-import guard already covers it.

    ``tests/core/plugins/test_master_toggle.py`` poisons
    ``nparseplus.core.plugins``; keeping the collector under that package is
    what makes "plugins off costs nothing" true of this feature too, with no
    second gate to maintain.
    """
    from nparseplus.core.plugins import telemetry

    assert telemetry.__name__.startswith("nparseplus.core.plugins.")
