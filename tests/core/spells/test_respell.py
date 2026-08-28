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
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import SpellCastOnYouEvent
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.parsers.spell_cast_on_other import SpellCastOnOtherParser
from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
from nparseplus.core.spells.durations import get_duration_seconds
from nparseplus.core.timers import SpellRow, TimersService

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
