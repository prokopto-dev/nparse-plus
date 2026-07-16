"""Shared enums ported from EQToolShared/Enums and EQTool models.

Numeric values must match the C# ordinals: they travel over the PigParse
network wire (server/class ints) and index into spell data.
"""

from enum import Enum, IntEnum, IntFlag, auto


class PlayerClass(IntEnum):
    # EQToolShared/Enums/PlayerClasses.cs — wire values, do not reorder.
    WARRIOR = 0
    CLERIC = 1
    PALADIN = 2
    RANGER = 3
    SHADOW_KNIGHT = 4
    DRUID = 5
    MONK = 6
    BARD = 7
    ROGUE = 8
    SHAMAN = 9
    NECROMANCER = 10
    WIZARD = 11
    MAGICIAN = 12
    ENCHANTER = 13
    OTHER = 14

    @property
    def display_name(self) -> str:
        return {"SHADOW_KNIGHT": "Shadow Knight"}.get(self.name, self.name.capitalize())


class Server(IntEnum):
    # EQToolShared/Enums/Servers.cs — wire values, do not reorder.
    GREEN = 0
    BLUE = 1
    RED = 2
    QUARM = 3


class MapLocationSharing(IntEnum):
    # EQToolShared/HubModels/SignalrPlayer.cs — wire values, do not reorder.
    EVERYONE = 0
    GUILD_ONLY = 1


class Boat(IntEnum):
    # EQToolShared/Zones.cs Boats — wire values. Member names deliberately keep
    # the C# spelling: they double as the short boat keys in data/zones.json
    # (BoatEvent.boat), so ``Boat[event.boat]`` is the key->wire mapping.
    BarrelBarge = 0
    BloatedBelly = 1
    MaidensVoyage = 2
    NroIcecladBoat = 3


class RollTimerType(IntEnum):
    # EQToolShared/APIModels/RollTimerModel.cs — wire values.
    SCOUT = 1
    QUAKE = 2


class CommsChannel(IntFlag):
    # EQTool/Models/EventModels.cs CommsEvent.Channel
    NONE = 0
    TELL = 1
    SAY = 2
    GROUP = 4
    GUILD = 8
    AUCTION = 16
    OOC = 32
    SHOUT = 64
    ANY = TELL | SAY | GROUP | GUILD | AUCTION | OOC | SHOUT


class PetIncident(Enum):
    # EQTool/Models/EventModels.cs PetEvent.PetIncident
    NONE = auto()
    LEADER = auto()
    RECLAIMED = auto()
    DEATH = auto()
    CREATION = auto()
    GETLOST = auto()
    PETATTACK = auto()
    PETLIFETAP = auto()
    PETFOLLOWME = auto()
    SITSTAND = auto()
    GUARD = auto()
    ANY = auto()


class FactionStatus(Enum):
    GOT_BETTER = "got_better"
    GOT_WORSE = "got_worse"
    COULD_NOT_GET_BETTER = "could_not_get_better"
    COULD_NOT_GET_WORSE = "could_not_get_worse"


class ResistType(IntEnum):
    # EQTool/Models/SpellBase.cs
    NONE = 0
    MAGIC = 1
    FIRE = 2
    COLD = 3
    POISON = 4
    DISEASE = 5
    CHROMATIC = 6
    PRISMATIC = 7
    PHYSICAL = 8
    CORRUPTION = 9


class SpellType(IntEnum):
    # EQTool/Models/SpellBase.cs (spells_us.txt field 98, target type)
    RAG_ZHEZUM_SPECIAL = 0
    LINE_OF_SIGHT = 1
    GROUP_V1 = 3
    POINT_BLANK_AOE = 4
    SINGLE = 5
    SELF = 6
    TARGETED_AOE = 8
    ANIMAL = 9
    UNDEAD = 10
    SUMMONED = 11
    LIFE_TAP = 13
    PET = 14
    CORPSE = 15
    PLANT = 16
    UBER_GIANTS = 17
    UBER_DRAGONS = 18
    TARGETED_AOE_LIFE_TAP = 20
    AOE_UNDEAD = 24
    AOE_SUMMONED = 25
    AOE_CASTER = 32
    NPC_HATE_LIST = 33
    DUNGEON_OBJECT = 34
    MURAMITE = 35
    AREA_PC_ONLY = 36
    AREA_NPC_ONLY = 37
    SUMMONED_PET = 38
    GROUP_NO_PETS = 39
    AOE_PC_V2 = 40
    GROUP_V2 = 41
    SELF_DIRECTIONAL = 42
    GROUP_WITH_PETS = 43
    BEAM = 44


class SpellBenefitDetriment(Enum):
    BENEFICIAL = "beneficial"
    BENEFICIAL_GROUP_ONLY = "beneficial_group_only"
    DETRIMENTAL = "detrimental"
