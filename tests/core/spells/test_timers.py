"""TimersService unit tests (SpellWindowViewModel row bookkeeping)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.core.spells.conftest import T0

from nparseplus.core.spells.spells_us import SpellBook
from nparseplus.core.timers import (
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    CounterRow,
    RollRow,
    SpellRow,
    TimerRow,
    TimersService,
    YouSpellSnapshot,
    group_rows_for_display,
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


def test_post_expiry_persist_keeps_row_then_drops(
    timers: TimersService, spell_book: SpellBook
) -> None:
    """#16: a persisting spell lingers past ends_at (flashing), reports expired
    exactly once, then drops after its window."""
    timers.add_spell(_spell_row(spell_book, name="Clarity", seconds=10, post_expiry_persist_s=30))
    expired_calls: list[list[str]] = []
    change_calls: list[int] = []
    timers.on_expired.append(lambda rows: expired_calls.append([r.name for r in rows]))
    timers.on_change.append(lambda: change_calls.append(1))

    assert timers.tick(T0 + timedelta(seconds=5)) == []  # still live
    # Crosses ends_at: reported once, but KEPT with expired_at stamped.
    just = timers.tick(T0 + timedelta(seconds=11))
    assert [r.name for r in just] == ["Clarity"]
    row = timers.rows_of(SpellRow)[0]
    assert isinstance(row, SpellRow) and row.expired_at == T0 + timedelta(seconds=11)
    # Within the window: kept and NOT re-reported.
    assert timers.tick(T0 + timedelta(seconds=20)) == []
    assert len(timers.rows_of(SpellRow)) == 1
    # Past the window: finally dropped (a change fires, but not on_expired again).
    assert timers.tick(T0 + timedelta(seconds=42)) == []
    assert timers.rows_of(SpellRow) == []
    assert expired_calls == [["Clarity"]]
    # on_change was registered after the add, so only the crossover stamp and
    # the final drop notify — the mid-window ticks stay silent.
    assert change_calls == [1, 1]


def test_post_expiry_row_dismissed_immediately_by_remove(
    timers: TimersService, spell_book: SpellBook
) -> None:
    timers.add_spell(_spell_row(spell_book, name="Clarity", seconds=10, post_expiry_persist_s=30))
    row = timers.tick(T0 + timedelta(seconds=11))[0]
    assert timers.remove_row(row) is True
    assert timers.rows_of(SpellRow) == []


def test_zero_persist_expires_normally(timers: TimersService, spell_book: SpellBook) -> None:
    """Default (no persist) is unchanged: expire and drop on the same tick."""
    timers.add_spell(_spell_row(spell_book, name="Clarity", seconds=10))
    expired = timers.tick(T0 + timedelta(seconds=11))
    assert [r.name for r in expired] == ["Clarity"]
    assert timers.rows_of(SpellRow) == []


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
    """Row STORAGE is always target-keyed, regardless of raid mode — the
    spell-vs-target orientation is a pure display concern
    (``group_rows_for_display``) that never mutates the rows themselves."""
    for target in ("Joe", "Bob", "Ann"):
        timers.add_spell(_spell_row(spell_book, name="Aegolism", group=target, seconds=100))
    timers.tick(T0 + timedelta(seconds=1))
    rows = timers.rows_of(SpellRow)
    assert {row.group for row in rows} == {"Joe", "Bob", "Ann"}
    assert {row.name for row in rows} == {"Aegolism"}
    # New rows keep target-as-group too.
    added = timers.add_spell(_spell_row(spell_book, name="Aegolism", group="Zed", seconds=100))
    assert added.group == "Zed" and added.name == "Aegolism"


# -- display grouping / raid-mode orientation (#17) ---------------------------


def test_display_default_is_target_headed_you_first(spell_book: SpellBook) -> None:
    rows = [
        _spell_row(spell_book, name="Clarity", group=YOU_GROUP),
        _spell_row(spell_book, name="Aegolism", group="Bob"),
        _spell_row(spell_book, name="Aegolism", group="Ann"),
    ]
    groups = group_rows_for_display(rows)
    assert [(g.header, g.orientation) for g in groups] == [
        (YOU_GROUP, "target"),
        ("Ann", "target"),
        ("Bob", "target"),
    ]
    # Under a target header the rows are the spells themselves.
    assert [r.name for r in groups[1].rows] == ["Aegolism"]


def test_display_off_mode_ignores_target_count(spell_book: SpellBook) -> None:
    """With the opt-in off, three targets / one spell stays target-headed."""
    rows = [_spell_row(spell_book, name="Aegolism", group=t) for t in ("Joe", "Bob", "Ann")]
    groups = group_rows_for_display(rows, group_by_spell=False)
    assert all(g.orientation == "target" for g in groups)
    assert [g.header for g in groups] == ["Ann", "Bob", "Joe"]


def test_display_raid_flip_when_targets_exceed_spells(spell_book: SpellBook) -> None:
    rows = [_spell_row(spell_book, name="Aegolism", group=t) for t in ("Joe", "Bob", "Ann")]
    groups = group_rows_for_display(rows, group_by_spell=True)
    assert len(groups) == 1
    (group,) = groups
    assert group.header == "Aegolism" and group.orientation == "spell"
    # The rows are the same objects; each carries its target as ``group``, so
    # the UI renders the target (not the spell) under the spell header.
    assert [r.group for r in group.rows] == ["Ann", "Bob", "Joe"]


def test_display_no_flip_when_spells_not_outnumbered(spell_book: SpellBook) -> None:
    """Two targets, two spells → not outnumbered → stays target-headed."""
    rows = [
        _spell_row(spell_book, name="Aegolism", group="Joe"),
        _spell_row(spell_book, name="Clarity", group="Bob"),
    ]
    groups = group_rows_for_display(rows, group_by_spell=True)
    assert all(g.orientation == "target" for g in groups)
    assert {g.header for g in groups} == {"Joe", "Bob"}


def test_display_you_group_never_flips(spell_book: SpellBook) -> None:
    rows = [
        _spell_row(spell_book, name="Clarity", group=YOU_GROUP),
        _spell_row(spell_book, name="Aegolism", group=YOU_GROUP),
        *[_spell_row(spell_book, name="Aegolism", group=t) for t in ("Joe", "Bob", "Ann")],
    ]
    groups = group_rows_for_display(rows, group_by_spell=True)
    assert groups[0].header == YOU_GROUP and groups[0].orientation == "target"
    # Your own two buffs stay listed under YOU; only the other players flip.
    assert {r.name for r in groups[0].rows} == {"Clarity", "Aegolism"}
    assert [(g.header, g.orientation) for g in groups[1:]] == [("Aegolism", "spell")]
    assert [r.group for r in groups[1].rows] == ["Ann", "Bob", "Joe"]


def test_display_detrimental_and_cooldown_stay_target_headed(spell_book: SpellBook) -> None:
    rows = [
        _spell_row(spell_book, name="Aegolism", group="Joe", detrimental=True),
        _spell_row(spell_book, name="Aegolism", group="Bob", detrimental=True),
        _spell_row(spell_book, name="Aegolism", group="Ann", detrimental=True),
    ]
    groups = group_rows_for_display(rows, group_by_spell=True)
    assert all(g.orientation == "target" for g in groups)


def test_display_npc_targets_stay_target_headed(spell_book: SpellBook) -> None:
    """Only player targets flip; NPC-target spells never do."""
    rows = [
        _spell_row(spell_book, name="Aegolism", group=t, is_target_player=False)
        for t in ("a mob", "a bat", "a rat")
    ]
    groups = group_rows_for_display(rows, group_by_spell=True)
    assert all(g.orientation == "target" for g in groups)


def test_display_timer_sections_stay_target_headed(spell_book: SpellBook) -> None:
    rows = [_spell_row(spell_book, name="Aegolism", group=t) for t in ("Joe", "Bob", "Ann")]
    rows.append(
        TimerRow(
            name="Pull",
            group=TRIGGER_TIMER_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=30),
            total_duration_s=30,
        )
    )
    groups = group_rows_for_display(rows, group_by_spell=True)
    by_header = {g.header: g for g in groups}
    assert by_header["Aegolism"].orientation == "spell"
    assert by_header[TRIGGER_TIMER_GROUP].orientation == "target"


def test_display_midfight_target_recognition_has_no_stuck_header(spell_book: SpellBook) -> None:
    """The acceptance case: a target recognized mid-fight (is_target_player
    flipped AFTER the row was added) re-groups cleanly, leaving no stale
    header. Because orientation is recomputed each call from the rows, the
    old global-flag desync (stuck spell-header) cannot happen."""
    players = [_spell_row(spell_book, name="Aegolism", group=t) for t in ("Joe", "Bob")]
    npc = _spell_row(spell_book, name="Aegolism", group="Xanth", is_target_player=False)
    rows = [*players, npc]

    before = group_rows_for_display(rows, group_by_spell=True)
    # 2 players (>1 spell) flip; the not-yet-recognized target stays its own header.
    assert {(g.header, g.orientation) for g in before} == {
        ("Aegolism", "spell"),
        ("Xanth", "target"),
    }

    # /who resolves Xanth as a player — flip the per-row flag and re-group.
    npc.is_target_player = True
    after = group_rows_for_display(rows, group_by_spell=True)
    assert [(g.header, g.orientation) for g in after] == [("Aegolism", "spell")]
    assert [r.group for r in after[0].rows] == ["Bob", "Joe", "Xanth"]
    # No leftover target-headed group — nothing is stuck.
    assert all(g.orientation == "spell" for g in after)
