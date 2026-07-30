"""HostPluginContext capabilities against a real backend (sharing off)."""

from __future__ import annotations

import functools
import threading
import time
from datetime import datetime
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
    assert ctx.timers is backend.timers
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
