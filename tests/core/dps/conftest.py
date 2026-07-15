"""Shared fixtures for the DPS/fight engine tests."""

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker
from nparseplus.core.events import DamageEvent
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.lineinfo import LineInfo, parse_line
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.player import ActivePlayer

T0 = datetime(2026, 7, 8, 21, 0, 0)


@pytest.fixture
def t0() -> datetime:
    return T0


@pytest.fixture
def tracker() -> FightTracker:
    return FightTracker()


@pytest.fixture
def player() -> ActivePlayer:
    return ActivePlayer(name="Genartik")


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def handler(bus: EventBus, player: ActivePlayer, tracker: FightTracker) -> DpsHandler:
    return DpsHandler(bus, player, tracker)


@pytest.fixture
def ctx(bus: EventBus, player: ActivePlayer) -> ParseContext:
    return ParseContext(bus=bus, player=player)


@pytest.fixture
def make_line() -> Callable[..., LineInfo]:
    """Build a LineInfo from a raw message at T0 + offset seconds."""

    def _make(message: str, offset_s: float = 0.0, line_number: int = 1) -> LineInfo:
        stamp = (T0 + timedelta(seconds=offset_s)).strftime("%a %b %d %H:%M:%S %Y")
        info = parse_line(f"[{stamp}] {message}", line_number)
        assert info is not None
        return info

    return _make


@pytest.fixture
def hit() -> Callable[..., DamageEvent]:
    """Build a DamageEvent at T0 + offset seconds."""

    def _make(
        attacker: str,
        target: str,
        damage: int,
        offset_s: float = 0.0,
        level_guess: int | None = None,
    ) -> DamageEvent:
        return DamageEvent(
            timestamp=T0 + timedelta(seconds=offset_s),
            target_name=target,
            attacker_name=attacker,
            damage_done=damage,
            damage_type="hit",
            level_guess=level_guess,
        )

    return _make
