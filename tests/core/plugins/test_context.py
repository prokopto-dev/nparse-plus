"""HostPluginContext capabilities against a real backend (sharing off)."""

from __future__ import annotations

import functools
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from nparseplus.core import driver as driver_module
from nparseplus.core.events import LineEvent
from nparseplus.core.plugins import context as context_module
from nparseplus.core.plugins.context import HostPluginContext, _OwnedNet
from nparseplus.core.plugins.storage import JsonPluginStorage
from nparseplus_sdk import PluginMeta

META = PluginMeta(id="ctx-test", name="Ctx Test")

RAW_UNCLAIMED = "[Wed Jul 15 12:00:00 2026] A gnoll pup scratches its ear."
RAW_CONSUMED = "[Wed Jul 15 12:00:00 2026] You have entered East Commonlands."


def make_ctx(backend, tmp_path) -> HostPluginContext:
    storage = JsonPluginStorage(tmp_path / "plugin-data" / META.id)
    return HostPluginContext(META, backend, "1.15.0", storage, _OwnedNet(backend))


class SyncWorker:
    """NetWorker stand-in: runs fetch inline, still delivers through `deliver`."""

    def __init__(self, deliver) -> None:
        self._deliver = deliver
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def submit(self, fetch, apply=None) -> None:
        result = fetch()
        if apply is not None:
            self._deliver(functools.partial(apply, result))


def test_identity_and_logger(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    assert ctx.meta is META
    assert ctx.app_version == "1.15.0"
    assert ctx.logger.name == "nparseplus.plugins.ctx-test"
    # ctx.timers is the tagging facade, not the service itself (#45) — but
    # everything it does not stamp reaches the same object.
    assert ctx.timers is ctx.timers
    assert ctx.timers.snapshot() == backend.timers.snapshot()
    assert ctx.timers.on_change is backend.timers.on_change
    assert ctx.player is backend.player
    assert ctx.speaker is backend.speaker


def test_subscribe_guarded_and_unsubscribable(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    seen: list[str] = []

    def bad_handler(event: LineEvent) -> None:
        raise RuntimeError("handler boom")

    ctx.subscribe(LineEvent, bad_handler)
    unsubscribe = ctx.subscribe(LineEvent, lambda e: seen.append(e.line))
    backend.pipeline.process(RAW_UNCLAIMED)  # bad handler must not break dispatch
    assert seen == ["A gnoll pup scratches its ear."]
    unsubscribe()
    backend.pipeline.process(RAW_UNCLAIMED)
    assert len(seen) == 1


def test_plugin_parser_runs_after_builtins(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    handled: list[str] = []

    class RecordingParser:
        def handle(self, line: Any, pctx: Any) -> bool:
            handled.append(line.message)
            return True

    ctx.add_parser(RecordingParser())
    line_events: list[LineEvent] = []
    backend.bus.subscribe(LineEvent, line_events.append)

    backend.pipeline.process(RAW_CONSUMED)  # a built-in consumes zone entry
    assert handled == []
    backend.pipeline.process(RAW_UNCLAIMED)
    assert handled == ["A gnoll pup scratches its ear."]
    # The raw-line firehose still fires for both lines.
    assert len(line_events) == 2


def test_add_tick_guarded(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    ran: list[datetime] = []
    ctx.add_tick(lambda now: (_ for _ in ()).throw(RuntimeError("tick boom")))
    ctx.add_tick(ran.append)
    now = datetime.now()
    for tick in list(backend.driver.on_tick):
        tick(now)  # simulating the driver loop; nothing may raise
    assert ran == [now]


def test_plugin_ticks_are_supervised_but_builtins_are_not(backend, tmp_path) -> None:
    builtin_ticks = list(backend.driver.on_tick)  # composition.py's own
    assert backend.driver._supervised == {}
    ctx = make_ctx(backend, tmp_path)
    ctx.add_tick(lambda now: None)
    supervised = list(backend.driver._supervised)
    assert len(supervised) == 1
    assert supervised[0] not in builtin_ticks
    assert backend.driver._supervised[supervised[0]].label == "plugin ctx-test"


def test_a_hogging_tick_is_dropped_and_the_context_records_it(
    backend, tmp_path, monkeypatch
) -> None:
    """A plugin that stalls the driver loses its tick; the fact is readable."""
    # Budget 0 makes every run a breach — the timing arithmetic itself is
    # covered in tests/core/test_driver.py with a hand-cranked clock.
    monkeypatch.setattr(driver_module, "TICK_BUDGET_S", 0.0)
    ctx = make_ctx(backend, tmp_path)
    runs: list[datetime] = []
    ctx.add_tick(runs.append)
    assert ctx.tick_dropped is None

    for _ in range(driver_module.TICK_BREACH_LIMIT + 3):
        backend.driver._run_supervised_ticks(datetime.now())

    assert len(runs) == driver_module.TICK_BREACH_LIMIT
    assert ctx.tick_dropped is not None and "removed" in ctx.tick_dropped
    assert backend.driver._supervised == {}
    ctx.unwind()  # unwinding an already-dropped tick must not raise


def test_submit_without_sharing_lazily_creates_worker_and_applies_on_tick(
    backend, tmp_path, monkeypatch
) -> None:
    assert backend.net_worker is None  # sharing off in these fixtures
    monkeypatch.setattr(context_module, "NetWorker", SyncWorker)
    ctx = make_ctx(backend, tmp_path)
    applied: list[int] = []
    ctx.submit(lambda: 41 + 1, applied.append)
    # fetch ran, but apply is parked in the coordinator inbox until the tick.
    assert applied == []
    backend.sharing.tick(datetime.now())
    assert applied == [42]


def test_submit_apply_errors_are_guarded(backend, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(context_module, "NetWorker", SyncWorker)
    ctx = make_ctx(backend, tmp_path)

    def bad_apply(result: int) -> None:
        raise RuntimeError("apply boom")

    ctx.submit(lambda: 1, bad_apply)
    backend.sharing.tick(datetime.now())  # must not raise


def test_submit_uses_backend_worker_when_sharing_built_one(backend, tmp_path) -> None:
    recorded: list[tuple] = []

    class RecordingWorker:
        def submit(self, fetch, apply=None) -> None:
            recorded.append((fetch, apply))

    backend.net_worker = RecordingWorker()  # what a "pigparse" mode backend has
    ctx = make_ctx(backend, tmp_path)
    ctx.submit(lambda: 1)
    assert len(recorded) == 1


def test_pigparse_lazy_creation_and_close(backend, tmp_path, monkeypatch) -> None:
    created: list[FakeApi] = []

    class FakeApi:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url
            self.closed = False
            created.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(context_module, "PigParseApiClient", FakeApi)
    owned = _OwnedNet(backend)
    ctx = HostPluginContext(META, backend, "1.15.0", JsonPluginStorage(tmp_path / "d"), owned)
    api_first = ctx.pigparse
    assert ctx.pigparse is api_first  # cached, one client for all plugins
    assert created == [api_first]
    owned.close()
    assert api_first.closed


def run_concurrently(fn, threads: int = 8) -> None:
    """Fire ``fn`` from several threads released at the same moment."""
    start = threading.Event()

    def runner() -> None:
        start.wait()
        fn()

    workers = [threading.Thread(target=runner) for _ in range(threads)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=5)


def test_pigparse_client_built_once_under_concurrent_access(backend, tmp_path, monkeypatch) -> None:
    """A plugin may touch ctx.pigparse from a tick and a Qt timer at once."""
    created: list[Any] = []

    class SlowApi:
        def __init__(self, base_url: str) -> None:
            time.sleep(0.02)  # widen the window an unlocked check-then-set loses
            created.append(self)

        def close(self) -> None:
            pass

    monkeypatch.setattr(context_module, "PigParseApiClient", SlowApi)
    ctx = make_ctx(backend, tmp_path)
    clients: list[Any] = []
    run_concurrently(lambda: clients.append(ctx.pigparse))
    assert len(created) == 1
    assert {id(client) for client in clients} == {id(created[0])}


def test_net_worker_started_once_under_concurrent_submit(backend, tmp_path, monkeypatch) -> None:
    started: list[Any] = []

    class SlowWorker(SyncWorker):
        def __init__(self, deliver) -> None:
            super().__init__(deliver)
            time.sleep(0.02)

        def start(self) -> None:
            super().start()
            started.append(self)

    monkeypatch.setattr(context_module, "NetWorker", SlowWorker)
    ctx = make_ctx(backend, tmp_path)
    run_concurrently(lambda: ctx.submit(lambda: 1))
    assert len(started) == 1  # a leaked second worker means a leaked thread


def test_pigparse_prefers_backend_client(backend, tmp_path) -> None:
    sentinel = object()
    backend.pigparse_api = sentinel
    ctx = make_ctx(backend, tmp_path)
    assert ctx.pigparse is sentinel


# -- pop-window timers (#125) --------------------------------------------------

TOD = datetime(2026, 7, 15, 12, 0, 0)


def test_add_window_timer_builds_the_expected_row(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    row = ctx.add_window_timer(
        "Trakanon",
        group="  Mob Timers",
        started_at=TOD,
        base_seconds=400,
        window_seconds=900,
    )
    assert row.name == "Trakanon"
    assert row.group == "  Mob Timers"
    # Durations from the anchor: the window OPENS at base, closes a window later.
    assert row.ends_at == TOD + timedelta(seconds=400)
    assert row.window_ends_at == TOD + timedelta(seconds=1300)
    assert row.total_duration_s == 400.0
    # Not stamped: only a driver tick that observes the crossover does that.
    assert row.window_opened_at is None
    # And it really is in the host store.
    assert backend.timers.find("Trakanon", "  Mob Timers") is row


def test_add_window_timer_satisfies_the_sdk_protocol(backend, tmp_path) -> None:
    from nparseplus_sdk import WindowTimerLike

    ctx = make_ctx(backend, tmp_path)
    row = ctx.add_window_timer(
        "Trakanon", group="g", started_at=TOD, base_seconds=10, window_seconds=20
    )
    assert isinstance(row, WindowTimerLike)


def test_add_window_timer_raises_through_rather_than_swallowing(backend, tmp_path) -> None:
    """Not _guarded: this is the plugin calling in, already inside its own
    guarded subscription or tick, so the error belongs in its frame."""
    import pytest
    from pydantic import ValidationError

    ctx = make_ctx(backend, tmp_path)
    with pytest.raises(ValidationError):
        ctx.add_window_timer("Bad", group="g", started_at=TOD, base_seconds=100, window_seconds=0)
    assert backend.timers.find("Bad", "g") is None


def test_unwind_leaves_the_timer_row(backend, tmp_path) -> None:
    """Asserted so nobody "fixes" it later: unwind reverses registrations, and
    a timer row is data the plugin put in a user-visible store — same as
    ctx.timers.add_timer today. remove_row is the way back out; the owner tag
    (#45) is how the *host* takes it out when the plugin is disabled."""
    ctx = make_ctx(backend, tmp_path)
    row = ctx.add_window_timer(
        "Trakanon", group="g", started_at=TOD, base_seconds=400, window_seconds=900
    )
    ctx.unwind()
    assert backend.timers.find("Trakanon", "g") is row
    assert row.owner == META.id


# -- rows carry their plugin, so disabling one can take them away (#45) --------


def test_rows_added_through_ctx_timers_are_tagged(backend, tmp_path) -> None:
    from nparseplus.core.timers import CounterRow, TimerRow

    ctx = make_ctx(backend, tmp_path)
    ctx.timers.add_timer(
        TimerRow(
            name="Plugin Countdown",
            group="g",
            updated_at=TOD,
            ends_at=TOD + timedelta(minutes=5),
            total_duration_s=300.0,
        )
    )
    ctx.timers.add_counter(CounterRow(name="Casts", group="g", updated_at=TOD))
    assert {row.owner for row in backend.timers.snapshot()} == {META.id}
    assert backend.timers.remove_owner(META.id) == 2
    assert backend.timers.snapshot() == []


def test_incrementing_someone_elses_counter_does_not_claim_it(backend, tmp_path) -> None:
    """The app (or another plugin) keeps the row it created."""
    from nparseplus.core.timers import CounterRow

    ours = backend.timers.add_counter(CounterRow(name="Casts", group="g", updated_at=TOD))
    ctx = make_ctx(backend, tmp_path)
    ctx.timers.add_counter(CounterRow(name="Casts", group="g", updated_at=TOD))
    assert ours.count == 2
    assert ours.owner == ""


def test_timer_mutations_off_the_driver_thread_wait_for_it(backend, tmp_path) -> None:
    """TimersService belongs to the driver thread, and #45 made it reachable
    from the GUI thread: a plugin enabled from the settings window runs its
    activate() there. So a mutation from anywhere but the driver thread is
    queued and lands at the next loop boundary — with the row still handed
    back, since the plugin owns that object either way."""
    import contextlib

    from nparseplus.core.timers import TimerRow

    backend.driver.log_dir = tmp_path
    ctx = make_ctx(backend, tmp_path)
    # Wall-clock anchored: _iterate ticks the service right after draining,
    # and a row that ended a month ago would expire on the same pass.
    now = datetime.now()
    row = TimerRow(
        name="Deferred",
        group="g",
        updated_at=now,
        ends_at=now + timedelta(minutes=5),
        total_duration_s=300.0,
    )
    with contextlib.ExitStack() as stack:
        stack.callback(_stop_accepting, backend.driver)
        _start_accepting(backend.driver)

        assert ctx.timers.add_timer(row) is row
        assert backend.timers.snapshot() == []  # queued, not applied here
        backend.driver._iterate()
        assert backend.timers.find("Deferred", "g") is row
        assert row.owner == META.id

        assert ctx.timers.remove_row(row) is True  # it was there when asked
        assert backend.timers.find("Deferred", "g") is row  # ...still, for now
        backend.driver._iterate()
        assert backend.timers.snapshot() == []


def test_timer_mutations_on_the_driver_thread_land_immediately(backend, tmp_path) -> None:
    """The ordinary path — a plugin's own handler, tick or parser, and every
    call at startup — is unchanged: applied now, and the service's own answer
    comes back (which is what makes add_counter's merge visible)."""
    from nparseplus.core.timers import CounterRow

    ctx = make_ctx(backend, tmp_path)
    first = ctx.timers.add_counter(CounterRow(name="Casts", group="g", updated_at=TOD))
    again = ctx.timers.add_counter(CounterRow(name="Casts", group="g", updated_at=TOD))
    assert again is first and first.count == 2
    assert ctx.timers.remove_row(first) is True
    assert backend.timers.snapshot() == []


def _start_accepting(driver) -> None:
    """The state ``start()`` leaves the driver in, without the poll loop."""
    with driver._command_lock:
        driver._accepting_commands = True


def _stop_accepting(driver) -> None:
    with driver._command_lock:
        driver._accepting_commands = False


def test_unwind_clears_the_slow_tick_note(backend, tmp_path) -> None:
    """The note describes a tick that no longer exists once unwind ran.

    Left behind, a plugin re-enabled in the same session would inherit
    "tick disabled (too slow)" from the activation before it.
    """
    ctx = make_ctx(backend, tmp_path)
    ctx.add_tick(lambda now: None)
    ctx._note_tick_dropped("tick disabled (too slow)")
    assert ctx.tick_dropped is not None
    ctx.unwind()
    assert ctx.tick_dropped is None


# -- several candidate windows for one spawn (#125) ----------------------------

LODIZAL_WINDOWS = [(12 * 3600, 4 * 3600), (20 * 3600, 4 * 3600), (30 * 3600, 6 * 3600)]


def test_add_window_series_arms_every_candidate(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    rows = ctx.add_window_series(
        "--Dead-- Lodizal",
        group="  Mob Timers",
        started_at=TOD,
        windows=LODIZAL_WINDOWS,
    )
    assert len(rows) == 3
    assert [(r.window_index, r.window_count) for r in rows] == [(1, 3), (2, 3), (3, 3)]
    assert len({r.window_series for r in rows}) == 1
    assert rows[0].ends_at == TOD + timedelta(hours=12)
    assert rows[2].window_ends_at == TOD + timedelta(hours=36)
    # They share a name on purpose and must NOT have replaced one another.
    assert len(backend.timers.snapshot()) == 3


def test_the_series_key_is_derived_so_it_survives_a_rebuild(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    first = ctx.add_window_series(
        "--Dead-- Lodizal", group="g", started_at=TOD, windows=LODIZAL_WINDOWS
    )
    backend.timers.remove_series(first[0].window_series)
    again = ctx.add_window_series(
        "--Dead-- Lodizal", group="g", started_at=TOD, windows=LODIZAL_WINDOWS
    )
    assert again[0].window_series == first[0].window_series


def test_remove_series_clears_the_whole_set(backend, tmp_path) -> None:
    ctx = make_ctx(backend, tmp_path)
    rows = ctx.add_window_series(
        "--Dead-- Lodizal", group="g", started_at=TOD, windows=LODIZAL_WINDOWS
    )
    assert backend.timers.remove_series(rows[0].window_series) == 3
    assert backend.timers.snapshot() == []


def test_add_window_series_rejects_a_malformed_table(backend, tmp_path) -> None:
    """Rules about the SET, which no single row can check."""
    import pytest

    ctx = make_ctx(backend, tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        ctx.add_window_series("x", group="g", started_at=TOD, windows=[])
    with pytest.raises(ValueError, match="positive span"):
        ctx.add_window_series("x", group="g", started_at=TOD, windows=[(100, 0)])
    with pytest.raises(ValueError, match="ascending"):
        ctx.add_window_series("x", group="g", started_at=TOD, windows=[(100, 50), (120, 50)])
    assert backend.timers.snapshot() == []
