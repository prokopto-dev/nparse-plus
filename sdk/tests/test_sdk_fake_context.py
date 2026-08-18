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


# -- pop-window timers (#125) --------------------------------------------------


def test_timers_defaults_to_a_fake_store() -> None:
    """It used to default to None, which is why validate_plugin could not
    exercise a plugin that touches timers at activate time."""
    from nparseplus_sdk.testing import FakeTimers

    ctx = FakePluginContext()
    assert isinstance(ctx.timers, FakeTimers)
    assert ctx.timers.snapshot() == []
    # An explicit store still wins.
    sentinel = object()
    assert FakePluginContext(timers=sentinel).timers is sentinel


def test_add_window_timer_records_and_returns() -> None:
    from datetime import datetime, timedelta

    from nparseplus_sdk import WindowTimerLike

    tod = datetime(2026, 7, 15, 12, 0, 0)
    ctx = FakePluginContext()
    row = ctx.add_window_timer(
        "Trakanon",
        group="  Mob Timers",
        started_at=tod,
        base_seconds=400,
        window_seconds=900,
    )
    assert ctx.window_timers == [row]
    assert isinstance(row, WindowTimerLike)
    assert row.name == "Trakanon"
    assert row.group == "  Mob Timers"
    assert row.ends_at == tod + timedelta(seconds=400)
    assert row.window_ends_at == tod + timedelta(seconds=1300)
    # Never stamped by the fake: only a driver tick observes the crossover.
    assert row.window_opened_at is None
    # Visible through ctx.timers too, like the host store.
    assert ctx.timers.find("Trakanon", "  Mob Timers") is row


def test_add_window_timer_rejects_an_empty_window() -> None:
    from datetime import datetime

    import pytest

    ctx = FakePluginContext()
    with pytest.raises(ValueError):
        ctx.add_window_timer(
            "Bad",
            group="g",
            started_at=datetime(2026, 7, 15, 12, 0, 0),
            base_seconds=10,
            window_seconds=0,
        )


def test_fake_timers_records_the_ordinary_adds_too() -> None:
    from nparseplus_sdk.testing import FakeTimers

    timers = FakeTimers()
    row = object()
    assert timers.add_timer(row) is row
    assert timers.snapshot() == [row]
    assert timers.remove_row(row) is True
    assert timers.remove_row(row) is False
