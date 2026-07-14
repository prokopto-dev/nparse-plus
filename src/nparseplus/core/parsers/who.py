"""/who output parser (port of EQTool PlayerWhoLogParse.cs).

Publishes WhoEvent on the "Players on EverQuest:" header and a
WhoPlayerEvent per player row while inside a /who block. The block ends at
any line that is neither a player row nor the "---" separator.
"""

from __future__ import annotations

from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import WhoEvent, WhoPlayer, WhoPlayerEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_PLAYERS_ON_EVERQUEST = "Players on EverQuest:"
_DASHES = "---------------------------"

_CLASS_MAPPING: dict[PlayerClass, tuple[str, ...]] = {
    PlayerClass.BARD: ("Bard", "Minstrel", "Troubadour", "Virtuoso"),
    PlayerClass.CLERIC: ("Cleric", "Vicar", "Templar", "High Priest"),
    PlayerClass.DRUID: ("Druid", "Wanderer", "Preserver", "Hierophant"),
    PlayerClass.ENCHANTER: ("Enchanter", "Illusionist", "Beguiler", "Phantasmist"),
    PlayerClass.MAGICIAN: ("Magician", "Elementalist", "Conjurer", "Arch Mage"),
    PlayerClass.MONK: ("Monk", "Disciple", "Master", "Grandmaster"),
    PlayerClass.NECROMANCER: ("Necromancer", "Heretic", "Defiler", "Warlock"),
    PlayerClass.PALADIN: ("Paladin", "Cavalier", "Knight", "Crusader"),
    PlayerClass.RANGER: ("Ranger", "Pathfinder", "Outrider", "Warder"),
    PlayerClass.ROGUE: ("Rogue", "Rake", "Blackguard", "Assassin"),
    PlayerClass.SHADOW_KNIGHT: ("Shadow Knight", "Reaver", "Revenant", "Grave Lord"),
    PlayerClass.SHAMAN: ("Shaman", "Mystic", "Luminary", "Oracle"),
    PlayerClass.WARRIOR: ("Warrior", "Champion", "Myrmidon", "Warlord"),
    PlayerClass.WIZARD: ("Wizard", "Channeler", "Evoker", "Sorcerer"),
}


def parse_player_info(message: str) -> WhoPlayer | None:
    """Parse one /who row, e.g. ``[60 High Priest] Dany (High Elf) <The Drift>``."""
    if not message.startswith("AFK") and not message.startswith("["):
        return None
    begin_index = message.find("[")
    if begin_index == -1:
        return None
    message = message[begin_index:]
    end_index = message.find("]")
    if end_index == -1:
        return None
    space_index = message.find(" ")
    if space_index == -1:
        return None
    if message.startswith("[MQ2]"):
        return None

    level: int | None = None
    player_class: PlayerClass | None = None
    if space_index < end_index:
        level_string = message[1:space_index].strip()
        try:
            level = int(level_string)
        except ValueError:
            level = None
        class_guess = message[space_index:end_index].strip()
        for cls, titles in _CLASS_MAPPING.items():
            if class_guess in titles:
                player_class = cls
                break

    message = message[end_index + 1 :].strip()
    space_index = message.find(" ")
    name = message[:space_index].strip() if space_index != -1 else message

    guild_name: str | None = None
    carrot_index = message.find("<")
    if carrot_index != -1:
        end_index = message.find(">")
        guild_name = message[carrot_index:end_index].strip("<> ")

    return WhoPlayer(name=name, level=level, player_class=player_class, guild_name=guild_name)


class PlayerWhoLogParse:
    def __init__(self) -> None:
        self._starting_who_of_zone = False

    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        player = parse_player_info(message)
        if player is not None and self._starting_who_of_zone:
            ctx.bus.publish(
                WhoPlayerEvent(
                    timestamp=line.timestamp,
                    line=message,
                    line_number=line.line_number,
                    player=player,
                )
            )
            return True

        if message == _PLAYERS_ON_EVERQUEST:
            self._starting_who_of_zone = True
            ctx.bus.publish(
                WhoEvent(timestamp=line.timestamp, line=message, line_number=line.line_number)
            )
            return True

        self._starting_who_of_zone = message == _DASHES and self._starting_who_of_zone
        return False
