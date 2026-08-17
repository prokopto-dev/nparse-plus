"""Camp/relog end to end (#120): literal log lines through the committed parser
chain and the driver tick, into TimersService and back out again.

Mirrors the composition wiring — CampParser's tick on the driver's tick list,
PlayerProfileHandler before TimerPersistenceHandler, seconds-left anchored to
the pipeline's last log line — so what the unit tests assert in pieces is
asserted here as one path.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass
from nparseplus.core.handlers.boat import BOATS_GROUP
from nparseplus.core.handlers.timer_persistence import TimerPersistenceHandler
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.camp import CampParser
from nparseplus.core.parsers.registry import build_parser_chain
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer, Server
from nparseplus.core.spells.spells_us import SpellBook, load_spell_book
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimerRow, TimersService

SPELLS_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "spells_us.txt"

T0 = datetime(2026, 7, 8, 21, 59, 36)

CAMP_LINE = "It will take about 5 more seconds to prepare your camp."
ABANDON_LINE = "You abandon your preparations to camp."
WELCOME_LINE = "Welcome to EverQuest!"


@pytest.fixture(scope="module")
def spell_book() -> SpellBook:
    return load_spell_book(SPELLS_FIXTURE)


class Harness:
    """Log lines -> chain -> handlers, with the driver tick under test control."""

    def __init__(self, spell_book: SpellBook) -> None:
        self.now = T0
        self.bus = EventBus()
        self.player = ActivePlayer(name="Tester", server=Server.GREEN)
        self.player.player_class = PlayerClass.ENCHANTER
        self.player.level = 60
        self.settings = Settings(players=[PlayerInfo(name="Tester", server="green")])
        self.timers = TimersService()
        ctx = ParseContext(
            bus=self.bus, player=self.player, spells=spell_book, settings=self.settings
        )
        parsers = build_parser_chain()
        self.pipeline = LogPipeline(parsers, ctx)
        self.camp = next(p for p in parsers if isinstance(p, CampParser))
        self.camp._clock = lambda: self.now
        TimerPersistenceHandler(
            self.bus,
            self.player,
            self.settings,
            self.timers,
            spell_book,
            clock=lambda: self.now,
            log_clock=lambda: self.pipeline.last_entry_time,
        )

    @property
    def profile(self) -> PlayerInfo:
        return self.settings.players[0]

    def push(self, message: str) -> None:
        self.pipeline.process(f"[{self.now:%a %b %d %H:%M:%S %Y}] {message}")

    def tick(self) -> None:
        """One driver iteration, in composition's order."""
        self.camp.tick(self.now)
        self.timers.tick(self.now)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.tick()

    def add_buff(self, seconds: int = 1200) -> None:
        spell = self.pipeline._ctx.spells.spell_by_name("Clarity")
        assert spell is not None
        self.timers.add_spell(
            SpellRow(
                name=spell.name,
                group=YOU_GROUP,
                updated_at=self.now,
                spell=spell,
                ends_at=self.now + timedelta(seconds=seconds),
                total_duration_s=float(seconds),
            )
        )

    def add_boat(self, seconds: int = 3000) -> None:
        self.timers.add_timer(
            TimerRow(
                name="Freeport to Butcherblock",
                group=BOATS_GROUP,
                updated_at=self.now,
                ends_at=self.now + timedelta(seconds=seconds),
                total_duration_s=float(seconds),
            )
        )

    def row_names(self) -> list[str]:
        return sorted(row.name for row in self.timers.snapshot())


@pytest.fixture
def rig(spell_book: SpellBook) -> Harness:
    return Harness(spell_book)


def test_camping_hides_the_buff_and_leaves_the_boat(rig: Harness) -> None:
    rig.add_buff(seconds=1200)
    rig.add_boat()
    rig.push(CAMP_LINE)

    # Inside the 6 s window nothing has happened yet.
    rig.advance(5)
    assert rig.row_names() == ["Clarity", "Freeport to Butcherblock"]

    rig.advance(1)
    assert rig.row_names() == ["Freeport to Butcherblock"]
    assert [s.seconds_left for s in rig.profile.you_spells] == [1200]


def test_abandoning_the_camp_moves_nothing(rig: Harness) -> None:
    rig.add_buff(seconds=1200)
    rig.add_boat()
    rig.push(CAMP_LINE)
    rig.advance(2)
    rig.push(ABANDON_LINE)

    rig.advance(60)

    assert rig.row_names() == ["Clarity", "Freeport to Butcherblock"]
    # Still ticking down on screen, and the buff was never frozen away.
    row = rig.timers.find("Clarity")
    assert row is not None
    assert row.ends_at == T0 + timedelta(seconds=1200)


def test_logging_back_in_returns_the_buff_with_the_same_time_left(rig: Harness) -> None:
    rig.add_buff(seconds=1200)
    rig.add_boat()
    rig.push(CAMP_LINE)
    rig.advance(6)
    assert rig.timers.find("Clarity") is None

    # Away long enough that the buff would have expired against the wall clock.
    rig.advance(2400)
    rig.push(WELCOME_LINE)

    row = rig.timers.find("Clarity")
    assert row is not None
    assert row.ends_at == rig.now + timedelta(seconds=1200)
    # The boat kept counting the whole time and is still on its original end.
    boat = rig.timers.find("Freeport to Butcherblock")
    assert boat is not None
    assert boat.ends_at == T0 + timedelta(seconds=3000)


def test_camp_completion_is_published_on_the_ticking_thread(rig: Harness) -> None:
    """Step 1 of #120, from the log line in: a camp resolved on a timer thread
    would mutate TimersService off the driver thread."""
    import threading

    rig.add_buff()
    rig.push(CAMP_LINE)
    rig.now += timedelta(seconds=6)

    threads: list[str] = []
    original = rig.timers.remove_self_rows

    def spy() -> int:
        threads.append(threading.current_thread().name)
        return original()

    rig.timers.remove_self_rows = spy  # type: ignore[method-assign]
    ticker = threading.Thread(target=rig.tick, name="pretend-driver")
    ticker.start()
    ticker.join(timeout=2)

    assert threads == ["pretend-driver"]
    assert rig.timers.find("Clarity") is None
