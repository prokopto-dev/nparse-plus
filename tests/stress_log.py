"""Deterministic synthetic raid-combat log for stress/benchmark tests.

Generates a few thousand interleaved lines shaped like heavy raid combat:
melee/spell damage (DamageEvents), periodic /loc fixes (PlayerLocationEvents
that drive the map), CH chain calls, comms chatter, and misses. Deterministic
by construction — no randomness — so replay assertions can be exact.
"""

from __future__ import annotations

from datetime import datetime, timedelta

BASE_TIME = datetime(2026, 7, 15, 21, 0, 0)

ATTACKERS = ["Grunk", "Sneakstab", "Bowaflame", "Xantik"]
MOB = "Lord Nagafen"


def raid_lines(rounds: int = 500) -> list[str]:
    """~7 lines per round: 3 melee hits, a miss, chatter, a CH call every 5
    rounds, and a /loc every 10 rounds."""
    lines: list[str] = []
    now = BASE_TIME

    def stamp() -> str:
        return now.strftime("[%a %b %d %H:%M:%S %Y]")

    for i in range(rounds):
        now = BASE_TIME + timedelta(seconds=i)
        attacker = ATTACKERS[i % len(ATTACKERS)]
        damage = 20 + (i % 60)
        lines.append(f"{stamp()} {attacker} slashes {MOB} for {damage} points of damage.")
        lines.append(f"{stamp()} You slash {MOB} for {damage + 5} points of damage.")
        lines.append(f"{stamp()} {MOB} hits YOU for {damage + 11} points of damage.")
        lines.append(f"{stamp()} {attacker} tries to pierce {MOB}, but misses!")
        lines.append(f"{stamp()} {attacker} tells the raid,  'stress line {i}'")
        if i % 5 == 0:
            lines.append(f"{stamp()} Clericone tells the group, 'CA 001 CH -- Tanky'")
        if i % 10 == 0:
            lines.append(f"{stamp()} Your Location is {100.0 + i:.2f}, {-200.0 - i:.2f}, 3.75")
    return lines
