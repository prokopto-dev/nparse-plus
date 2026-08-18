"""TimerWindowNotifier — pop-window crossings reach the bus (#125).

TimersService holds no bus reference, so this is the bridge that lets a
plugin (or anything else) hear that a variable respawn window opened and
closed. It must be unconditional and name-agnostic — unlike the speech in
RespawnExpiryNotifier — and it must stay silent for every other kind of
expiry, since publish() feeds the Qt bridge's queued cross-thread signal.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tests._helpers import EventCollector

from nparseplus.core.bus import EventBus
from nparseplus.core.events import TimerWindowClosedEvent, TimerWindowOpenedEvent
from nparseplus.core.handlers.timer_window import TimerWindowNotifier
from nparseplus.core.spells.spells_us import SpellBook
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    SpellRow,
    TimerRow,
    TimersService,
)

T0 = datetime(2026, 7, 8, 21, 59, 36)
BASE_S = 400.0
WINDOW_S = 900.0
OPENS_AT = T0 + timedelta(seconds=BASE_S)
CLOSES_AT = OPENS_AT + timedelta(seconds=WINDOW_S)


class Env:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.timers = TimersService()
        self.collector = EventCollector(self.bus)
        self.notifier = TimerWindowNotifier(self.bus, self.timers)

    def opened(self) -> list[TimerWindowOpenedEvent]:
        return self.collector.of_type(TimerWindowOpenedEvent)

    def closed(self) -> list[TimerWindowClosedEvent]:
        return self.collector.of_type(TimerWindowClosedEvent)


@pytest.fixture
def env() -> Env:
    return Env()


def _window_row(name: str = "--Dead-- Trakanon", group: str = MOB_TIMER_GROUP) -> TimerRow:
    return TimerRow(
        name=name,
        group=group,
        updated_at=T0,
        ends_at=OPENS_AT,
        total_duration_s=BASE_S,
        window_ends_at=CLOSES_AT,
    )


def test_opening_publishes_the_whole_window(env: Env) -> None:
    env.timers.add_timer(_window_row())
    env.timers.tick(OPENS_AT)

    assert len(env.opened()) == 1
    event = env.opened()[0]
    assert event.name == "--Dead-- Trakanon"
    assert event.group == MOB_TIMER_GROUP
    assert event.opens_at == OPENS_AT
    assert event.closes_at == CLOSES_AT
    assert event.opened_at == OPENS_AT
    assert env.closed() == []


def test_opened_at_is_the_tick_that_saw_it_not_the_anchor(env: Env) -> None:
    """The driver polls at 100 ms, and a catch-up read can cross the boundary
    long after the fact."""
    env.timers.add_timer(_window_row())
    late = OPENS_AT + timedelta(seconds=45)
    env.timers.tick(late)
    assert env.opened()[0].opened_at == late
    assert env.opened()[0].opens_at == OPENS_AT


def test_closing_publishes_once_the_window_runs_out(env: Env) -> None:
    env.timers.add_timer(_window_row())
    env.timers.tick(OPENS_AT)
    env.timers.tick(CLOSES_AT)

    assert len(env.opened()) == 1
    assert len(env.closed()) == 1
    event = env.closed()[0]
    assert event.name == "--Dead-- Trakanon"
    assert event.group == MOB_TIMER_GROUP
    assert event.opens_at == OPENS_AT
    assert event.closes_at == CLOSES_AT
    assert event.closed_at == CLOSES_AT


def test_each_event_fires_exactly_once(env: Env) -> None:
    env.timers.add_timer(_window_row())
    for seconds in range(int(BASE_S) - 1, int(BASE_S + WINDOW_S) + 3):
        env.timers.tick(T0 + timedelta(seconds=seconds))
    assert len(env.opened()) == 1
    assert len(env.closed()) == 1


def test_a_plugins_own_group_is_bridged_too(env: Env) -> None:
    """Name- and group-agnostic on purpose: the speech gate is not this."""
    env.timers.add_timer(_window_row(name="Trakanon", group="Custom Pop Windows"))
    env.timers.tick(OPENS_AT)
    assert [e.name for e in env.opened()] == ["Trakanon"]
    assert env.opened()[0].group == "Custom Pop Windows"


def test_an_ordinary_timer_expiry_publishes_nothing(env: Env) -> None:
    env.timers.add_timer(
        TimerRow(
            name="Custom",
            group=TRIGGER_TIMER_GROUP,
            updated_at=T0,
            ends_at=T0 + timedelta(seconds=30),
            total_duration_s=30.0,
        )
    )
    env.timers.tick(T0 + timedelta(seconds=31))
    assert env.opened() == []
    assert env.closed() == []


def test_a_spell_expiry_publishes_nothing(env: Env, spell_book: SpellBook) -> None:
    spell = spell_book.spell_by_name("Clarity")
    assert spell is not None
    env.timers.add_spell(
        SpellRow(
            name="Clarity",
            group=YOU_GROUP,
            updated_at=T0,
            spell=spell,
            ends_at=T0 + timedelta(seconds=30),
            total_duration_s=30.0,
        )
    )
    env.timers.tick(T0 + timedelta(seconds=31))
    assert env.opened() == []
    assert env.closed() == []


def test_removing_a_window_row_by_hand_publishes_nothing(env: Env) -> None:
    """Right-click -> Clear is the user throwing the row away, not the window
    closing; nothing should hear about it."""
    row = env.timers.add_timer(_window_row())
    env.timers.tick(OPENS_AT)
    assert env.timers.remove_row(row) is True
    assert len(env.opened()) == 1
    assert env.closed() == []
