"""TimersService unit tests (SpellWindowViewModel row bookkeeping)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from tests.core.spells.conftest import T0

from nparseplus.core.spells.spells_us import SpellBook
from nparseplus.core.timers import (
    COUNTER_IDLE_EXPIRY,
    MOB_TIMER_GROUP,
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    CounterRow,
    RespawnTimerSnapshot,
    RollRow,
    Row,
    SelfCooldownSnapshot,
    SelfCounterSnapshot,
    SpellRow,
    TimerRow,
    TimersService,
    YouSpellSnapshot,
    countdown_target,
    expires_at,
    fraction_remaining,
    group_rows_for_display,
    has_pop_window,
    in_pop_window,
    seconds_left,
    snap_to_second,
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


# -- one-second display grid ---------------------------------------------------


@pytest.mark.parametrize("micros", [0, 1, 499_999, 500_000, 999_999])
def test_snap_to_second_truncates(micros: int) -> None:
    """Truncation, not round-to-nearest: matches what the log timestamp
    already does, so a wall-clock anchor can never push the end time past the
    second the caller asked for."""
    snapped = snap_to_second(T0.replace(microsecond=micros))
    assert snapped.microsecond == 0
    assert snapped.second == T0.second


def test_snapped_timer_opens_on_its_nominal_duration() -> None:
    """Regression (caught live): rounding to nearest made a 45 s timer render
    00:46 on its first frame whenever the anchor sat past the half-second."""
    for micros in (0, 300_000, 500_000, 900_000):
        started = T0.replace(microsecond=micros)
        row = TimerRow(
            name="Custom",
            group=TRIGGER_TIMER_GROUP,
            updated_at=started,
            ends_at=started + timedelta(seconds=45),
            total_duration_s=45.0,
        )
        assert seconds_left(row.ends_at, started) == 45


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        (30.0, 30),  # exact boundary keeps the whole value
        (29.999, 30),  # ceiling: a hair under 30 still reads 30
        (0.001, 1),  # any time left at all reads at least 1
        (0.0, 0),
        (-5.0, 0),  # clamped, never negative
    ],
)
def test_seconds_left_ceils_and_clamps(remaining: float, expected: int) -> None:
    assert seconds_left(T0 + timedelta(seconds=remaining), T0) == expected


def test_ends_at_is_snapped_on_construction(spell_book: SpellBook) -> None:
    """A wall-clock producer (trigger timers, PigParse rolls, restored buffs)
    hands us a fractional anchor; the row puts it back on the grid."""
    row = _spell_row(spell_book)
    row_off_grid = SpellRow(
        name="Clarity",
        group=YOU_GROUP,
        updated_at=T0,
        spell=row.spell,
        ends_at=T0 + timedelta(seconds=100, microseconds=372_000),
        total_duration_s=100.0,
    )
    assert row_off_grid.ends_at == T0 + timedelta(seconds=100)


def test_ends_at_is_snapped_on_assignment(spell_book: SpellBook) -> None:
    """validate_assignment covers the in-place restarts that bypass add_*:
    TriggerTimerSink.add_timer, the shared-trigger restart, add_roll's group
    reset."""
    row = _spell_row(spell_book)
    row.ends_at = T0 + timedelta(seconds=42, microseconds=800_000)
    assert row.ends_at == T0 + timedelta(seconds=42)


def test_rows_from_different_clocks_step_on_the_same_boundary(spell_book: SpellBook) -> None:
    """The whole point: a log-anchored row and a wall-clock-anchored one
    started mid-second must change their digit at the same instant.

    They need not show the *same* number (a timer started 0.6 s later really
    does end a second later) — what matters is that neither flips mid-second,
    so the window steps once, together, on the second.
    """
    log_anchored = _spell_row(spell_book, seconds=60)
    wall_clock = TimerRow(
        name="Custom",
        group=TRIGGER_TIMER_GROUP,
        updated_at=T0,
        ends_at=T0.replace(microsecond=613_000) + timedelta(seconds=60),
        total_duration_s=60.0,
    )
    for row in (log_anchored, wall_clock):
        base = T0 + timedelta(seconds=30)
        # Every sub-second sample inside one wall-clock second reads alike...
        within = {
            seconds_left(row.ends_at, base + timedelta(microseconds=u))
            for u in range(0, 1_000_000, 50_000)
        }
        assert len(within) == 1
        # ...and the next second is exactly one lower.
        assert seconds_left(row.ends_at, base + timedelta(seconds=1)) == within.pop() - 1


def test_unsnapped_anchor_would_flip_mid_second(spell_book: SpellBook) -> None:
    """Guards the reason the validator exists: without the snap, a fractional
    anchor changes its digit partway through the second, out of step with
    every other row."""
    off_grid = T0.replace(microsecond=613_000) + timedelta(seconds=60)
    base = T0 + timedelta(seconds=30)
    within = {
        seconds_left(off_grid, base + timedelta(microseconds=u))
        for u in range(0, 1_000_000, 50_000)
    }
    assert len(within) == 2  # it steps somewhere inside the second


def test_export_you_spells_round_trips_without_shedding_a_second(
    timers: TimersService, spell_book: SpellBook
) -> None:
    timers.add_spell(_spell_row(spell_book, seconds=100))
    now = T0 + timedelta(seconds=10, microseconds=400_000)
    saved = timers.export_you_spells(now)
    assert saved == [YouSpellSnapshot(name="Clarity", total_seconds_left=90)]


# -- fraction_remaining (drives the bar value AND its color fade) --------------


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0, 1.0),  # fresh row is full
        (25, 0.75),
        (50, 0.5),
        (100, 0.0),  # exactly expired
        (400, 0.0),  # clamped, never negative
        (-30, 1.0),  # clamped, never over 1 (a row anchored in the future)
    ],
)
def test_fraction_remaining_over_a_rows_life(
    spell_book: SpellBook, elapsed: float, expected: float
) -> None:
    row = _spell_row(spell_book, seconds=100)
    assert fraction_remaining(row, T0 + timedelta(seconds=elapsed)) == pytest.approx(expected)


def test_fraction_remaining_is_full_without_a_countdown() -> None:
    """CounterRow has no ends_at — "no progress information" reads as full."""
    counter = CounterRow(name="Resisted", group=YOU_GROUP, updated_at=T0, count=3)
    assert fraction_remaining(counter, T0 + timedelta(hours=1)) == 1.0


def test_fraction_remaining_survives_a_zero_duration_row() -> None:
    row = TimerRow(
        name="t", group=TRIGGER_TIMER_GROUP, updated_at=T0, ends_at=T0, total_duration_s=0.0
    )
    assert fraction_remaining(row, T0) == 0.0


# -- the character's own rows (#120) -------------------------------------------


def _self_rows(timers: TimersService, spell_book: SpellBook) -> None:
    """One row of each kind camping hides, plus the world rows it must not."""
    timers.add_spell(_spell_row(spell_book, name="Clarity", group=YOU_GROUP, seconds=120))
    timers.add_spell(
        _spell_row(
            spell_book,
            name="Harvest Cooldown",
            spell_name="Harvest",
            group=YOU_GROUP,
            seconds=600,
            is_cooldown=True,
        )
    )
    timers.add_timer(
        TimerRow(
            name="Lay on Hands",
            group=YOU_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=3600),
            total_duration_s=3600.0,
        )
    )
    timers.add_counter(CounterRow(name="Selo's casts", group=YOU_GROUP, updated_at=T0))
    # World state: another player's buff, a custom timer, a roll window.
    timers.add_spell(_spell_row(spell_book, name="Aegolism", group="Joe", seconds=500))
    timers.add_timer(
        TimerRow(
            name="Custom",
            group=TRIGGER_TIMER_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=90),
            total_duration_s=90.0,
        )
    )
    timers.add_roll(
        RollRow(
            name="Tester",
            group=" Random -- 100",
            updated_at=T0,
            roll=42,
            max_roll=100,
            ends_at=T0 + timedelta(seconds=180),
            total_duration_s=180.0,
        )
    )


def test_remove_self_rows_spares_everything_outside_you_group(
    timers: TimersService, spell_book: SpellBook
) -> None:
    _self_rows(timers, spell_book)
    calls: list[int] = []
    timers.on_change.append(lambda: calls.append(1))

    assert timers.remove_self_rows() == 4
    assert sorted(row.name for row in timers.snapshot()) == ["Aegolism", "Custom", "Tester"]
    assert calls == [1]
    # Nothing left to remove: no notification either.
    assert timers.remove_self_rows() == 0
    assert calls == [1]


def test_export_self_cooldowns_covers_both_row_shapes(
    timers: TimersService, spell_book: SpellBook
) -> None:
    _self_rows(timers, spell_book)
    saved = timers.export_self_cooldowns(T0)
    assert [(s.name, s.spell_name) for s in saved] == [
        ("Harvest Cooldown", "Harvest"),
        ("Lay on Hands", ""),
    ]
    # Absolute ends, not seconds-left: a reuse timer runs while you are away.
    assert saved[1].ends_at == T0 + timedelta(seconds=3600)


def test_export_self_cooldowns_skips_buffs_and_other_groups(
    timers: TimersService, spell_book: SpellBook
) -> None:
    _self_rows(timers, spell_book)
    names = [s.name for s in timers.export_self_cooldowns(T0)]
    assert "Clarity" not in names  # a buff belongs to you_spells (frozen)
    assert "Custom" not in names  # a custom timer is world state


def test_export_self_cooldowns_drops_what_already_came_up(
    timers: TimersService, spell_book: SpellBook
) -> None:
    _self_rows(timers, spell_book)
    assert timers.export_self_cooldowns(T0 + timedelta(seconds=1200)) == [
        SelfCooldownSnapshot(
            name="Lay on Hands", ends_at=T0 + timedelta(seconds=3600), total_duration_s=3600.0
        )
    ]


def test_restore_self_cooldowns_rebuilds_the_shape_it_saved(
    timers: TimersService, spell_book: SpellBook
) -> None:
    _self_rows(timers, spell_book)
    saved = timers.export_self_cooldowns(T0)

    fresh = TimersService()
    fresh.restore_self_cooldowns(saved, T0 + timedelta(seconds=300), spell_book)
    rows = fresh.snapshot()
    assert [type(r).__name__ for r in rows] == ["SpellRow", "TimerRow"]
    assert isinstance(rows[0], SpellRow) and rows[0].is_cooldown
    # Absolute ends survive the round trip: the 300 s came off both.
    assert rows[0].ends_at == T0 + timedelta(seconds=600)
    assert rows[1].ends_at == T0 + timedelta(seconds=3600)
    assert {r.group for r in rows} == {YOU_GROUP}


def test_restore_self_cooldowns_drops_what_came_up_while_away(
    timers: TimersService, spell_book: SpellBook
) -> None:
    saved = [
        SelfCooldownSnapshot(
            name="Harm Touch", ends_at=T0 + timedelta(seconds=60), total_duration_s=4320.0
        )
    ]
    calls: list[int] = []
    timers.on_change.append(lambda: calls.append(1))
    timers.restore_self_cooldowns(saved, T0 + timedelta(seconds=120), spell_book)
    assert timers.snapshot() == []
    assert calls == []


def test_restore_self_cooldown_falls_back_to_a_timer_row_for_an_unknown_spell(
    timers: TimersService, spell_book: SpellBook
) -> None:
    """A spell the loaded database no longer has must not lose the countdown."""
    timers.restore_self_cooldowns(
        [
            SelfCooldownSnapshot(
                name="Gate Cooldown",
                ends_at=T0 + timedelta(seconds=60),
                total_duration_s=60.0,
                spell_name="No Such Spell",
            )
        ],
        T0,
        spell_book,
    )
    rows = timers.snapshot()
    assert [type(r).__name__ for r in rows] == ["TimerRow"]
    assert rows[0].name == "Gate Cooldown"


def test_export_and_restore_self_counters(timers: TimersService, spell_book: SpellBook) -> None:
    _self_rows(timers, spell_book)
    timers.add_counter(CounterRow(name="Selo's casts", group=YOU_GROUP, updated_at=T0))
    # Another target's counter is not this character's row.
    timers.add_counter(CounterRow(name="Resisted", group="Joe", updated_at=T0))

    saved = timers.export_self_counters()
    assert saved == [SelfCounterSnapshot(name="Selo's casts", count=2, updated_at=T0)]

    fresh = TimersService()
    fresh.restore_self_counters(saved, T0 + timedelta(minutes=5))
    rows = fresh.rows_of(CounterRow)
    assert [(r.name, r.count, r.group) for r in rows] == [("Selo's casts", 2, YOU_GROUP)]
    # The stamp is preserved, so tick() ages it exactly as it would have.
    assert rows[0].updated_at == T0


def test_restore_self_counters_drops_an_idle_expired_one(timers: TimersService) -> None:
    saved = [SelfCounterSnapshot(name="Selo's casts", count=3, updated_at=T0)]
    timers.restore_self_counters(saved, T0 + COUNTER_IDLE_EXPIRY + timedelta(seconds=1))
    assert timers.snapshot() == []


# -- variable respawn ("pop") windows (#125) -----------------------------------

BASE_S = 400.0
WINDOW_S = 900.0


def _window_row(
    name: str = "--Dead-- Trakanon",
    base_s: float = BASE_S,
    window_s: float = WINDOW_S,
    started_at: datetime = T0,
    **kwargs: object,
) -> TimerRow:
    """A TOD-anchored respawn row: base countdown, then a pop window."""
    ends_at = started_at + timedelta(seconds=base_s)
    return TimerRow(
        name=name,
        group=MOB_TIMER_GROUP,
        updated_at=started_at,
        ends_at=ends_at,
        total_duration_s=base_s,
        window_ends_at=ends_at + timedelta(seconds=window_s),
        **kwargs,  # type: ignore[arg-type]
    )


def test_window_row_opens_at_its_base_end_without_expiring(timers: TimersService) -> None:
    """The crossover is an opening, not an expiry: the row stays on screen."""
    opened: list[list[Row]] = []
    expired: list[list[Row]] = []
    timers.on_window_open.append(opened.append)
    timers.on_expired.append(expired.append)
    row = timers.add_timer(_window_row())

    assert timers.tick(T0 + timedelta(seconds=BASE_S - 1)) == []
    assert opened == []
    assert row.window_opened_at is None

    at_open = T0 + timedelta(seconds=BASE_S)
    assert timers.tick(at_open) == []
    assert [[r.name for r in batch] for batch in opened] == [["--Dead-- Trakanon"]]
    assert expired == []
    assert row.window_opened_at == at_open
    assert timers.snapshot() == [row]


def test_window_opens_exactly_once(timers: TimersService) -> None:
    opened: list[Row] = []
    timers.on_window_open.append(opened.extend)
    row = timers.add_timer(_window_row())
    for elapsed in range(int(BASE_S), int(BASE_S) + 5):
        timers.tick(T0 + timedelta(seconds=elapsed))
    assert opened == [row]
    assert row.window_opened_at == T0 + timedelta(seconds=BASE_S)


def test_window_row_expires_when_the_window_closes(timers: TimersService) -> None:
    expired: list[Row] = []
    timers.on_expired.append(expired.extend)
    row = timers.add_timer(_window_row())
    timers.tick(T0 + timedelta(seconds=BASE_S))

    # Inside the window: still present, still not expired.
    assert timers.tick(T0 + timedelta(seconds=BASE_S + WINDOW_S - 1)) == []
    assert expired == []
    assert timers.snapshot() == [row]

    at_close = T0 + timedelta(seconds=BASE_S + WINDOW_S)
    assert timers.tick(at_close) == [row]
    assert expired == [row]
    assert timers.snapshot() == []


def test_a_row_created_past_both_times_opens_then_expires_in_one_tick(
    timers: TimersService,
) -> None:
    order: list[str] = []
    timers.on_window_open.append(lambda rows: order.append(f"open:{rows[0].name}"))
    timers.on_expired.append(lambda rows: order.append(f"expire:{rows[0].name}"))
    row = timers.add_timer(_window_row(name="--Dead-- Faydedar"))

    assert timers.tick(T0 + timedelta(seconds=BASE_S + WINDOW_S + 60)) == [row]
    assert order == ["open:--Dead-- Faydedar", "expire:--Dead-- Faydedar"]
    assert row.window_opened_at is not None


def test_the_crossover_notifies_even_though_nothing_was_dropped(timers: TimersService) -> None:
    """The row changed (it is now in its window) and the UI has to repaint."""
    changes: list[int] = []
    timers.add_timer(_window_row())
    timers.on_change.append(lambda: changes.append(1))
    timers.tick(T0 + timedelta(seconds=BASE_S))
    assert changes == [1]
    # A later tick inside the window changes nothing.
    timers.tick(T0 + timedelta(seconds=BASE_S + 1))
    assert changes == [1]


def test_a_plain_timer_row_is_untouched_by_the_window_branch(timers: TimersService) -> None:
    opened: list[Row] = []
    timers.on_window_open.append(opened.extend)
    row = timers.add_timer(
        TimerRow(
            name="Custom",
            group=TRIGGER_TIMER_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=30),
            total_duration_s=30.0,
        )
    )
    assert timers.tick(T0 + timedelta(seconds=31)) == [row]
    assert opened == []


def test_a_persisting_spell_and_a_window_timer_coexist(
    timers: TimersService, spell_book: SpellBook
) -> None:
    """Regression: the window branch sits between the ends_at guard and the
    post-expiry persist branch and must disturb neither."""
    opened: list[Row] = []
    expired: list[Row] = []
    timers.on_window_open.append(opened.extend)
    timers.on_expired.append(expired.extend)
    spell = timers.add_spell(_spell_row(spell_book, seconds=BASE_S, post_expiry_persist_s=30.0))
    window = timers.add_timer(_window_row())

    # Both cross their ends_at on the same tick: the spell "expires" (and
    # lingers as a rebuff prompt), the window row merely opens.
    at_open = T0 + timedelta(seconds=BASE_S)
    assert timers.tick(at_open) == [spell]
    assert opened == [window]
    assert expired == [spell]
    assert spell.expired_at == at_open
    assert window.window_opened_at == at_open
    assert [r.name for r in timers.snapshot()] == [spell.name, window.name]

    # The spell's persist window elapses first; the pop window outlives it.
    timers.tick(T0 + timedelta(seconds=BASE_S + 31))
    assert timers.snapshot() == [window]
    assert expired == [spell]


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0, 1.0),  # phase 1: the base respawn is the whole bar
        (200, 0.5),
        (399, pytest.approx(0.0025)),
        (400, 1.0),  # phase 2 opens: the bar refills over the window itself
        (400 + 450, 0.5),
        (400 + 900, 0.0),  # window closed
        (400 + 5000, 0.0),  # clamped
    ],
)
def test_fraction_remaining_is_phase_aware(elapsed: float, expected: float) -> None:
    row = _window_row()
    assert fraction_remaining(row, T0 + timedelta(seconds=elapsed)) == pytest.approx(expected)


def test_countdown_target_and_expires_at_follow_the_phase() -> None:
    row = _window_row()
    opens_at = T0 + timedelta(seconds=BASE_S)
    closes_at = opens_at + timedelta(seconds=WINDOW_S)

    assert countdown_target(row, T0) == opens_at
    assert countdown_target(row, opens_at - timedelta(seconds=1)) == opens_at
    assert countdown_target(row, opens_at) == closes_at
    assert countdown_target(row, closes_at + timedelta(minutes=1)) == closes_at
    # expires_at has no phase: a window row is done when its window closes.
    assert expires_at(row) == closes_at


def test_the_helpers_are_inert_for_rows_without_a_window(spell_book: SpellBook) -> None:
    plain = TimerRow(
        name="Custom",
        group=TRIGGER_TIMER_GROUP,
        updated_at=T0,
        ends_at=T0 + timedelta(seconds=30),
        total_duration_s=30.0,
    )
    counter = CounterRow(name="Resisted", group=YOU_GROUP, updated_at=T0)
    for row in (plain, _spell_row(spell_book), counter):
        assert has_pop_window(row) is False
        assert in_pop_window(row, T0 + timedelta(days=1)) is False
    assert expires_at(plain) == plain.ends_at
    assert countdown_target(plain, T0 + timedelta(days=1)) == plain.ends_at
    assert expires_at(counter) is None
    assert countdown_target(counter, T0) is None


def test_in_pop_window_stays_true_for_the_frame_after_it_closes() -> None:
    """So a closing row renders 00:00 in its window presentation rather than
    snapping back to phase 1 on its way off the screen."""
    row = _window_row()
    closes_at = T0 + timedelta(seconds=BASE_S + WINDOW_S)
    assert in_pop_window(row, closes_at) is True
    assert in_pop_window(row, closes_at + timedelta(seconds=5)) is True


def test_window_ends_at_is_snapped_on_construction_and_assignment() -> None:
    row = TimerRow(
        name="--Dead-- Trakanon",
        group=MOB_TIMER_GROUP,
        updated_at=T0,
        ends_at=T0 + timedelta(seconds=400),
        total_duration_s=400.0,
        window_ends_at=T0 + timedelta(seconds=1300, microseconds=742_000),
    )
    assert row.window_ends_at == T0 + timedelta(seconds=1300)
    row.window_ends_at = T0 + timedelta(seconds=900, microseconds=999_999)
    assert row.window_ends_at == T0 + timedelta(seconds=900)


def test_window_ends_at_can_be_cleared() -> None:
    """The validator has to pass None through, or validate_assignment raises
    the moment a caller drops the window."""
    row = _window_row()
    row.window_ends_at = None
    assert row.window_ends_at is None
    assert has_pop_window(row) is False


def test_the_window_must_follow_the_base_end() -> None:
    with pytest.raises(ValidationError):
        TimerRow(
            name="--Dead-- Trakanon",
            group=MOB_TIMER_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=400),
            total_duration_s=400.0,
            window_ends_at=T0 + timedelta(seconds=400),
        )
    row = _window_row()
    with pytest.raises(ValidationError):
        row.window_ends_at = T0


def test_the_window_stamp_is_not_snapped() -> None:
    """It is an observation of tick time, like SpellRow.expired_at — not an
    anchor anything counts down to."""
    row = _window_row()
    at_open = T0 + timedelta(seconds=BASE_S, microseconds=613_000)
    TimersService().add_timer(row)
    row.window_opened_at = at_open
    assert row.window_opened_at == at_open


def test_export_keeps_a_row_whose_window_is_open(timers: TimersService) -> None:
    """Camping mid-window used to drop the row: its ends_at is in the past."""
    row = timers.add_timer(_window_row())
    opens_at = T0 + timedelta(seconds=BASE_S)
    mid_window = opens_at + timedelta(seconds=60)
    timers.tick(opens_at)
    timers.tick(mid_window)

    saved = timers.export_respawn_timers(MOB_TIMER_GROUP, mid_window)
    assert saved == [
        RespawnTimerSnapshot(
            name="--Dead-- Trakanon",
            ends_at=row.ends_at,
            total_duration_s=BASE_S,
            window_ends_at=row.window_ends_at,
            window_opened_at=opens_at,
        )
    ]
    # And is dropped once the window has actually closed.
    closed = T0 + timedelta(seconds=BASE_S + WINDOW_S)
    assert timers.export_respawn_timers(MOB_TIMER_GROUP, closed) == []


def test_restore_preserves_the_window_stamp_instead_of_re_stamping(
    timers: TimersService,
) -> None:
    """A character swap does remove_group + restore; re-stamping would re-fire
    on_window_open — and its event and its speech — every single time."""
    opened: list[Row] = []
    timers.on_window_open.append(opened.extend)
    opens_at = T0 + timedelta(seconds=BASE_S)
    timers.restore_respawn_timers(
        [
            RespawnTimerSnapshot(
                name="--Dead-- Trakanon",
                ends_at=opens_at,
                total_duration_s=BASE_S,
                window_ends_at=opens_at + timedelta(seconds=WINDOW_S),
                window_opened_at=opens_at,
            )
        ],
        MOB_TIMER_GROUP,
        opens_at + timedelta(seconds=60),
    )
    rows = timers.rows_of(TimerRow)
    assert len(rows) == 1
    assert isinstance(rows[0], TimerRow)
    assert rows[0].window_opened_at == opens_at

    timers.tick(opens_at + timedelta(seconds=61))
    assert opened == []


def test_restore_drops_a_window_that_closed_while_away(timers: TimersService) -> None:
    opens_at = T0 + timedelta(seconds=BASE_S)
    timers.restore_respawn_timers(
        [
            RespawnTimerSnapshot(
                name="--Dead-- Trakanon",
                ends_at=opens_at,
                total_duration_s=BASE_S,
                window_ends_at=opens_at + timedelta(seconds=WINDOW_S),
                window_opened_at=opens_at,
            )
        ],
        MOB_TIMER_GROUP,
        opens_at + timedelta(seconds=WINDOW_S + 1),
    )
    assert timers.snapshot() == []


def test_restore_keeps_a_row_whose_base_end_passed_but_window_has_not(
    timers: TimersService,
) -> None:
    """The plain-timer rule (ends_at <= now -> drop) would throw this away."""
    opens_at = T0 + timedelta(seconds=BASE_S)
    timers.restore_respawn_timers(
        [
            RespawnTimerSnapshot(
                name="--Dead-- Trakanon",
                ends_at=opens_at,
                total_duration_s=BASE_S,
                window_ends_at=opens_at + timedelta(seconds=WINDOW_S),
                window_opened_at=opens_at,
            )
        ],
        MOB_TIMER_GROUP,
        opens_at + timedelta(seconds=60),
    )
    assert [r.name for r in timers.snapshot()] == ["--Dead-- Trakanon"]


def test_restore_degrades_an_inconsistent_saved_window_instead_of_raising(
    timers: TimersService,
) -> None:
    """settings.json is user-editable; one bad pair must not abort the restore
    of every entry behind it."""
    ends_at = T0 + timedelta(seconds=BASE_S)
    timers.restore_respawn_timers(
        [
            RespawnTimerSnapshot(
                name="--Dead-- Bad",
                ends_at=ends_at,
                total_duration_s=BASE_S,
                # Before the base end — the row model rejects it outright.
                window_ends_at=ends_at - timedelta(seconds=30),
                window_opened_at=T0,
            ),
            RespawnTimerSnapshot(name="--Dead-- Good", ends_at=ends_at, total_duration_s=BASE_S),
        ],
        MOB_TIMER_GROUP,
        T0,
    )
    rows = timers.rows_of(TimerRow)
    assert [r.name for r in rows] == ["--Dead-- Bad", "--Dead-- Good"]
    bad = rows[0]
    assert isinstance(bad, TimerRow)
    assert bad.window_ends_at is None
    assert bad.window_opened_at is None


def test_restore_degrades_a_window_that_snaps_onto_the_base_end(
    timers: TimersService,
) -> None:
    """Sub-second apart in the file, equal on the one-second grid the row uses."""
    ends_at = T0 + timedelta(seconds=BASE_S)
    timers.restore_respawn_timers(
        [
            RespawnTimerSnapshot(
                name="--Dead-- Trakanon",
                ends_at=ends_at,
                total_duration_s=BASE_S,
                window_ends_at=ends_at + timedelta(microseconds=400_000),
            )
        ],
        MOB_TIMER_GROUP,
        T0,
    )
    rows = timers.rows_of(TimerRow)
    assert len(rows) == 1
    assert isinstance(rows[0], TimerRow)
    assert rows[0].window_ends_at is None
