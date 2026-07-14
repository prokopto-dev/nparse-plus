"""Melee/non-melee damage parser (port of EQTool DamageParser.cs).

Matches, in order: your hits, your misses, others' hits, others' misses,
and non-melee damage. Also fires ClassDetectedEvent(Rogue) on your first
backstab, and guesses NPC levels from max melee hits.
"""

from __future__ import annotations

import re
from importlib import resources

from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import ClassDetectedEvent, DamageEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_YOU_HIT_RE = re.compile(
    r"^You (?P<dmg_type>hit|slash|pierce|crush|claw|bite|sting|maul|gore|punch|kick"
    r"|backstab|bash|slice|strike) (?P<target_name>[\w` ]+) for (?P<damage>[\d]+)"
    r" point(s)? of damage"
)
_YOU_MISS_RE = re.compile(
    r"^You try to (?P<dmg_type>hit|slash|pierce|crush|claw|bite|sting|maul|gore|punch|kick"
    r"|backstab|bash|slice|strike) (?P<target_name>[\w` ]+), but"
)
_OTHER_HIT_RE = re.compile(
    r"^(?P<attacker_name>[\w`'-. ]+?) (?P<dmg_type>hits|slashes|pierces|crushes|claws|bites"
    r"|stings|mauls|gores|punches|kicks|backstabs|bashes|slices|strikes)"
    r" (?P<target_name>[\w` ]+) for (?P<damage>[\d]+) point(s)? of damage"
)
_OTHERS_MISS_RE = re.compile(
    r"^(?P<attacker_name>[\w` ]+?) tries to (?P<dmg_type>hit|slash|pierce|crush|claw|bite"
    r"|sting|maul|gore|punch|kick|backstab|bash|slice|strike) (?P<target_name>[\w` ]+), but"
)
_NON_MELEE_RE = re.compile(
    r"^(?P<target_name>[\w` ]+) was hit by non-melee for (?P<damage>[\d]+) point(s)? of damage"
)


def _load_npc_names() -> frozenset[str]:
    """Case-insensitive NPC name set (EQToolShared MasterNPCList)."""
    path = resources.files("nparseplus").joinpath("data/npcs/master_npc_list.txt")
    text = path.read_text(encoding="utf-8-sig")
    return frozenset(name.strip().lower() for name in text.split(",") if name.strip())


_NPC_NAMES = _load_npc_names()


def _guess_level(attacker_name: str, damage_done: int, damage_type: str) -> int | None:
    """Port of DamageParser.GuessLevelFromHit — max-hit based level estimate."""
    if (
        damage_done <= 0
        or not attacker_name.strip()
        or attacker_name.lower() == "you"
        or damage_type.lower() in ("kick", "backstab")
    ):
        return None
    lowered = attacker_name.lower()
    added = 20 if ("giant" in lowered or "spectre" in lowered) else 0
    if lowered not in _NPC_NAMES:
        return None
    if damage_done <= 60:
        return int(damage_done / 2.0)
    return int(((damage_done - added - 60.0) / 4.0) + 30.0)


class DamageParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        # Every pattern requires " point… of damage" or a ", but" miss clause.
        if " point" not in message and ", but" not in message:
            return False

        match = _YOU_HIT_RE.match(message)
        if match:
            damage = int(match.group("damage"))
            dmg_type = match.group("dmg_type")
            # First backstab from the active player marks them as a Rogue.
            if dmg_type == "backstab" and ctx.player.player_class != PlayerClass.ROGUE:
                ctx.bus.publish(
                    ClassDetectedEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        player_class=PlayerClass.ROGUE,
                    )
                )
            ctx.bus.publish(
                DamageEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    target_name=match.group("target_name"),
                    attacker_name="You",
                    damage_done=damage,
                    damage_type=dmg_type,
                    level_guess=_guess_level("You", damage, dmg_type),
                )
            )
            return True

        match = _YOU_MISS_RE.match(message)
        if match:
            ctx.bus.publish(
                DamageEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    target_name=match.group("target_name"),
                    attacker_name="You",
                    damage_done=0,
                    damage_type=match.group("dmg_type"),
                    level_guess=None,
                )
            )
            return True

        match = _OTHER_HIT_RE.match(message)
        if match:
            attacker = match.group("attacker_name")
            damage = int(match.group("damage"))
            dmg_type = match.group("dmg_type")
            ctx.bus.publish(
                DamageEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    target_name=match.group("target_name"),
                    attacker_name=attacker,
                    damage_done=damage,
                    damage_type=dmg_type,
                    level_guess=_guess_level(attacker, damage, dmg_type),
                )
            )
            return True

        match = _OTHERS_MISS_RE.match(message)
        if match:
            attacker = match.group("attacker_name")
            dmg_type = match.group("dmg_type")
            ctx.bus.publish(
                DamageEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    target_name=match.group("target_name"),
                    attacker_name=attacker,
                    damage_done=0,
                    damage_type=dmg_type,
                    level_guess=_guess_level(attacker, 0, dmg_type),
                )
            )
            return True

        match = _NON_MELEE_RE.match(message)
        if match:
            ctx.bus.publish(
                DamageEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    target_name=match.group("target_name"),
                    attacker_name="You",
                    damage_done=int(match.group("damage")),
                    damage_type="non-melee",
                    level_guess=None,
                )
            )
            return True

        return False
