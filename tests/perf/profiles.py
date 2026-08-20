"""Benchmark fixtures: solo, group and raid P99 log traffic (#132).

Every line shape here is a **real** line — taken from ``EQtoolsTests``' own
corpus, either through ``tests/fixtures/logfiles/log1.txt`` (the capture
``tests/core/test_pipeline_replay.py`` replays) or from the string constants
the ported parser tests assert against, which are verbatim transcriptions of
the C# tests. Only the names, numbers and interleaving are ours. That
constraint is the point of the fixture: a benchmark run over invented lines
measures a parser chain reacting to traffic nobody generates, and the whole
value of Phase 0 is that its numbers are worth acting on.

What IS constructed is the **mix** — how many of each shape arrive per second
of play — because no raid capture exists in this repo to take it from. Each
profile below states the reasoning for its rates so they can be argued with,
and every profile is deterministic (no randomness) so two runs of the same
benchmark replay identical work.

Three profiles, because they load the chain in different places:

``solo``
    One player, one mob at a time, lots of movement. Dominated by
    ``PlayerLocationEvent`` and misses — the shapes that reach the *end* of
    the parser chain, so it is the profile where first-match-wins ordering
    costs the most.

``group``
    Six players in a dungeon. Adds group chat, pet lines, buffs wearing off
    and other players' melee, so more lines match early and more handlers
    run per line.

``raid``
    A ~40-person target raid. Melee from everyone in range, raid chat,
    complete-heal chains and shouts: the peak the driver thread has to keep
    up with, and the profile the plugin-dispatch benchmarks use.

Rates are per in-game second and the timestamp advances one second per
"round", which matches how EQ stamps its log (one-second resolution, many
lines sharing a stamp).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

BASE_TIME = datetime(2026, 7, 15, 21, 0, 0)

# --- the catalogue ----------------------------------------------------------
# Names are drawn from the same tests so the parsers see the character classes
# they were ported against (EQ names may contain backticks and spaces).
PLAYERS = ("Whitewitch", "Vebanab", "Genartik", "Jaloy", "Thalistair", "Mezzter")
RAIDERS = (*PLAYERS, "Fizzix", "Enudara", "Azleep", "Wreckeder", "Xantik", "Clericone")
MOBS = ("a shadowed man", "a willowisp", "an orc pawn", "a Tesch Mas Gnoll", "a bile golem")
RAID_TARGET = "Lord Nagafen"

# Every template below is annotated with where the shape came from. `{}`
# placeholders are the only thing that varies.
_MELEE_HIT = "{attacker} slices {target} for {damage} points of damage."  # DamageTests
_MELEE_YOU = "You crush {target} for {damage} points of damage."  # DamageTests
_MELEE_MISS = "You try to pierce {target}, but miss!"  # DamageTests
_MOB_HIT = "{attacker} slices YOU for {damage} points of damage."  # DamageTests shape
_NON_MELEE = "{target} was hit by non-melee for {damage} points of damage."  # DamageTests
_BACKSTAB = "You backstab {target} for {damage} points of damage."  # DamageTests
_LOCATION = "Your Location is {x:.2f}, {y:.2f}, {z:.2f}"  # log1.txt
_LAND = "You land."  # log1.txt
_WORN_OFF = "Your {spell} spell has worn off."  # SpellWornOffOtherTests
_FADE_SELF = "You feel cleansed."  # SpellWornOffSelfTests (a spells_us fade text)
_CAST = "You begin casting {spell}."  # YouBeginCastingTests
_MOB_CAST = "{mob} begins to cast a spell."  # SpellCastingTests
_EXPERIENCE = "You gain experience!!"  # ConTests / RandomTests
_PARTY_EXPERIENCE = "You gain party experience!!"  # ConTests
_SLAIN_BY = "{target} has been slain by {attacker}!"  # SlainTests
_YOU_SLEW = "You have slain {target}!"  # SlainTests
_DIED = "{target} died."  # SlainTests
_GROUP_TELL = "{who} tells the group, 'wiki says he can be in 1 of 2 locations'"  # CommsTests
_RAID_TELL = "{who} tells the raid,  'assist {who2}'"  # CommsTests shape
_GUILD = "You say to your guild, 'nice'"  # CommsTests
_TELL = "{who} tells you, 'omw'"  # CommsTests
_SAY = "{who} says, 'i am a new warrior'"  # CommsTests
_AUCTION = "{who} auctions, 'WTS bone chips 7p per stack pst'"  # CommsTests
_OOC = "{who} says out of character, 'grats'"  # CommsTests
_SHOUT = "{who} shouts, 'ASSIST {who} on --- [ {target} ]'"  # CommsTests
_ENGAGE = "{mob} engages {who}!"  # FteTests
_CH_CALL = "{who} tells the group, 'CA {n:03d} CH -- {target}'"  # CompleteHealCommsTests
_PET_ORDER = "{pet} tells you, 'Attacking {target} Master.'"  # PetTests
_PET_SPAWN = "{pet} says 'At your service Master.'"  # PetTests
_ROLL = "**A Magic Die is rolled by {who}."  # RandomTests
_ZONED = "You have entered The Feerrott."  # YouZonedTests
_FACTION = "Your faction standing with Bloodsabers got worse."  # FactionTests
_GLOW = "Your Mask of Deception begins to glow."  # YourItemBeginsToGlowTests

SPELLS = ("Clarity", "Root", "Envenomed Bolt", "Venom of the Snake", "Pacify")


def _stamp(when: datetime) -> str:
    return when.strftime("[%a %b %d %H:%M:%S %Y]")


class _Writer:
    """Accumulates stamped lines for one round (one in-game second)."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._when = BASE_TIME

    def second(self, index: int) -> None:
        self._when = BASE_TIME + timedelta(seconds=index)

    def add(self, message: str) -> None:
        self.lines.append(f"{_stamp(self._when)} {message}")


def solo_lines(seconds: int = 240) -> list[str]:
    """One player soloing and running between camps: ~2.8 lines/second.

    A melee round is two lines (your hit, the mob's), a miss lands every
    third round, and ``/loc`` fires on the 6-second cadence a moving player
    produces with location sharing on. Kills, casts and the odd tell are the
    punctuation. Movement-heavy by design: those lines match late in the
    chain or not at all.
    """
    out = _Writer()
    for i in range(seconds):
        out.second(i)
        mob = MOBS[i % len(MOBS)]
        damage = 12 + (i % 40)
        out.add(_MELEE_YOU.format(target=mob, damage=damage))
        out.add(_MOB_HIT.format(attacker=mob, damage=damage // 2 + 3))
        if i % 3 == 0:
            out.add(_MELEE_MISS.format(target=mob))
        if i % 6 == 0:
            out.add(_LOCATION.format(x=100.0 + i, y=-200.0 - i, z=3.75))
        if i % 12 == 0:
            out.add(_CAST.format(spell=SPELLS[i % len(SPELLS)]))
        if i % 20 == 0:
            out.add(_YOU_SLEW.format(target=mob))
            out.add(_EXPERIENCE)
        if i % 31 == 0:
            out.add(_WORN_OFF.format(spell=SPELLS[(i // 31) % len(SPELLS)]))
        if i % 40 == 0:
            out.add(_TELL.format(who=PLAYERS[i % len(PLAYERS)]))
        if i % 60 == 0:
            out.add(_LAND)
            out.add(_ZONED)
    return out.lines


def group_lines(seconds: int = 240) -> list[str]:
    """A six-person group in a dungeon: ~7 lines/second.

    Three other players swinging, a pet, buffs expiring, group chat, and a
    kill roughly every eight seconds — the pull-fight-loot rhythm of a camp.
    More lines match early here than in ``solo``, which is the difference
    worth measuring.
    """
    out = _Writer()
    for i in range(seconds):
        out.second(i)
        mob = MOBS[i % len(MOBS)]
        damage = 20 + (i % 55)
        out.add(_MELEE_YOU.format(target=mob, damage=damage))
        out.add(_MOB_HIT.format(attacker=mob, damage=damage // 3 + 5))
        for offset in range(3):
            attacker = PLAYERS[(i + offset) % len(PLAYERS)]
            out.add(_MELEE_HIT.format(attacker=attacker, target=mob, damage=damage + offset * 4))
        if i % 2 == 0:
            out.add(_MELEE_MISS.format(target=mob))
        if i % 3 == 0:
            out.add(_PET_ORDER.format(pet="Gobaner", target=mob))
        if i % 4 == 0:
            out.add(_NON_MELEE.format(target=mob, damage=damage + 30))
        if i % 5 == 0:
            out.add(_GROUP_TELL.format(who=PLAYERS[i % len(PLAYERS)]))
        if i % 6 == 0:
            out.add(_LOCATION.format(x=-561.0 + i, y=-5043.0 - i, z=-194.40))
        if i % 8 == 0:
            out.add(_SLAIN_BY.format(target=mob, attacker=PLAYERS[i % len(PLAYERS)]))
            out.add(_PARTY_EXPERIENCE)
        if i % 9 == 0:
            out.add(_MOB_CAST.format(mob=mob))
        if i % 11 == 0:
            out.add(_CAST.format(spell=SPELLS[i % len(SPELLS)]))
        if i % 17 == 0:
            out.add(_WORN_OFF.format(spell=SPELLS[(i // 17) % len(SPELLS)]))
            out.add(_FADE_SELF)
        if i % 23 == 0:
            out.add(_ROLL.format(who=PLAYERS[i % len(PLAYERS)]))
            out.add(_GLOW)
        if i % 29 == 0:
            out.add(_PET_SPAWN.format(pet="Gobaner"))
            out.add(_FACTION)
        if i % 37 == 0:
            out.add(_DIED.format(target=mob))
            out.add(_GUILD)
    return out.lines


def raid_lines(seconds: int = 240) -> list[str]:
    """A target raid on a dragon: ~21 lines/second.

    Melee from eight attackers in range (a raid produces far more than the
    log shows — EQ only prints what is near you), the mob swinging back,
    nukes landing as non-melee, a CH rotation call every three seconds, raid
    chat, and the shouts and ``/loc`` frames that ride along. This is the
    profile that decides whether the driver thread keeps up, and the one the
    plugin-dispatch benchmarks replay.
    """
    out = _Writer()
    for i in range(seconds):
        out.second(i)
        damage = 40 + (i % 90)
        for offset in range(8):
            attacker = RAIDERS[(i + offset) % len(RAIDERS)]
            out.add(
                _MELEE_HIT.format(attacker=attacker, target=RAID_TARGET, damage=damage + offset)
            )
        out.add(_MELEE_YOU.format(target=RAID_TARGET, damage=damage + 11))
        out.add(_BACKSTAB.format(target=RAID_TARGET, damage=damage * 4))
        for offset in range(3):
            out.add(_MOB_HIT.format(attacker=RAID_TARGET, damage=200 + offset * 7))
        for offset in range(4):
            out.add(_NON_MELEE.format(target=RAID_TARGET, damage=damage * 2 + offset))
        out.add(_MELEE_MISS.format(target=RAID_TARGET))
        out.add(
            _RAID_TELL.format(who=RAIDERS[i % len(RAIDERS)], who2=RAIDERS[(i + 3) % len(RAIDERS)])
        )
        if i % 2 == 0:
            out.add(_MOB_CAST.format(mob=RAID_TARGET))
        if i % 3 == 0:
            out.add(
                _CH_CALL.format(
                    who=RAIDERS[i % len(RAIDERS)],
                    n=(i // 3) % 999,
                    target=RAIDERS[(i + 1) % len(RAIDERS)],
                )
            )
        if i % 4 == 0:
            out.add(_LOCATION.format(x=1200.0 + i, y=-330.0 - i, z=12.5))
        if i % 5 == 0:
            out.add(_SHOUT.format(who=RAIDERS[i % len(RAIDERS)], target=RAID_TARGET))
        if i % 7 == 0:
            out.add(_CAST.format(spell=SPELLS[i % len(SPELLS)]))
            out.add(_OOC.format(who=RAIDERS[i % len(RAIDERS)]))
        if i % 11 == 0:
            out.add(_WORN_OFF.format(spell=SPELLS[(i // 11) % len(SPELLS)]))
            out.add(_FADE_SELF)
            out.add(_SAY.format(who=RAIDERS[i % len(RAIDERS)]))
        if i % 13 == 0:
            out.add(_ENGAGE.format(mob=RAID_TARGET, who=RAIDERS[i % len(RAIDERS)]))
        if i % 19 == 0:
            out.add(_AUCTION.format(who=RAIDERS[i % len(RAIDERS)]))
        if i % 60 == 59:
            out.add(_SLAIN_BY.format(target=RAID_TARGET, attacker=RAIDERS[i % len(RAIDERS)]))
            out.add(_EXPERIENCE)
    return out.lines


PROFILES = {
    "solo": solo_lines,
    "group": group_lines,
    "raid": raid_lines,
}


def profile(name: str, seconds: int = 240) -> list[str]:
    return PROFILES[name](seconds)


def corpus_lines() -> list[str]:
    """The EQtoolsTests capture itself, for a fourth "this is real" data point.

    Small (93 lines) and unrepresentative of combat, which is exactly why the
    three profiles above exist — but it is the one thing here that is a
    capture rather than a composition, so a benchmark that replays it is
    measuring traffic nobody arranged.
    """
    path = Path(__file__).resolve().parents[1] / "fixtures" / "logfiles" / "log1.txt"
    with path.open(encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in handle]


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(PROFILES))
    parser.add_argument("--seconds", type=int, default=240)
    parser.add_argument(
        "--out",
        type=Path,
        help="write to this file instead of stdout (e.g. an eqlog_Name_server.txt "
        "in a scratch log directory, to drive the real app)",
    )
    args = parser.parse_args()
    lines = profile(args.profile, args.seconds)
    text = "\n".join(lines) + "\n"
    if args.out is None:
        print(text, end="")
    else:
        args.out.write_text(text, encoding="utf-8")
        print(f"{len(lines)} lines -> {args.out}")


if __name__ == "__main__":
    _main()
