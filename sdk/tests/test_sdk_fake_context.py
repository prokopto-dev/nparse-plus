"""FakePluginContext behaves like the contract plugins will meet in-app."""

from __future__ import annotations

from dataclasses import dataclass

from nparseplus_sdk import PluginMeta
from nparseplus_sdk.testing import FakePluginContext


@dataclass(frozen=True)
class _Ping:
    n: int


@dataclass(frozen=True)
class _Pong:
    n: int


def test_subscribe_publish_and_unsubscribe() -> None:
    ctx = FakePluginContext()
    seen: list[int] = []
    unsubscribe = ctx.subscribe(_Ping, lambda e: seen.append(e.n))
    ctx.publish(_Ping(1))
    ctx.publish(_Pong(2))  # exact-type dispatch: not delivered
    assert seen == [1]
    unsubscribe()
    ctx.publish(_Ping(3))
    assert seen == [1]


def test_submit_records_without_executing_then_runs() -> None:
    ctx = FakePluginContext()
    calls: list[str] = []
    ctx.submit(lambda: calls.append("fetch") or 42, lambda result: calls.append(f"apply:{result}"))
    assert calls == []  # nothing executed at submit time (no network in activate)
    ctx.run_submitted()
    assert calls == ["fetch", "apply:42"]
    assert ctx.submitted == []


def test_storage_roundtrip_and_speaker_recording() -> None:
    ctx = FakePluginContext(PluginMeta(id="my-plug", name="Mine"))
    ctx.storage.save({"items": ["Words of X"]})
    assert ctx.storage.load() == {"items": ["Words of X"]}
    assert ctx.storage.save_count == 1
    ctx.speaker.speak("hello")
    assert ctx.speaker.spoken == ["hello"]
    assert ctx.logger.name == "nparseplus.plugins.my-plug"


def test_pigparse_recorder() -> None:
    ctx = FakePluginContext()
    ctx.pigparse.item_prices(1, ["Fine Steel Long Sword"])
    assert ctx.pigparse.calls == [("item_prices", (1, ["Fine Steel Long Sword"]), {})]
