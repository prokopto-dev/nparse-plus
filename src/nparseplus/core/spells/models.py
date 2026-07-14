"""Spell domain model — port of EQTool's SpellBase (Models/SpellBase.cs).

Field names keep EQTool's spells_us.txt vocabulary so the loader
(`spells_us.py`) and ported tests read naturally.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nparseplus.core.enums import PlayerClass, ResistType, SpellBenefitDetriment, SpellType


class Spell(BaseModel):
    """One row of spells_us.txt (fields by index; see ParseSpells_spells_us.cs)."""

    model_config = ConfigDict(frozen=True)

    id: int  # field 0
    name: str  # field 1
    cast_on_you: str = ""  # field 6
    cast_on_other: str = ""  # field 7
    spell_fades: str = ""  # field 8
    cast_time_ms: int = 0  # field 13
    recast_time_ms: int = 0  # field 15
    buff_duration_formula: int = 0  # field 16
    pvp_buff_duration_formula: int = 0
    buff_duration_ticks: int = 0  # field 17 (1 tick = 6 seconds)
    resist_type: ResistType = ResistType.NONE  # field 85
    spell_type: SpellType = SpellType.SINGLE  # field 98 (target type)
    class_levels: dict[PlayerClass, int] = {}  # fields 104+ (min level per class)
    spell_icon: int = 0  # field 144
    descr_number: int = 0  # field 157 (DescrNumber; kept raw — file has out-of-enum values)
    benefit_detriment: SpellBenefitDetriment = SpellBenefitDetriment.BENEFICIAL

    @property
    def is_detrimental(self) -> bool:
        return self.benefit_detriment is SpellBenefitDetriment.DETRIMENTAL
