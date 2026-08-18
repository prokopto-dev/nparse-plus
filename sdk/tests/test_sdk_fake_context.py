"""FakePluginContext behaves like the contract plugins will meet in-app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


# -- the fake store mirrors the host's replacement rules -----------------------


def _timer(name: str, group: str = "g"):
    from dataclasses import make_dataclass

    row = make_dataclass("Row", [("name", str), ("group", str)])
    return row(name, group)


def test_add_timer_replaces_the_same_name_and_group_by_default() -> None:
    """The host drops an existing TimerRow with the same (name, group); a
    plugin test that adds twice must see one row, as the app would show."""
    from nparseplus_sdk.testing import FakeTimers

    timers = FakeTimers()
    first = timers.add_timer(_timer("Trakanon"))
    second = timers.add_timer(_timer("trakanon"))  # casefold identity, like the host
    assert timers.snapshot() == [second]
    assert first not in timers.snapshot()


def test_add_timer_keeps_both_when_duplicates_are_allowed() -> None:
    from nparseplus_sdk.testing import FakeTimers

    timers = FakeTimers()
    first = timers.add_timer(_timer("Trakanon"))
    second = timers.add_timer(_timer("Trakanon"), allow_duplicates=True)
    assert timers.snapshot() == [first, second]


def test_replacement_only_matches_rows_added_the_same_way() -> None:
    """Stands in for the host's isinstance check: a spell and a timer sharing
    a name are different rows there, and must be here too."""
    from nparseplus_sdk.testing import FakeTimers

    timers = FakeTimers()
    spell = timers.add_spell(_timer("Trakanon"))
    timer = timers.add_timer(_timer("Trakanon"))
    assert timers.snapshot() == [spell, timer]


def test_add_spell_overwrites_unless_told_not_to() -> None:
    from nparseplus_sdk.testing import FakeTimers

    timers = FakeTimers()
    timers.add_spell(_timer("Clarity"))
    second = timers.add_spell(_timer("Clarity"))
    assert timers.snapshot() == [second]

    third = timers.add_spell(_timer("Clarity"), overwrite=False)
    assert timers.snapshot() == [second, third]


def test_find_is_casefold_like_the_host() -> None:
    from nparseplus_sdk.testing import FakeTimers

    timers = FakeTimers()
    row = timers.add_timer(_timer("Trakanon", "  Mob Timers"))
    assert timers.find("trakanon") is row
    assert timers.find("TRAKANON", "  mob timers") is row
    assert timers.find("Trakanon", "elsewhere") is None


def test_add_window_timer_goes_through_the_store_not_around_it() -> None:
    """Regression: it used to record the row and append straight into
    FakeTimers.rows, so an injected double never saw it and allow_duplicates
    was silently dropped."""
    from datetime import datetime

    calls: list[tuple[Any, bool]] = []

    class RecordingTimers:
        def add_timer(self, row, allow_duplicates=False):
            calls.append((row, allow_duplicates))
            return row

    ctx = FakePluginContext(timers=RecordingTimers())
    row = ctx.add_window_timer(
        "Trakanon",
        group="g",
        started_at=datetime(2026, 7, 15, 12, 0, 0),
        base_seconds=10,
        window_seconds=20,
        allow_duplicates=True,
    )
    assert calls == [(row, True)]
    assert ctx.window_timers == [row]


def test_add_window_timer_replaces_by_default_through_the_store() -> None:
    from datetime import datetime

    tod = datetime(2026, 7, 15, 12, 0, 0)
    ctx = FakePluginContext()
    ctx.add_window_timer("Trakanon", group="g", started_at=tod, base_seconds=10, window_seconds=20)
    second = ctx.add_window_timer(
        "Trakanon", group="g", started_at=tod, base_seconds=10, window_seconds=20
    )
    # Two recorded arms, but the store shows one row — what the app would show.
    assert len(ctx.window_timers) == 2
    assert ctx.timers.snapshot() == [second]


def test_add_window_timer_rejects_a_store_that_cannot_participate() -> None:
    """Better than silently recording a row the store never saw."""
    from datetime import datetime

    import pytest

    ctx = FakePluginContext(timers=object())
    with pytest.raises(TypeError, match="add_timer"):
        ctx.add_window_timer(
            "Trakanon",
            group="g",
            started_at=datetime(2026, 7, 15, 12, 0, 0),
            base_seconds=10,
            window_seconds=20,
        )
    assert ctx.window_timers == []


def test_add_window_series_records_every_candidate() -> None:
    from datetime import datetime

    from nparseplus_sdk import WindowTimerLike

    tod = datetime(2026, 7, 15, 12, 0, 0)
    ctx = FakePluginContext()
    rows = ctx.add_window_series(
        "--Dead-- Lodizal",
        group="  Mob Timers",
        started_at=tod,
        windows=[(12 * 3600, 4 * 3600), (20 * 3600, 4 * 3600), (30 * 3600, 6 * 3600)],
    )
    assert len(rows) == 3
    assert all(isinstance(r, WindowTimerLike) for r in rows)
    assert [(r.window_index, r.window_count) for r in rows] == [(1, 3), (2, 3), (3, 3)]
    assert len({r.window_series for r in rows}) == 1
    # Recorded, and in the store — sharing a name without replacing each other.
    assert ctx.window_timers == rows
    assert len(ctx.timers.snapshot()) == 3


def test_add_window_series_rejects_a_malformed_table() -> None:
    from datetime import datetime

    import pytest

    tod = datetime(2026, 7, 15, 12, 0, 0)
    ctx = FakePluginContext()
    with pytest.raises(ValueError, match="at least one"):
        ctx.add_window_series("x", group="g", started_at=tod, windows=[])
    with pytest.raises(ValueError, match="positive span"):
        ctx.add_window_series("x", group="g", started_at=tod, windows=[(100, 0)])
    with pytest.raises(ValueError, match="ascending"):
        ctx.add_window_series("x", group="g", started_at=tod, windows=[(100, 50), (120, 50)])
    assert ctx.window_timers == []
