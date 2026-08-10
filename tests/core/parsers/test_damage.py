from nparseplus.core import damagetypes
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import ClassDetectedEvent, DamageEvent
from nparseplus.core.parsers.damage import DamageParser


def test_you_hit(ctx, make_line, spy):
    events = spy(DamageEvent)
    parser = DamageParser()
    assert parser.handle(make_line("You crush a shadowed man for 1 point of damage."), ctx)
    assert events[0].attacker_name == "You"
    assert events[0].target_name == "a shadowed man"
    assert events[0].damage_done == 1
    assert events[0].damage_type == "crush"


def test_other_hits(ctx, make_line, spy):
    events = spy(DamageEvent)
    parser = DamageParser()
    assert parser.handle(make_line("Vebanab slices a willowisp for 56 points of damage."), ctx)
    assert events[0].attacker_name == "Vebanab"
    assert events[0].target_name == "a willowisp"
    assert events[0].damage_done == 56
    assert events[0].damage_type == "slices"


def test_you_miss(ctx, make_line, spy):
    events = spy(DamageEvent)
    parser = DamageParser()
    assert parser.handle(make_line("You try to pierce an orc pawn, but miss!"), ctx)
    assert events[0].damage_done == 0
    assert events[0].target_name == "an orc pawn"
    assert events[0].damage_type == "pierce"


def test_non_melee(ctx, make_line, spy):
    events = spy(DamageEvent)
    parser = DamageParser()
    assert parser.handle(
        make_line("Ratman Rager was hit by non-melee for 45 points of damage."), ctx
    )
    assert events[0].attacker_name == "You"
    assert events[0].target_name == "Ratman Rager"
    assert events[0].damage_done == 45
    assert events[0].damage_type == "non-melee"


def test_backstab_detects_rogue(ctx, make_line, spy):
    damage_events = spy(DamageEvent)
    class_events = spy(ClassDetectedEvent)
    parser = DamageParser()
    assert parser.handle(make_line("You backstab a willowisp for 100 points of damage."), ctx)
    assert len(damage_events) == 1
    assert class_events[0].player_class == PlayerClass.ROGUE


def test_non_damage_line(ctx, make_line, spy):
    events = spy(DamageEvent)
    parser = DamageParser()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []


def test_the_verb_alternations_are_unchanged_by_the_extraction():
    """The four patterns used to spell their verbs out inline.

    core.damagetypes now generates them so core.dps can share the vocabulary
    without importing this module (which loads the master NPC list). Pin the
    generated alternations to what the hand-written patterns held, verbatim
    and in order — regex alternation is order-sensitive to read, and a
    reordering here would be invisible in behavior tests until some EQ line
    happened to expose it.
    """
    assert damagetypes.MELEE_SINGULAR == (
        "hit|slash|pierce|crush|claw|bite|sting|maul|gore|punch|kick|backstab|bash|slice|strike"
    )
    assert damagetypes.MELEE_PLURAL == (
        "hits|slashes|pierces|crushes|claws|bites"
        "|stings|mauls|gores|punches|kicks|backstabs|bashes|slices|strikes"
    )


def test_non_melee_still_publishes_its_event(ctx, make_line, spy):
    """The melee-only DPS default filters in the tracker, NOT here.

    The parser stays the single source of truth for what the log said; only
    core.dps.FightTracker decides what counts toward a row, so triggers and
    plugins subscribed to DamageEvent keep seeing spell damage either way.
    """
    events = spy(DamageEvent)
    parser = DamageParser()
    assert parser.handle(
        make_line("a shadowed man was hit by non-melee for 45 points of damage."), ctx
    )
    assert events[0].damage_type == damagetypes.NON_MELEE_DAMAGE_TYPE
