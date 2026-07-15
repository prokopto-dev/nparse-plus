"""DpsHandler — bus wiring, end-to-end through the real parsers."""

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from nparseplus.core.dps import FightTracker
from nparseplus.core.events import (
    CampEvent,
    ConfirmedDeathEvent,
    DamageEvent,
    LoadingPleaseWaitEvent,
    SlainEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.damage import DamageParser
from nparseplus.core.parsers.slain import SlainParser


@pytest.mark.usefixtures("handler")
def test_end_to_end_damage_lines_build_the_fight(
    tracker: FightTracker,
    ctx: ParseContext,
    make_line: Callable[..., LineInfo],
    t0: datetime,
) -> None:
    damage_parser = DamageParser()
    assert damage_parser.handle(make_line("You slash a gnoll for 10 points of damage."), ctx)
    assert damage_parser.handle(
        make_line("You slash a gnoll for 25 points of damage.", offset_s=3), ctx
    )
    assert damage_parser.handle(
        make_line("Vebanab pierces a gnoll for 7 points of damage.", offset_s=4), ctx
    )
    assert damage_parser.handle(make_line("You try to slash a gnoll, but miss!", offset_s=5), ctx)
    rows = tracker.snapshot(t0 + timedelta(seconds=5))
    assert [(r.attacker_name, r.total_damage) for r in rows] == [("You", 35), ("Vebanab", 7)]
    you = rows[0]
    assert you.target_total_damage == 42
    assert you.highest_hit == 25
    assert you.trailing_damage == 35


@pytest.mark.usefixtures("handler")
def test_end_to_end_slain_line_ends_the_fight(
    tracker: FightTracker,
    ctx: ParseContext,
    make_line: Callable[..., LineInfo],
    t0: datetime,
) -> None:
    assert DamageParser().handle(make_line("You slash a gnoll for 10 points of damage."), ctx)
    assert SlainParser().handle(make_line("You have slain a gnoll!", offset_s=8), ctx)
    rows = tracker.snapshot(t0 + timedelta(seconds=8))
    assert rows[0].is_dead
    assert rows[0].total_seconds == 8


def test_slain_event_for_you_freezes_fights_targeting_you(
    handler: DpsHandler,
    tracker: FightTracker,
    hit: Callable[..., DamageEvent],
    t0: datetime,
) -> None:
    handler.bus.publish(hit("a brigand", "You", 50))
    handler.bus.publish(
        SlainEvent(
            timestamp=t0 + timedelta(seconds=4),
            victim="You",
            killer="a brigand",
        )
    )
    rows = tracker.snapshot(t0 + timedelta(seconds=4))
    assert rows[0].target_name == "You"
    assert rows[0].is_dead


def test_confirmed_death_ends_fight_but_pseudo_victims_do_not(
    handler: DpsHandler,
    tracker: FightTracker,
    hit: Callable[..., DamageEvent],
    t0: datetime,
) -> None:
    handler.bus.publish(hit("You", "a gnoll", 10))
    handler.bus.publish(
        ConfirmedDeathEvent(timestamp=t0 + timedelta(seconds=2), victim="Exp Slain", killer="You")
    )
    assert tracker.active_fight("a gnoll") is not None
    handler.bus.publish(
        ConfirmedDeathEvent(timestamp=t0 + timedelta(seconds=3), victim="a gnoll", killer="You")
    )
    assert tracker.active_fight("a gnoll") is None


def test_zone_camp_and_loading_clear_active_fights(
    handler: DpsHandler,
    tracker: FightTracker,
    hit: Callable[..., DamageEvent],
    t0: datetime,
) -> None:
    events = [
        YouZonedEvent(
            timestamp=t0 + timedelta(seconds=1), long_name="The Feerrott", short_name="feerrott"
        ),
        LoadingPleaseWaitEvent(timestamp=t0 + timedelta(seconds=1)),
        CampEvent(timestamp=t0 + timedelta(seconds=1)),
    ]
    for event in events:
        handler.bus.publish(hit("You", "a gnoll", 10))
        assert tracker.fights
        handler.bus.publish(event)
        assert tracker.fights == []


def test_zoning_mid_fight_still_credits_the_session(
    handler: DpsHandler,
    tracker: FightTracker,
    hit: Callable[..., DamageEvent],
    t0: datetime,
) -> None:
    handler.bus.publish(hit("You", "a gnoll", 240))
    handler.bus.publish(hit("You", "a gnoll", 120, offset_s=22))
    handler.bus.publish(
        YouZonedEvent(
            timestamp=t0 + timedelta(seconds=23), long_name="The Feerrott", short_name="feerrott"
        )
    )
    assert tracker.fights == []
    summary = tracker.session_summary()
    assert summary.current_session.total_damage == 360
    assert summary.current_session.highest_hit == 240
