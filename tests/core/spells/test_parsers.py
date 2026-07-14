"""Parser event tests — lines in, typed bus events out."""

from __future__ import annotations

from datetime import timedelta

from tests.core.spells.conftest import T0, EventCollector, make_line

from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import (
    ClassDetectedEvent,
    ResistSpellEvent,
    SpellCastOnOtherEvent,
    SpellCastOnYouEvent,
    SpellWornOffOtherEvent,
    SpellWornOffSelfEvent,
    YouBeginCastingEvent,
    YouFinishCastingEvent,
    YouForgetEvent,
    YouHaveFinishedMemorizingEvent,
    YourSpellInterruptedEvent,
)
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.finished_memorizing import YouHaveFinishedMemorizingParser
from nparseplus.core.parsers.resist import ResistParser
from nparseplus.core.parsers.spell_cast_on_other import SpellCastOnOtherParser
from nparseplus.core.parsers.spell_interrupted import YourSpellInterruptedParser
from nparseplus.core.parsers.spell_worn_off import (
    SpellWornOffOtherParser,
    SpellWornOffSelfParser,
)
from nparseplus.core.parsers.you_begin_casting import YouBeginCastingParser
from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
from nparseplus.core.parsers.you_forget import YouForgetParser
from nparseplus.core.spells.spells_us import SPACE_YOU


def test_you_begin_casting(ctx: ParseContext, collector: EventCollector) -> None:
    ctx.player.player_class = PlayerClass.ENCHANTER
    ctx.player.level = 54
    assert YouBeginCastingParser().handle(make_line("You begin casting Clarity."), ctx)
    event = collector.single(YouBeginCastingEvent)
    assert event.spell.name == "Clarity"


def test_you_begin_casting_double_space(ctx: ParseContext, collector: EventCollector) -> None:
    # EQTool's substring keeps a leading space; "You begin casting  Acumen" works.
    ctx.player.player_class = PlayerClass.SHAMAN
    ctx.player.level = 60
    assert YouBeginCastingParser().handle(make_line("You begin casting  Acumen"), ctx)
    assert collector.single(YouBeginCastingEvent).spell.name == "Acumen"


def test_you_begin_casting_detects_class(ctx: ParseContext, collector: EventCollector) -> None:
    # Acumen is shaman-only -> class + level detection events fire.
    assert YouBeginCastingParser().handle(make_line("You begin casting Acumen."), ctx)
    detected = collector.single(ClassDetectedEvent)
    assert detected.player_class is PlayerClass.SHAMAN


def test_you_begin_casting_unknown_spell(ctx: ParseContext, collector: EventCollector) -> None:
    assert not YouBeginCastingParser().handle(
        make_line("You begin casting Totally Made Up Spell."), ctx
    )
    assert not collector.events


def test_finish_casting_on_self(ctx: ParseContext, collector: EventCollector) -> None:
    assert ctx.spells is not None
    clarity = ctx.spells.spell_by_name("Clarity")
    assert clarity is not None
    ctx.spells.casting.begin(clarity, T0)
    landed_at = T0 + timedelta(milliseconds=clarity.cast_time_ms)
    assert YouFinishCastingParser().handle(make_line(clarity.cast_on_you, landed_at), ctx)
    event = collector.single(YouFinishCastingEvent)
    assert event.spell.name == "Clarity"
    assert event.target_name == SPACE_YOU


def test_finish_casting_on_other_extracts_target(
    ctx: ParseContext, collector: EventCollector
) -> None:
    assert ctx.spells is not None
    clarity = ctx.spells.spell_by_name("Clarity")
    assert clarity is not None
    ctx.spells.casting.begin(clarity, T0)
    landed_at = T0 + timedelta(milliseconds=clarity.cast_time_ms)
    assert YouFinishCastingParser().handle(
        make_line(f"Joe {clarity.cast_on_other}", landed_at), ctx
    )
    event = collector.single(YouFinishCastingEvent)
    assert event.target_name == "Joe"


def test_finish_casting_too_early_falls_through(
    ctx: ParseContext, collector: EventCollector
) -> None:
    assert ctx.spells is not None
    clarity = ctx.spells.spell_by_name("Clarity")
    assert clarity is not None
    ctx.spells.casting.begin(clarity, T0)
    # Same timestamp as cast start: way before casttime-600ms elapsed. The
    # cast_on_you message still matches the SpellCastOnYou table instead.
    assert YouFinishCastingParser().handle(make_line(clarity.cast_on_you, T0), ctx)
    assert not collector.of_type(YouFinishCastingEvent)
    assert collector.single(SpellCastOnYouEvent).spell.name == "Clarity"


def test_spell_cast_on_you_guess(ctx: ParseContext, collector: EventCollector) -> None:
    ctx.player.player_class = PlayerClass.CLERIC
    ctx.player.level = 54
    # TestClairityDurationGuess1: Clarity II's cast_on_you line
    assert YouFinishCastingParser().handle(make_line("A soft breeze slips through your mind."), ctx)
    assert collector.single(SpellCastOnYouEvent).spell.name == "Clarity II"


def test_cast_on_other_possessive(ctx: ParseContext, collector: EventCollector) -> None:
    assert SpellCastOnOtherParser().handle(
        make_line("Joe's hand is covered with a dull aura."), ctx
    )
    event = collector.single(SpellCastOnOtherEvent)
    assert [s.name for s in event.spells] == ["Grim Aura"]
    assert event.target_name == "Joe"


def test_cast_on_other_someone(ctx: ParseContext, collector: EventCollector) -> None:
    ctx.player.player_class = PlayerClass.ENCHANTER
    ctx.player.level = 54
    assert SpellCastOnOtherParser().handle(
        make_line("Someone is covered by an aura of black petals."), ctx
    )
    event = collector.single(SpellCastOnOtherEvent)
    assert any(s.name == "Aura of Black Petals" for s in event.spells)
    assert event.target_name == "Someone"


def test_cast_on_other_multiword_target(ctx: ParseContext, collector: EventCollector) -> None:
    ctx.player.player_class = PlayerClass.NECROMANCER
    ctx.player.level = 60
    turgurs = ctx.spells.spell_by_name("Turgur's Insects") if ctx.spells else None
    assert turgurs is not None
    assert SpellCastOnOtherParser().handle(
        make_line(f"an Jobober rager {turgurs.cast_on_other}"), ctx
    )
    event = collector.single(SpellCastOnOtherEvent)
    assert event.target_name == "an Jobober rager"
    assert any(s.name == "Turgur's Insects" for s in event.spells)


def test_cast_on_other_npc_gets_space_prefix(ctx: ParseContext, collector: EventCollector) -> None:
    # TestShamanEpic: "A Ratling" is in the master NPC list -> " A Ratling"
    ctx.player.player_class = PlayerClass.NECROMANCER
    ctx.player.level = 60
    assert SpellCastOnOtherParser().handle(
        make_line("A Ratling is consumed by the raging spirits of the land."), ctx
    )
    event = collector.single(SpellCastOnOtherEvent)
    assert event.target_name == " A Ratling"
    assert any(s.name == "Curse of the Spirits" for s in event.spells)


def test_cast_on_other_ignores_your_lines(ctx: ParseContext, collector: EventCollector) -> None:
    assert not SpellCastOnOtherParser().handle(
        make_line("Your faction standing with Guards of Qeynos got worse."), ctx
    )
    assert not collector.events


def test_worn_off_self(ctx: ParseContext, collector: EventCollector) -> None:
    assert SpellWornOffSelfParser().handle(make_line("The grim aura fades."), ctx)
    event = collector.single(SpellWornOffSelfEvent)
    assert "Grim Aura" in event.spell_names


def test_worn_off_other(ctx: ParseContext, collector: EventCollector) -> None:
    assert SpellWornOffOtherParser().handle(make_line("Your Clarity spell has worn off."), ctx)
    assert collector.single(SpellWornOffOtherEvent).spell_name == "Clarity"


def test_resist_you(ctx: ParseContext, collector: EventCollector) -> None:
    assert ResistParser().handle(make_line("You resist the Fear spell!"), ctx)
    event = collector.single(ResistSpellEvent)
    assert event.spell.name == "Fear"
    assert event.is_you is True


def test_resist_target(ctx: ParseContext, collector: EventCollector) -> None:
    assert ResistParser().handle(make_line("Your target resisted the LowerElement spell."), ctx)
    event = collector.single(ResistSpellEvent)
    assert event.spell.name == "LowerElement"
    assert event.is_you is False


def test_you_forget(ctx: ParseContext, collector: EventCollector) -> None:
    assert YouForgetParser().handle(make_line("You forget Clarity."), ctx)
    assert collector.single(YouForgetEvent).spell_name == "Clarity"


def test_finished_memorizing(ctx: ParseContext, collector: EventCollector) -> None:
    assert YouHaveFinishedMemorizingParser().handle(
        make_line("You have finished memorizing Harvest."), ctx
    )
    assert collector.single(YouHaveFinishedMemorizingEvent).spell_name == "Harvest"


def test_spell_interrupted(ctx: ParseContext, collector: EventCollector) -> None:
    assert YourSpellInterruptedParser().handle(make_line("Your spell is interrupted."), ctx)
    assert collector.single(YourSpellInterruptedEvent)
