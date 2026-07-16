"""The ordered parser chain — first match consumes the line.

Order ports EQTool's LogParser.cs constructor (lines 39-70): Damage, Faction,
Pet, and Comms are forced to the very front (most common lines), followed by
the You*/Spell* casting parsers, then everything else. Within each block the
patterns are mutually exclusive, so relative order is for efficiency, not
correctness.
"""

from __future__ import annotations

from nparseplus.core.parsers.base import LineParser
from nparseplus.core.parsers.boat import BoatParser
from nparseplus.core.parsers.camp import CampParser
from nparseplus.core.parsers.comms import CommsParser
from nparseplus.core.parsers.consider import ConLogParse
from nparseplus.core.parsers.damage import DamageParser
from nparseplus.core.parsers.discipline_cooldown import DisciplineCooldownParser
from nparseplus.core.parsers.exp_gained import ExpGainedParser
from nparseplus.core.parsers.faction import FactionParser
from nparseplus.core.parsers.finished_memorizing import YouHaveFinishedMemorizingParser
from nparseplus.core.parsers.fte import FTEParser
from nparseplus.core.parsers.group_leader import GroupLeaderParser
from nparseplus.core.parsers.level import PlayerLevelDetectionParser
from nparseplus.core.parsers.loading_please_wait import LoadingPleaseWaitParser
from nparseplus.core.parsers.location import LocationParser
from nparseplus.core.parsers.mend_wounds import MendWoundsParser
from nparseplus.core.parsers.pet import PetParser
from nparseplus.core.parsers.quake import QuakeParser
from nparseplus.core.parsers.random import RandomParser
from nparseplus.core.parsers.resist import ResistParser
from nparseplus.core.parsers.ring_war import RingWarParser
from nparseplus.core.parsers.slain import SlainParser
from nparseplus.core.parsers.spell_cast_on_other import SpellCastOnOtherParser
from nparseplus.core.parsers.spell_interrupted import YourSpellInterruptedParser
from nparseplus.core.parsers.spell_worn_off import SpellWornOffOtherParser, SpellWornOffSelfParser
from nparseplus.core.parsers.welcome import WelcomeParser
from nparseplus.core.parsers.who import PlayerWhoLogParse
from nparseplus.core.parsers.you_begin_casting import YouBeginCastingParser
from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
from nparseplus.core.parsers.you_forget import YouForgetParser
from nparseplus.core.parsers.you_zoned import YouZonedParser
from nparseplus.core.parsers.your_item_begins_to_glow import YourItemBeginsToGlowParser


def build_parser_chain() -> list[LineParser]:
    return [
        # Forced front-runners (LogParser.cs: most common lines first).
        DamageParser(),
        FactionParser(),
        PetParser(),
        CommsParser(),
        # You*/Spell* casting block (C# selects these by class-name prefix).
        YouBeginCastingParser(),
        YouFinishCastingParser(),  # also handles SpellCastOnYou
        YouZonedParser(),
        YouForgetParser(),
        YouHaveFinishedMemorizingParser(),
        YourItemBeginsToGlowParser(),
        YourSpellInterruptedParser(),
        SpellCastOnOtherParser(),
        SpellWornOffSelfParser(),
        SpellWornOffOtherParser(),
        # The rest.
        SlainParser(),
        ResistParser(),
        RandomParser(),
        FTEParser(),
        QuakeParser(),
        RingWarParser(),
        BoatParser(),
        LocationParser(),
        GroupLeaderParser(),
        DisciplineCooldownParser(),
        PlayerWhoLogParse(),
        PlayerLevelDetectionParser(),
        MendWoundsParser(),
        CampParser(),
        WelcomeParser(),
        LoadingPleaseWaitParser(),
        ConLogParse(),
        ExpGainedParser(),
    ]
