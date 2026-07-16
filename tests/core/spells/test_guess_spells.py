"""Guess Spells option — ambiguous casts create no timers when disabled.

nparseplus extension: EQTool's best-guess is always on; best_guess_spells
(default True) preserves that, False suppresses multi-candidate guesses at
both gates (the cast-on-other handler and the cast-on-you parser).
"""

from __future__ import annotations

from tests.core.spells.conftest import EventCollector, make_line

from nparseplus.config.settings import Settings
from nparseplus.core.events import SpellCastOnYouEvent
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.parsers.spell_cast_on_other import SpellCastOnOtherParser
from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
from nparseplus.core.timers import SpellRow, TimersService

# From the pinned fixture: two candidates share these cast texts.
AMBIGUOUS_OTHER = "Joe's blood ignites."  # Ignite Blood | Pyrocruor
AMBIGUOUS_YOU = "Your blood ignites."
SINGLE_YOU = "You feel cleansed."  # Cleanse only


def _rig(ctx, best_guess: bool):
    settings = Settings()
    settings.spellwindow.best_guess_spells = best_guess
    ctx.settings = settings
    timers = TimersService()
    SpellTimerHandler(ctx.bus, ctx.player, ctx.spells, timers, spell_settings=settings.spellwindow)
    return timers


def _push(ctx, parser, message: str) -> bool:
    return parser.handle(make_line(message), ctx)


def test_ambiguous_cast_on_other_suppressed_when_off(ctx) -> None:
    timers = _rig(ctx, best_guess=False)
    assert _push(ctx, SpellCastOnOtherParser(), AMBIGUOUS_OTHER)  # still consumed
    assert [r for r in timers.snapshot() if isinstance(r, SpellRow)] == []


def test_ambiguous_cast_on_other_guessed_when_on(ctx) -> None:
    timers = _rig(ctx, best_guess=True)
    _push(ctx, SpellCastOnOtherParser(), AMBIGUOUS_OTHER)
    rows = [r for r in timers.snapshot() if isinstance(r, SpellRow)]
    assert len(rows) == 1
    assert rows[0].spell.name in ("Ignite Blood", "Pyrocruor")


def test_ambiguous_cast_on_you_suppressed_when_off(ctx) -> None:
    _rig(ctx, best_guess=False)
    collector = EventCollector(ctx.bus)
    assert _push(ctx, YouFinishCastingParser(), AMBIGUOUS_YOU)  # consumed, no event
    assert collector.of_type(SpellCastOnYouEvent) == []


def test_ambiguous_cast_on_you_guessed_when_on(ctx) -> None:
    _rig(ctx, best_guess=True)
    collector = EventCollector(ctx.bus)
    _push(ctx, YouFinishCastingParser(), AMBIGUOUS_YOU)
    events = collector.of_type(SpellCastOnYouEvent)
    assert len(events) == 1


def test_single_candidate_always_lands(ctx) -> None:
    _rig(ctx, best_guess=False)
    collector = EventCollector(ctx.bus)
    _push(ctx, YouFinishCastingParser(), SINGLE_YOU)
    events = collector.of_type(SpellCastOnYouEvent)
    assert len(events) == 1 and events[0].spell.name == "Cleanse"


def test_ctx_without_settings_keeps_guessing(ctx) -> None:
    ctx.settings = None
    collector = EventCollector(ctx.bus)
    _push(ctx, YouFinishCastingParser(), AMBIGUOUS_YOU)
    assert len(collector.of_type(SpellCastOnYouEvent)) == 1
