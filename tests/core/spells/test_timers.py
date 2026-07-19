"""TimersService unit tests (SpellWindowViewModel row bookkeeping)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.core.spells.conftest import T0

from nparseplus.core.spells.spells_us import SpellBook
from nparseplus.core.timers import (
    YOU_GROUP,
    CounterRow,
    RollRow,
    SpellRow,
    TimersService,
    YouSpellSnapshot,
)


@pytest.fixture
def timers() -> TimersService:
    return TimersService()


def _spell_row(
    spell_book: SpellBook,
    name: str = "Clarity",
    group: str = YOU_GROUP,
    seconds: float = 100.0,
    spell_name: str | None = None,
    **kwargs: object,
) -> SpellRow:
    spell = spell_book.spell_by_name(spell_name or name)
    assert spell is not None
    return SpellRow(
        name=name,
        group=group,
        updated_at=T0,
        spell=spell,
        ends_at=T0 + timedelta(seconds=seconds),
        total_duration_s=seconds,
        **kwargs,  # type: ignore[arg-type]
    )


def test_add_spell_overwrites_same_name_and_group(
    timers: TimersService, spell_book: SpellBook
) -> None:
    timers.add_spell(_spell_row(spell_book, seconds=50))
    timers.add_spell(_spell_row(spell_book, seconds=200))
    rows = timers.rows_of(SpellRow)
    assert len(rows) == 1
    assert isinstance(rows[0], SpellRow)
    assert rows[0].total_duration_s == 200


def test_add_spell_same_name_different_target(timers: TimersService, spell_book: SpellBook) -> None:
    timers.add_spell(_spell_row(spell_book, group="Joe"))
    timers.add_spell(_spell_row(spell_book, group="Bob"))
    assert len(timers.rows_of(SpellRow)) == 2


def test_tick_removes_expired(timers: TimersService, spell_book: SpellBook) -> None:
    timers.add_spell(_spell_row(spell_book, seconds=10))
    timers.add_spell(_spell_row(spell_book, name="Aegolism", seconds=100))
    expired = timers.tick(T0 + timedelta(seconds=11))
    assert [row.name for row in expired] == ["Clarity"]
    assert [row.name for row in timers.snapshot()] == ["Aegolism"]


def test_counter_increments(timers: TimersService) -> None:
    row = CounterRow(name="Mana Sieve", group=" a mob ", updated_at=T0)
    first = timers.add_counter(row)
    assert first.count == 1
    again = timers.add_counter(CounterRow(name="Mana Sieve", group=" a mob ", updated_at=T0))
    assert again is first
    assert first.count == 2


def test_counter_expires_when_idle(timers: TimersService) -> None:
    timers.add_counter(CounterRow(name="Flame Lick", group="Joe", updated_at=T0))
    assert not timers.tick(T0 + timedelta(minutes=9))
    expired = timers.tick(T0 + timedelta(minutes=11))
    assert [row.name for row in expired] == ["Flame Lick"]


def test_roll_group_reset(timers: TimersService) -> None:
    first = timers.add_roll(
        RollRow(
            name="Joe",
            group="0-333",
            updated_at=T0,
            roll=100,
            max_roll=333,
            ends_at=T0 + timedelta(seconds=30),
            total_duration_s=30,
        )
    )
    later = T0 + timedelta(seconds=20)
    timers.add_roll(
        RollRow(
            name="Bob",
            group="0-333",
            updated_at=later,
            roll=200,
            max_roll=333,
            ends_at=later + timedelta(seconds=30),
            total_duration_s=30,
        )
    )
    assert first.ends_at == later + timedelta(seconds=30)


def test_remove_unambiguous_self(timers: TimersService, spell_book: SpellBook) -> None:
    timers.add_spell(_spell_row(spell_book, name="Clarity", group=YOU_GROUP))
    timers.add_spell(_spell_row(spell_book, name="Clarity", group="Joe"))
    assert timers.try_remove_unambiguous_self(["Clarity"])
    remaining = timers.rows_of(SpellRow)
    assert len(remaining) == 1 and remaining[0].group == "Joe"


def test_remove_unambiguous_other_skips_ambiguous(
    timers: TimersService, spell_book: SpellBook
) -> None:
    timers.add_spell(_spell_row(spell_book, name="Clarity", group="Joe"))
    timers.add_spell(_spell_row(spell_book, name="Clarity", group="Bob"))
    assert not timers.try_remove_unambiguous_other("Clarity")
    assert len(timers.rows_of(SpellRow)) == 2


def test_remove_unambiguous_other(timers: TimersService, spell_book: SpellBook) -> None:
    timers.add_spell(_spell_row(spell_book, name="Clarity", group="Joe"))
    assert timers.try_remove_unambiguous_other("Clarity")
    assert not timers.rows_of(SpellRow)


def test_clear_you_spells(timers: TimersService, spell_book: SpellBook) -> None:
    timers.add_spell(_spell_row(spell_book, group=YOU_GROUP))
    timers.add_spell(_spell_row(spell_book, name="Aegolism", group="Joe"))
    timers.clear_you_spells()
    assert [row.group for row in timers.snapshot()] == ["Joe"]


def test_clear_all_other_spells_keeps_you_and_npc_rows(
    timers: TimersService, spell_book: SpellBook
) -> None:
    # your own buff (YOU group) survives
    timers.add_spell(_spell_row(spell_book, name="Clarity", group=YOU_GROUP))
    # another player's buff is dropped
    timers.add_spell(_spell_row(spell_book, name="Aegolism", group="Joe"))
    # a spell landed on an NPC target (not a player) survives
    timers.add_spell(_spell_row(spell_book, name="Aegolism", group="a mob", is_target_player=False))
    calls: list[int] = []
    timers.on_change.append(lambda: calls.append(1))
    timers.clear_all_other_spells()
    assert sorted(row.group for row in timers.snapshot()) == sorted([YOU_GROUP, "a mob"])
    assert calls == [1]


def test_clear_all_empties_rows_and_notifies(timers: TimersService, spell_book: SpellBook) -> None:
    timers.add_spell(_spell_row(spell_book, group=YOU_GROUP))
    timers.add_spell(_spell_row(spell_book, name="Aegolism", group="Joe"))
    calls: list[int] = []
    timers.on_change.append(lambda: calls.append(1))
    assert timers.clear_all() == 2
    assert timers.snapshot() == []
    assert calls == [1]
    # Already empty: no rows, no notification.
    assert timers.clear_all() == 0
    assert calls == [1]


def test_on_change_fires(timers: TimersService, spell_book: SpellBook) -> None:
    calls: list[int] = []
    timers.on_change.append(lambda: calls.append(1))
    timers.add_spell(_spell_row(spell_book))
    assert calls


def test_export_and_restore_you_spells(timers: TimersService, spell_book: SpellBook) -> None:
    timers.add_spell(_spell_row(spell_book, name="Clarity", group=YOU_GROUP, seconds=120))
    timers.add_spell(_spell_row(spell_book, name="Aegolism", group="Joe", seconds=500))
    timers.add_spell(
        _spell_row(
            spell_book,
            name="Harvest Cooldown",
            spell_name="Harvest",
            group=YOU_GROUP,
            is_cooldown=True,
        )
    )
    now = T0 + timedelta(seconds=20)
    saved = timers.export_you_spells(now)
    assert saved == [YouSpellSnapshot(name="Clarity", total_seconds_left=100)]

    fresh = TimersService()
    fresh.restore_you_spells(saved, now, spell_book, player_class=None, player_level=54)
    rows = fresh.rows_of(SpellRow)
    assert len(rows) == 1
    assert isinstance(rows[0], SpellRow)
    assert rows[0].name == "Clarity"
    assert rows[0].group == YOU_GROUP
    assert rows[0].ends_at == now + timedelta(seconds=100)


def test_grouping_stays_by_target_even_when_targets_exceed_spells(
    timers: TimersService, spell_book: SpellBook
) -> None:
    """Regression: EQTool's adaptive raid regrouping (flip to group-by-spell
    when targets outnumber spells) is deliberately not ported — targets are
    ALWAYS the headers and spells the rows, before and after ticks."""
    for target in ("Joe", "Bob", "Ann"):
        timers.add_spell(_spell_row(spell_book, name="Aegolism", group=target, seconds=100))
    timers.tick(T0 + timedelta(seconds=1))
    rows = timers.rows_of(SpellRow)
    assert {row.group for row in rows} == {"Joe", "Bob", "Ann"}
    assert {row.name for row in rows} == {"Aegolism"}
    # New rows keep target-as-group too.
    added = timers.add_spell(_spell_row(spell_book, name="Aegolism", group="Zed", seconds=100))
    assert added.group == "Zed" and added.name == "Aegolism"
