"""Correcting an ambiguous guess (#177).

The matcher only guesses when several spells share one cast message, so a wrong
guess is a normal outcome rather than a defect to eliminate. These cover the
two halves of the way out: the candidates the guess passed over reaching the
timer row, and ``TimersService.respell_row`` swapping the row to one of them
without restarting the countdown.
"""

from __future__ import annotations

from datetime import timedelta

from tests.core.spells.conftest import T0, make_line

from nparseplus.config.settings import Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import SpellCastOnYouEvent
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.parsers.spell_cast_on_other import SpellCastOnOtherParser
from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.durations import (
    DISCIPLINE_DURATION_OVERRIDES_S,
    base_timer_duration_seconds,
    get_duration_seconds,
)
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimersService

SPIRIT_OF_WOLF = "You feel the spirit of wolf enter you."


def _rig(ctx) -> TimersService:
    settings = Settings()
    ctx.settings = settings
    timers = TimersService()
    SpellTimerHandler(ctx.bus, ctx.player, ctx.spells, timers, spell_settings=settings.spellwindow)
    return timers


def _spell_rows(timers: TimersService) -> list[SpellRow]:
    return [r for r in timers.snapshot() if isinstance(r, SpellRow)]


# -- the candidates reach the row ----------------------------------------------


def test_an_ambiguous_cast_on_you_carries_its_alternatives(ctx) -> None:
    ctx.player.player_class = PlayerClass.SHAMAN
    ctx.player.level = 40
    timers = _rig(ctx)
    assert YouFinishCastingParser().handle(make_line(SPIRIT_OF_WOLF), ctx)

    (row,) = _spell_rows(timers)
    assert row.name == "Spirit of Wolf"
    assert [s.name for s in row.alternatives] == ["Pack Spirit"]


def test_an_unambiguous_cast_carries_none(ctx) -> None:
    """No submenu on a row that was never a guess."""
    ctx.player.player_class = PlayerClass.CLERIC
    ctx.player.level = 40
    timers = _rig(ctx)
    assert YouFinishCastingParser().handle(make_line("You feel cleansed."), ctx)
    assert all(row.alternatives == [] for row in _spell_rows(timers))


def test_an_observed_cast_carries_its_alternatives(ctx) -> None:
    """The bystander path guesses too, and has the whole candidate tuple to
    hand — ``SpellCastOnOtherEvent.spells`` — so it needs no event change."""
    ctx.player.player_class = PlayerClass.WARRIOR
    ctx.player.level = 40
    timers = _rig(ctx)
    assert SpellCastOnOtherParser().handle(
        make_line("Joe is surrounded by a brief lupine aura."), ctx
    )
    (row,) = _spell_rows(timers)
    assert row.alternatives, "an ambiguous observed cast should offer a correction"
    assert row.name not in {s.name for s in row.alternatives}


def test_the_event_defaults_to_no_alternatives(spell_book) -> None:
    """Additive on EventModels.cs: nothing already publishing this event has to
    learn about the field."""
    event = SpellCastOnYouEvent(
        timestamp=T0,
        line="You feel cleansed.",
        line_number=1,
        spell=spell_book.spell_by_name("Cleanse"),
    )
    assert event.alternatives == ()


def test_cooldown_rows_get_no_alternatives(ctx, spell_book) -> None:
    """A cooldown is keyed to the recast the player actually started, so
    relabelling one would misreport which gem is down."""
    timers = _rig(ctx)
    handler = SpellTimerHandler(
        ctx.bus, ctx.player, ctx.spells, timers, spell_settings=Settings().spellwindow
    )
    divine_aura = spell_book.spell_by_name("Divine Aura")  # in SPELLS_THAT_NEED_TIMERS
    other = spell_book.spell_by_name("Levitate")
    assert divine_aura is not None and other is not None
    handler.handle_spell(divine_aura, " Joe ", 0, T0, [other])

    cooldowns = [row for row in _spell_rows(timers) if row.is_cooldown]
    assert cooldowns, "Divine Aura should have produced a cooldown row"
    assert all(row.alternatives == [] for row in cooldowns)
    # The buff row itself still carries them.
    buffs = [row for row in _spell_rows(timers) if not row.is_cooldown]
    assert buffs and [s.name for s in buffs[0].alternatives] == ["Levitate"]


# -- the swap ------------------------------------------------------------------


def _row_for(spell_book, chosen: str, alternative: str, elapsed_s: int = 0) -> SpellRow:
    spell = spell_book.spell_by_name(chosen)
    other = spell_book.spell_by_name(alternative)
    assert spell is not None and other is not None
    duration = float(get_duration_seconds(spell, PlayerClass.SHAMAN, 40))
    return SpellRow(
        name=spell.name,
        group=" Joe ",
        updated_at=T0,
        is_target_player=False,
        spell=spell,
        ends_at=T0 + timedelta(seconds=duration - elapsed_s),
        total_duration_s=duration,
        alternatives=[other],
    )


def test_respell_keeps_the_elapsed_time(spell_book) -> None:
    """The buff has been running since it landed; the correction changes what
    it is called and how long it lasts, not when it started."""
    timers = TimersService()
    row = timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    started_at = row.ends_at - timedelta(seconds=row.total_duration_s)

    sow = spell_book.spell_by_name("Spirit of Wolf")
    new = timers.respell_row(row, sow, PlayerClass.SHAMAN, 40)

    assert new is not None and new.name == "Spirit of Wolf"
    assert new.ends_at - timedelta(seconds=new.total_duration_s) == started_at
    assert new.total_duration_s == float(get_duration_seconds(sow, PlayerClass.SHAMAN, 40))
    assert [r.name for r in _spell_rows(timers)] == ["Spirit of Wolf"]


def test_respell_is_reversible(spell_book) -> None:
    """The chosen spell joins the alternatives so the user can change back, and
    a third candidate stays reachable."""
    timers = TimersService()
    row = timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    sow = spell_book.spell_by_name("Spirit of Wolf")
    new = timers.respell_row(row, sow, PlayerClass.SHAMAN, 40)
    assert new is not None
    assert [s.name for s in new.alternatives] == ["Pack Spirit"]

    back = timers.respell_row(new, spell_book.spell_by_name("Pack Spirit"), PlayerClass.SHAMAN, 40)
    assert back is not None and back.name == "Pack Spirit"
    assert [s.name for s in back.alternatives] == ["Spirit of Wolf"]


def test_respell_preserves_the_rows_identity(spell_book) -> None:
    timers = TimersService()
    row = timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    new = timers.respell_row(
        row, spell_book.spell_by_name("Spirit of Wolf"), PlayerClass.SHAMAN, 40
    )
    assert new is not None
    assert (new.group, new.is_target_player, new.updated_at) == (
        row.group,
        row.is_target_player,
        row.updated_at,
    )
    assert new.detrimental is spell_book.spell_by_name("Spirit of Wolf").is_detrimental


def test_respell_of_a_row_that_is_gone_answers_none(spell_book) -> None:
    """A row can expire, or be overwritten by a fresh cast, while the context
    menu is open."""
    timers = TimersService()
    row = _row_for(spell_book, "Pack Spirit", "Spirit of Wolf")
    assert timers.respell_row(row, spell_book.spell_by_name("Spirit of Wolf"), None, None) is None
    assert timers.snapshot() == []


def test_respell_notifies_the_window(spell_book) -> None:
    timers = TimersService()
    row = timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    calls: list[int] = []
    timers.on_change.append(lambda: calls.append(1))
    timers.respell_row(row, spell_book.spell_by_name("Spirit of Wolf"), PlayerClass.SHAMAN, 40)
    assert calls, "the spell window repaints off on_change"


# -- the correction re-derives the timer, not just the label -------------------


def test_respell_recomputes_the_duration_from_the_new_spell(spell_book) -> None:
    """Ultravision runs 36 minutes and See Invisible 27 at level 30, so a
    correction that only changed the label would leave the bar lying."""
    timers = TimersService()
    ultravision = spell_book.spell_by_name("Ultravision")
    see_invisible = spell_book.spell_by_name("See Invisible")
    old_duration = float(get_duration_seconds(ultravision, PlayerClass.SHAMAN, 30))
    new_duration = float(get_duration_seconds(see_invisible, PlayerClass.SHAMAN, 30))
    assert old_duration != new_duration

    row = timers.add_spell(
        SpellRow(
            name=ultravision.name,
            group=" Joe ",
            updated_at=T0,
            is_target_player=False,
            spell=ultravision,
            ends_at=T0 + timedelta(seconds=old_duration - 300),  # five minutes in
            total_duration_s=old_duration,
            alternatives=[see_invisible],
        )
    )
    new = timers.respell_row(row, see_invisible, PlayerClass.SHAMAN, 30)

    assert new is not None
    assert new.total_duration_s == new_duration
    # Five minutes were already gone, so the new end is five minutes short of
    # a full See Invisible — not a fresh one starting now.
    assert new.ends_at == T0 + timedelta(seconds=new_duration - 300)


def test_respell_swaps_the_gem_icon(spell_book) -> None:
    """The window paints from ``row.spell``, so the icon follows the correction."""
    timers = TimersService()
    row = timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    sow = spell_book.spell_by_name("Spirit of Wolf")
    assert row.spell.spell_icon != sow.spell_icon
    new = timers.respell_row(row, sow, PlayerClass.SHAMAN, 40)
    assert new is not None and new.spell.spell_icon == sow.spell_icon


def test_respell_recomputes_detrimental(spell_book) -> None:
    """It drives the bar colour and the NPC-overwrite rule, so it cannot be
    inherited from the spell that was guessed."""
    timers = TimersService()
    row = timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    assert row.detrimental is False
    snare = spell_book.spell_by_name("Snare")
    assert snare is not None and snare.is_detrimental
    new = timers.respell_row(row, snare, PlayerClass.SHAMAN, 40)
    assert new is not None and new.detrimental is True


def test_respell_takes_the_new_spells_flash_setting(spell_book) -> None:
    """Post-expiry flash is a per-spell opt-in (#16). A row flashing because
    the guess was on the allowlist must stop when it is corrected to a spell
    that is not — and vice versa."""
    timers = TimersService()
    row = _row_for(spell_book, "Pack Spirit", "Spirit of Wolf")
    row.post_expiry_persist_s = 30.0
    timers.add_spell(row)

    sow = spell_book.spell_by_name("Spirit of Wolf")
    new = timers.respell_row(row, sow, PlayerClass.SHAMAN, 40)
    assert new is not None and new.post_expiry_persist_s == 0.0

    row2 = timers.respell_row(
        new, spell_book.spell_by_name("Pack Spirit"), PlayerClass.SHAMAN, 40, 30.0
    )
    assert row2 is not None and row2.post_expiry_persist_s == 30.0


def test_respell_clears_a_stamped_expiry(spell_book) -> None:
    """``expired_at`` is an observation of the OLD countdown crossing over; the
    new one has not."""
    timers = TimersService()
    row = _row_for(spell_book, "Pack Spirit", "Spirit of Wolf")
    row.expired_at = T0
    timers.add_spell(row)
    new = timers.respell_row(
        row, spell_book.spell_by_name("Spirit of Wolf"), PlayerClass.SHAMAN, 40
    )
    assert new is not None and new.expired_at is None


# -- a correction lands where the matcher would have -------------------------


def _handler_row(spell_book, spell_name: str, target: str) -> SpellRow:
    """The row the matcher WOULD have produced had it named this spell."""
    timers = TimersService()
    handler = SpellTimerHandler(
        EventBus(),
        _player(),
        spell_book,
        timers,
        spell_settings=Settings().spellwindow,
    )
    handler.handle_spell(spell_book.spell_by_name(spell_name), target, 0, T0)
    return next(r for r in _spell_rows(timers) if not r.is_cooldown)


def _player() -> ActivePlayer:
    player = ActivePlayer()
    player.player_class = PlayerClass.SHAMAN
    player.level = 40
    return player


def _an_npc(spell_book) -> str:
    return next(iter(sorted(spell_book.npcs)))


def test_correcting_a_detrimental_on_an_npc_keeps_the_grace_tick(spell_book) -> None:
    """A detrimental row on an NPC gets one extra tick so it outlives the
    "spell has worn off" line. The correction has to apply it too, or a
    corrected row is six seconds shorter than the same spell guessed right.
    """
    npc = _an_npc(spell_book)
    expected = _handler_row(spell_book, "Tashan", npc)

    timers = TimersService()
    handler = SpellTimerHandler(
        EventBus(), _player(), spell_book, timers, spell_settings=Settings().spellwindow
    )
    handler.handle_spell(
        spell_book.spell_by_name("Snare"), npc, 0, T0, [spell_book.spell_by_name("Tashan")]
    )
    row = next(r for r in _spell_rows(timers) if not r.is_cooldown)
    corrected = timers.respell_row(row, spell_book.spell_by_name("Tashan"), PlayerClass.SHAMAN, 40)

    assert corrected is not None
    assert corrected.total_duration_s == expected.total_duration_s


def test_correcting_to_a_discipline_takes_its_duration_override(spell_book) -> None:
    """Five of the eight discipline overrides disagree with the spells_us.txt
    formula — Puretone runs 240s where the formula says 120 — so a correction
    that went through the formula alone would halve it."""
    puretone = spell_book.spell_by_name("Puretone Discipline")
    assert puretone is not None
    assert base_timer_duration_seconds(puretone, PlayerClass.BARD, 60) == float(
        DISCIPLINE_DURATION_OVERRIDES_S["Puretone Discipline"]
    )

    timers = TimersService()
    row = timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    corrected = timers.respell_row(row, puretone, PlayerClass.BARD, 60)
    assert corrected is not None
    assert corrected.total_duration_s == float(
        DISCIPLINE_DURATION_OVERRIDES_S["Puretone Discipline"]
    )


def test_a_correction_matches_the_handler_for_every_alternative(spell_book) -> None:
    """The general property, over a real ambiguous candidate list: whichever
    candidate the user picks, the row is the one the matcher would have built.
    """
    npc = _an_npc(spell_book)
    candidates = spell_book.cast_on_other("is surrounded by a brief lupine aura.")
    assert len(candidates) > 1

    for chosen in candidates:
        timers = TimersService()
        row = timers.add_spell(
            SpellRow(
                name="placeholder",
                group=npc,
                updated_at=T0,
                is_target_player=False,
                spell=candidates[0],
                ends_at=T0 + timedelta(seconds=60),
                total_duration_s=60.0,
                alternatives=list(candidates[1:]),
            )
        )
        corrected = timers.respell_row(row, chosen, PlayerClass.SHAMAN, 40)
        expected = _handler_row(spell_book, chosen.name, npc)
        assert corrected is not None
        assert corrected.total_duration_s == expected.total_duration_s, chosen.name
        assert corrected.detrimental == expected.detrimental, chosen.name


def test_a_spell_of_another_row_kind_is_never_offered(ctx, spell_book) -> None:
    """A correction rebuilds one SpellRow, so it can only offer candidates that
    would also have been a plain countdown. Counter spells become a tally and
    reuse-timer spells put a cooldown row beside the buff; neither is something
    a relabel creates, so they are dropped from the menu."""
    timers = _rig(ctx)
    handler = SpellTimerHandler(
        EventBus(), _player(), spell_book, timers, spell_settings=Settings().spellwindow
    )
    offered = [
        spell_book.spell_by_name("Spirit of Wolf"),  # a plain buff — kept
        spell_book.spell_by_name("Flash of Light"),  # counter spell — dropped
        spell_book.spell_by_name("Dictate"),  # reuse timer — dropped
        spell_book.spell_by_name("Puretone Discipline"),  # cooldown row — dropped
    ]
    assert all(s is not None for s in offered)
    handler.handle_spell(spell_book.spell_by_name("Pack Spirit"), " Joe ", 0, T0, offered)

    (row,) = [r for r in _spell_rows(timers) if not r.is_cooldown]
    assert [s.name for s in row.alternatives] == ["Spirit of Wolf"]


def test_narrowing_the_menu_does_not_narrow_the_guess(ctx, spell_book) -> None:
    """A counter spell that IS the guess still gets its tally — only the
    correction menu is narrowed."""
    timers = _rig(ctx)
    handler = SpellTimerHandler(
        EventBus(), _player(), spell_book, timers, spell_settings=Settings().spellwindow
    )
    handler.handle_spell(spell_book.spell_by_name("Flash of Light"), " Joe ", 0, T0)
    assert [r.name for r in timers.snapshot()] == ["Flash of Light"]
    assert _spell_rows(timers) == []  # a CounterRow, not a countdown


# -- a correction edits one row and no others --------------------------------


def _stacked(spell_book, timers: TimersService) -> SpellTimerHandler:
    """Two detrimental rows on same-named NPCs, which TimerRecast=StartNewTimer
    keeps as separate countdowns sharing one (name, group)."""
    handler = SpellTimerHandler(
        EventBus(),
        _player(),
        spell_book,
        timers,
        spell_settings=Settings().spellwindow,
        timer_recast=lambda: "StartNewTimer",
    )
    tashan = spell_book.spell_by_name("Tashan")
    tashani = spell_book.spell_by_name("Tashani")
    assert tashan.is_detrimental and tashani.is_detrimental
    npc = _an_npc(spell_book)
    handler.handle_spell(tashan, npc, 0, T0, [tashani])
    handler.handle_spell(tashan, npc, 0, T0 + timedelta(seconds=30), [tashani])
    return handler


def test_correcting_a_stacked_row_does_not_delete_its_neighbour(spell_book) -> None:
    """Going through add_spell would: its overwrite scan drops any other row
    sharing the new (name, group), so correcting the SECOND stacked DoT onto a
    name the first already carries silently erased a live countdown the user
    never touched."""
    timers = TimersService()
    _stacked(spell_book, timers)
    tashani = spell_book.spell_by_name("Tashani")

    live = [r for r in _spell_rows(timers) if not r.is_cooldown]
    assert len(live) == 2, "the two casts should have stacked"

    first = timers.respell_row(live[0], tashani, PlayerClass.SHAMAN, 60)
    assert first is not None
    remaining = [r for r in _spell_rows(timers) if not r.is_cooldown]
    assert len(remaining) == 2

    second = timers.respell_row(remaining[1], tashani, PlayerClass.SHAMAN, 60)
    assert second is not None
    survivors = [r for r in _spell_rows(timers) if not r.is_cooldown]
    assert len(survivors) == 2, "the first correction's row was destroyed"
    assert {r.name for r in survivors} == {"Tashani"}
    # ...and they are still two distinct countdowns, not one duplicated.
    assert len({r.ends_at for r in survivors}) == 2


def test_a_correction_keeps_the_rows_place(spell_book) -> None:
    """Appending would send the row to the bottom of the window mid-countdown."""
    timers = TimersService()
    _stacked(spell_book, timers)
    before = [r.name for r in timers.snapshot()]
    live = [r for r in _spell_rows(timers) if not r.is_cooldown]
    index = timers.snapshot().index(live[0])

    timers.respell_row(live[0], spell_book.spell_by_name("Tashani"), PlayerClass.SHAMAN, 60)

    after = timers.snapshot()
    assert len(after) == len(before)
    assert after[index].name == "Tashani"


def test_a_correction_leaves_every_other_row_untouched(spell_book) -> None:
    """The general rule the two cases above are instances of."""
    timers = TimersService()
    timers.add_spell(_row_for(spell_book, "Pack Spirit", "Spirit of Wolf"))
    bystanders = [
        timers.add_spell(
            SpellRow(
                name=name,
                group=group,
                updated_at=T0,
                spell=spell_book.spell_by_name(name),
                ends_at=T0 + timedelta(minutes=10),
                total_duration_s=600.0,
            )
        )
        for name, group in (("Spirit of Wolf", " Joe "), ("Levitate", YOU_GROUP))
    ]
    target = next(r for r in _spell_rows(timers) if r.name == "Pack Spirit")

    timers.respell_row(target, spell_book.spell_by_name("Spirit of Wolf"), PlayerClass.SHAMAN, 40)

    for row in bystanders:
        assert row in timers.snapshot(), f"{row.name} in {row.group} was collateral damage"
