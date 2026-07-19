"""Shared fixtures for the combat/automation handler tests.

End-to-end cases push literal log lines through the committed parser chain
(``build_parser_chain`` + ``LogPipeline``) with fixed timestamps; unit cases
publish typed events directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

# FakeSpeaker is re-exported here for the handler test modules that import it
# from this conftest by path; EventCollector is used by the Harness below.
from tests._helpers import EventCollector, FakeSpeaker  # noqa: F401

from nparseplus.core.bus import EventBus
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.registry import build_parser_chain
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.spells_us import (
    SpellBook,
    load_master_npc_list,
    load_spell_book,
)
from nparseplus.core.timers import TimersService
from nparseplus.core.zones import ZoneDatabase, load_zone_database

SPELLS_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "spells_us.txt"

T0 = datetime(2026, 7, 8, 21, 59, 36)


class Harness:
    """Log lines -> parser chain -> bus, with controllable timestamps."""

    def __init__(self, ctx: ParseContext) -> None:
        self.ctx = ctx
        self.bus = ctx.bus
        self.player = ctx.player
        self.timers = TimersService()
        self.pipeline = LogPipeline(build_parser_chain(), ctx)
        self.collector = EventCollector(ctx.bus)

    def push(self, message: str, timestamp: datetime = T0) -> None:
        self.pipeline.process(f"[{timestamp:%a %b %d %H:%M:%S %Y}] {message}")


@pytest.fixture(scope="session")
def zones() -> ZoneDatabase:
    return load_zone_database()


@pytest.fixture(scope="session")
def npcs() -> frozenset[str]:
    return load_master_npc_list()


@pytest.fixture(scope="session")
def spell_book() -> SpellBook:
    return load_spell_book(SPELLS_FIXTURE)


@pytest.fixture
def harness(spell_book: SpellBook, zones: ZoneDatabase) -> Harness:
    spell_book.casting.clear()
    ctx = ParseContext(
        bus=EventBus(), player=ActivePlayer(name="Tester"), spells=spell_book, zones=zones
    )
    return Harness(ctx)
