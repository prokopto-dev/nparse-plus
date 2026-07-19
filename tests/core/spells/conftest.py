"""Shared fixtures: the 8k-line spell fixture is parsed once per session."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from tests._helpers import EventCollector

from nparseplus.core.bus import EventBus
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.spells_us import SpellBook, load_spell_book

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "spells_us.txt"

T0 = datetime(2026, 7, 8, 21, 59, 36)


@pytest.fixture(scope="session")
def spell_book() -> SpellBook:
    return load_spell_book(FIXTURE)


@pytest.fixture
def ctx(spell_book: SpellBook) -> ParseContext:
    # The book is session-scoped; only its casting state mutates, so reset it.
    spell_book.casting.clear()
    return ParseContext(bus=EventBus(), player=ActivePlayer(), spells=spell_book)


def make_line(message: str, timestamp: datetime = T0, number: int = 1) -> LineInfo:
    return LineInfo(raw=message, message=message, timestamp=timestamp, line_number=number)


@pytest.fixture
def collector(ctx: ParseContext) -> EventCollector:
    return EventCollector(ctx.bus)
