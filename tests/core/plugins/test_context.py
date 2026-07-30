"""HostPluginContext capabilities against a real backend (sharing off)."""

from __future__ import annotations

import functools
from datetime import datetime
from typing import Any

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


def test_pigparse_prefers_backend_client(backend, tmp_path) -> None:
    sentinel = object()
    backend.pigparse_api = sentinel
    ctx = make_ctx(backend, tmp_path)
    assert ctx.pigparse is sentinel
