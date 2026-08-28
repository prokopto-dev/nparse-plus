"""Discipline reuse timers — the #187 scaling clamp and the table audit.

The scaled table interpolates between two anchors (the cooldown at the
discipline's own min level and the cooldown at 60). Everything here is about
what happens OUTSIDE that range, which is what the reported bug was.
"""

from __future__ import annotations

import pytest
from tests.core.spells.conftest import T0

from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.handlers.player_profile import PlayerProfileHandler
from nparseplus.core.handlers.spell_timers import _DISCIPLINE_COOLDOWNS, SpellTimerHandler
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.who import PlayerWhoLogParse
from nparseplus.core.parsers.you_begin_casting import YouBeginCastingParser
from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.durations import DISCIPLINE_DURATION_OVERRIDES_S
from nparseplus.core.spells.spells_us import SPACE_YOU, SpellBook
from nparseplus.core.timers import TimersService

MAX_LEVEL = 60


def cooldown_seconds(handler_cls, spell, level: int | None) -> int:
    """Drive the real handler method with a stubbed player level."""

    class _Player:
        pass

    player = _Player()
    player.level = level
    handler = handler_cls.__new__(handler_cls)
    handler.player = player
    return handler_cls._discipline_cooldown_seconds(handler, spell, 0)


@pytest.fixture
def disciplines(spell_book: SpellBook) -> dict:
    return {s.name: s for s in spell_book.spells if s.name.endswith("Discipline")}


# -- the reported bug -------------------------------------------------------------


def test_stonestance_across_levels(disciplines) -> None:
    """The #187 acceptance table: 12 min at or below 51, 4 min at or above 60."""
    spell = disciplines["Stonestance Discipline"]
    at = {
        lvl: cooldown_seconds(SpellTimerHandler, spell, lvl) for lvl in (1, 25, 50, 51, 55, 60, 65)
    }

    assert at[1] == at[25] == at[50] == at[51] == 12 * 60
    assert at[60] == at[65] == 4 * 60
    assert 4 * 60 < at[55] < 12 * 60  # interpolates strictly between the anchors


def test_stonestance_level_25_is_not_the_reported_35_minutes(disciplines) -> None:
    """The exact report: an unclamped level 25 computed 35.1 minutes."""
    seconds = cooldown_seconds(SpellTimerHandler, disciplines["Stonestance Discipline"], 25)
    assert seconds == 12 * 60
    assert seconds < 13 * 60  # nowhere near the ~35 min that was reported


def test_unknown_level_reads_as_the_base_cooldown(disciplines) -> None:
    """An unset level must not inflate the timer — it was the input that did."""
    spell = disciplines["Stonestance Discipline"]
    assert cooldown_seconds(SpellTimerHandler, spell, None) == 12 * 60


@pytest.mark.parametrize("name", sorted(_DISCIPLINE_COOLDOWNS))
def test_scaled_disciplines_are_bounded_and_monotonic(name, disciplines) -> None:
    """Same shape for all six: never negative, never above base, non-increasing."""
    spell = disciplines[name]
    base_seconds, min_level, seconds_range = _DISCIPLINE_COOLDOWNS[name]
    floor = base_seconds - seconds_range

    values = [cooldown_seconds(SpellTimerHandler, spell, lvl) for lvl in range(1, 71)]
    assert all(v > 0 for v in values), f"{name} produced a non-positive cooldown"
    assert all(v <= base_seconds for v in values), f"{name} exceeded its base cooldown"
    assert all(v >= floor for v in values), f"{name} fell below its level-60 cooldown"
    assert values == sorted(values, reverse=True), f"{name} is not monotonic in level"

    # The anchors themselves.
    assert cooldown_seconds(SpellTimerHandler, spell, min_level) == int(base_seconds)
    assert cooldown_seconds(SpellTimerHandler, spell, MAX_LEVEL) == int(floor)
    # Clamped on both sides.
    assert cooldown_seconds(SpellTimerHandler, spell, 1) == int(base_seconds)
    assert cooldown_seconds(SpellTimerHandler, spell, 70) == int(floor)


# -- the table audit (#187: evidenced, not asserted) --------------------------------

# Every discipline in the bundled spells_us.txt that deliberately takes its reuse
# straight from recast_time_ms rather than a scaled table row. Each was checked
# against the spell data and smasherprog/eqtool@d8e8084f, whose
# SpellHandlerService.Handle scales exactly the six in _DISCIPLINE_COOLDOWNS and
# leaves every one of these alone. Their recast_time_ms is the client's own flat
# figure, and DisciplineCooldownParser corrects any that is wrong from the game's
# "You can use the ability ... again in" line.
#
# This list exists so a discipline added to the data later cannot be silently
# missed: it fails this test until someone classifies it.
FORMULA_DRIVEN_DISCIPLINES = frozenset(
    {
        "Aggressive Discipline",
        "Ashenhand Discipline",
        "Battle Focus Discipline",
        "Battle Sense Discipline",
        "Bestial Fury Discipline",
        "Blind Rage Discipline",
        "Blinding Speed Discipline",
        "Burning Rage Discipline",
        "Charge Discipline",
        "Cleaving Anger Discipline",
        "Cleaving Rage Discipline",
        "Concentration Discipline",
        "Counterattack Discipline",
        "Deadeye Discipline",
        "Deftdance Discipline",
        "Duelist Discipline",
        "Fearless Discipline",
        "Fellstrike Discipline",
        "Focused Fury Discipline",
        "Focused Will Discipline",
        "Fortitude Discipline",
        "Furious Discipline",
        "Holyforge Discipline",
        "Hundred Fists Discipline",
        "Indomitable Discipline",
        "Inspired Anger Discipline",
        "Kinesthetics Discipline",
        "Leechcurse Discipline",
        "Mighty Strike Discipline",
        "Nimble Discipline",
        "Protective Spirit Discipline",
        "Puretone Discipline",
        "Reckless Discipline",
        "Resistant Discipline",
        "Sanctification Discipline",
        "Silentfist Discipline",
        "Spiritual Discipline",
        "Sprint Discipline",
        "Thunderkick Discipline",
        "Trueshot Discipline",
        "Unflinching Will Discipline",
        "Unholy Aura Discipline",
        "Unpredictable Rage Discipline",
        "Vengeful Flurry Discipline",
        "Weapon Shield Discipline",
        "Whirlwind Discipline",
    }
)


def test_every_discipline_in_the_data_is_classified(disciplines) -> None:
    """No discipline may be silently unaccounted for (#187)."""
    classified = set(_DISCIPLINE_COOLDOWNS) | set(FORMULA_DRIVEN_DISCIPLINES)
    in_data = set(disciplines)

    unclassified = in_data - classified
    assert not unclassified, (
        "disciplines in spells_us.txt with no audit classification: "
        f"{sorted(unclassified)} — add a _DISCIPLINE_COOLDOWNS row if reuse scales "
        "with level, otherwise list it in FORMULA_DRIVEN_DISCIPLINES"
    )

    stale = classified - in_data
    assert not stale, f"classified disciplines that no longer exist in the data: {sorted(stale)}"


def test_the_two_tables_do_not_overlap_in_meaning(disciplines) -> None:
    """The tables answer different questions; a name may appear in both."""
    # Duration overrides are about how long the effect lasts, cooldowns about
    # when it can be reused. Defensive/Evasive/Voiddance are legitimately in
    # both. What must hold is that every override names a real discipline.
    for name in DISCIPLINE_DURATION_OVERRIDES_S:
        assert name in disciplines, f"{name} has a duration override but is not in the data"


def test_scaled_bases_track_the_client_recast(disciplines) -> None:
    """Five of six bases equal recast_time_ms; Innerflame is the corrected one.

    This is the evidence for the Innerflame divergence — if a future data
    regeneration moves a recast, this test says which row to revisit.
    """
    for name, (base_seconds, _min_level, _rng) in _DISCIPLINE_COOLDOWNS.items():
        recast_s = disciplines[name].recast_time_ms / 1000.0
        assert recast_s == base_seconds, (
            f"{name}: table base {base_seconds}s disagrees with the client's "
            f"recast {recast_s}s — one of them is wrong"
        )


# -- end to end: a /who self row re-times the next discipline (#187) -----------------


def test_who_self_row_retimes_a_subsequent_discipline_cast(spell_book) -> None:
    """The whole reported chain: a monk with no known level casts Stonestance
    and gets the 12 min base; their own /who row lands level 60; the next cast
    is re-timed to 4 min."""

    spell_book.casting.clear()
    settings = Settings()
    settings.players.append(PlayerInfo(name="Fistman", server="green"))
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Fistman", Server.GREEN)
    timers = TimersService()
    PlayerProfileHandler(bus, player, settings)
    SpellTimerHandler(bus, player, spell_book, timers)
    ctx = ParseContext(bus=bus, player=player, spells=spell_book)
    parsers = [PlayerWhoLogParse(), YouBeginCastingParser(), YouFinishCastingParser()]

    counter = 0

    def push(message: str) -> None:
        nonlocal counter
        counter += 1
        line = LineInfo(raw=message, message=message, timestamp=T0, line_number=counter)
        for parser in parsers:
            if parser.handle(line, ctx):
                break

    def cooldown_row_seconds():
        row = timers.find("Stonestance Discipline Cooldown", SPACE_YOU)
        return None if row is None else round(row.total_duration_s)

    # No level known yet: the base cooldown, not the 57 min an unclamped 0 gave.
    assert player.level is None
    push("You begin casting Stonestance Discipline.")
    push("Your body becomes one with the earth.")
    assert cooldown_row_seconds() == 12 * 60

    # The player's own /who row — guildless, the case the tracker's branch skips.
    push("Players on EverQuest:")
    push("---------------------------")
    push("[60 Grandmaster] Fistman (Human)")
    assert player.level == 60

    timers.clear_all()
    push("You begin casting Stonestance Discipline.")
    push("Your body becomes one with the earth.")
    assert cooldown_row_seconds() == 4 * 60
