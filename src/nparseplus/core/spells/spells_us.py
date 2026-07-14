"""spells_us.txt loader and lookup dictionaries.

Port of EQTool's ``Services/Spells/ParseSpells_spells_us.cs`` (field parsing,
ignore lists, per-spell fixups, epic cast times) and ``Models/EQSpells.cs``
(the lookup dictionaries the parsers query). All dictionary lookups are
case-insensitive, mirroring the C# ``StringComparer.OrdinalIgnoreCase`` maps.

One deliberate divergence: EQTool keeps the "spell the user is casting right
now" on ``ActivePlayer``; here that session state lives in
``SpellBook.casting`` so the spell parsers can reach it via
``ParseContext.spells`` without widening the shared player object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path

from nparseplus.core.enums import PlayerClass, ResistType, SpellBenefitDetriment, SpellType
from nparseplus.core.spells.models import Spell

logger = logging.getLogger(__name__)

# EQSpells.cs constants.
SPACE_YOU = " You "
SPELL_HAS_WORN_OFF = "spell has worn off."
_INVIS_MESSAGE = " fades away"
MINIMUM_RECAST_FOR_YOU_COOLDOWN_TIMER_S = 18
MINIMUM_RECAST_FOR_OTHER_COOLDOWN_TIMER_S = 60

# --- ParseSpells_spells_us.cs ignore/fixup tables ----------------------------

_IGNORE_SPELLS = {
    "Shield of the Ring",
    "FireElementalAttack2",
    "Outbreak",
    "Paroxysm of Zek",
    "Dark Madness",
    "Peace of the Disciple Strike",
    "HandOfHolyVengeanceIRecourse",
    "HandOfHolyVengeanceIIRecourse",
    "HandOfHolyVengeanceIVRecourse",
    "HandOfHolyVengeanceVRecourse",
    "Complete Heal",
    "Denon`s Disruptive Discord",
    "Chords of Dissonance",
    "Infection Test 1",
    "Infection Test 2",
    "Levitate Test",
    "Test GLT",
    "Test GMD",
    "Test Shield",
    "Test GACD",
    "Bond of Sathir",
    "Soul Consumption R.",
    "Soul Claw Strike",
    "Malevolent Vex",
    "Caustic Mist",
    "Ojun Roar",
    "Dragon Bellow",
    "Ice Comet",
    "IceBoneFrostBurst",
    "FrostAOE",
    "Ring of Winter",
    "Frost Shards",
    "Umbral Rot",
    "Grimling Rot",
    "Crystal Roar",
    "Rimebone Frost Burst",
    "Trushar's Frost",
    "Icicle Shock",
    "Shock of Frost",
    "Talendor's Immolating Breath",
    "Lava Breath - Test",
    "Vengeance of the Undying",
    "Gift of A'err",
    "Zun`Muram's Terror",
    "Extended Regeneration",
    "Aura of Battle",
    "Nature's Recovery",
    "Regrowth of Dar Khura",
    "Vampyre Regeneration",
    "Aura of Courage",
    "Aura of Daring",
    "Aura of Bravery",
    "Aura of Valor",
    "Aura of Resolution",
    "Refreshment",
}

_IGNORE_IDS = {6615}

_IGNORE_ROMAN_NUMERALS = (
    " I",
    " II",
    " III",
    " IV",
    " V",
    " VI",
    " VII",
    " VIII",
    " IX",
    " X",
    " XI",
    " XII",
    " XIII",
    " XIV",
)

_GOOD_ROMAN_NUMERAL_SPELLS = (
    "Cannibalize",
    "Rune",
    "Yaulp",
    "Burnout",
    "Contact Poison",
    "Berserker Madness",
    "Brittle Haste",
    "Feeble Mind",
    "Injected Poison",
    "Clarity",
    "Monster Summoning",
    "Dizzy",
    "Blinding Poison",
)

_SPELLS_CASTABLE_BY_EVERYONE = (
    "Aura of Blue Petals",
    "Aura of White Petals",
    "Aura of Red Petals",
    "Aura of Black Petals",
    "Shield of the Eighth",
    "Frostreaver's Blessing",
)

# name -> (cast time ms, class granted at level 46)
_EPIC_SPELLS: dict[str, tuple[int, PlayerClass]] = {
    "Wrath of Nature": (9000, PlayerClass.DRUID),
    "Speed of the Shissar": (6000, PlayerClass.ENCHANTER),
    "Torment of Shadows": (9000, PlayerClass.NECROMANCER),
    "Earthcall": (0, PlayerClass.RANGER),
    "Soul Consumption": (0, PlayerClass.SHADOW_KNIGHT),
    "Curse of the Spirits": (9000, PlayerClass.SHAMAN),
    "Barrier of Force": (15000, PlayerClass.WIZARD),
    "Dance of the Blade": (0, PlayerClass.BARD),
    "Celestial Tranquility": (0, PlayerClass.MONK),
    "Seething Fury": (0, PlayerClass.ROGUE),
    "Manifest Elements": (0, PlayerClass.MAGICIAN),
}

# DescrNumber values used by the loader filters (enum kept as raw ints).
DESCR_ILLUSION_OTHER = 48
DESCR_ILLUSION_PLAYER = 49
_DESCR_SKIPPED = {116, 57, 113, 28}  # ThePlanes, Luclin, Taelosia, Discord
_DESCR_TRAPS2 = 131  # everything >= this is skipped

# Raw target types dropped at load (GetSpells "ignorespelltypes" + IgnoreSpellTypes).
_IGNORE_TARGET_TYPES = {
    int(SpellType.TARGETED_AOE_LIFE_TAP),
    int(SpellType.AOE_CASTER),
    int(SpellType.AREA_PC_ONLY),
    int(SpellType.AREA_NPC_ONLY),
    int(SpellType.AOE_PC_V2),
    int(SpellType.RAG_ZHEZUM_SPECIAL),
    int(SpellType.CORPSE),
}

_NAME_SUBSTRING_SKIPS = (
    "Translocate",
    "Translocation",
    "Prayer to ",
    " Port",
    "Journey:",
    "Ring of ",
    "Portal",
)


@dataclass
class _RawSpell:
    """Mutable working copy of one spells_us.txt row (SpellBase before Map)."""

    id: int
    name: str
    cast_on_you: str
    cast_on_other: str
    spell_fades: str
    casttime: int
    recast_time: int
    buffdurationformula: int
    pvp_buffdurationformula: int
    buffduration: int
    resisttype: int
    target_type: int
    benefit_detriment: int
    descr_number: int
    spell_icon: int
    classes: dict[PlayerClass, int]


@dataclass
class CastingState:
    """The spell the user is mid-cast on (EQTool: ActivePlayer.UserCastingSpell)."""

    spell: Spell | None = None
    started_at: datetime | None = None

    def begin(self, spell: Spell, timestamp: datetime) -> None:
        self.spell = spell
        self.started_at = timestamp

    def clear(self) -> None:
        self.spell = None
        self.started_at = None


@dataclass
class SpellBook:
    """Spell database + the lookup dicts EQSpells builds (case-insensitive)."""

    spells: list[Spell] = field(default_factory=list)
    npcs: frozenset[str] = frozenset()
    casting: CastingState = field(default_factory=CastingState)
    # Keys are casefolded; use the accessor methods rather than the dicts.
    _all_spells: dict[str, Spell] = field(default_factory=dict)
    _cast_other_spells: dict[str, list[Spell]] = field(default_factory=dict)
    _cast_on_you_spells: dict[str, list[Spell]] = field(default_factory=dict)
    _you_cast_spells: dict[str, list[Spell]] = field(default_factory=dict)
    _worn_off_spells: dict[str, list[Spell]] = field(default_factory=dict)

    def spell_by_name(self, name: str) -> Spell | None:
        return self._all_spells.get(name.casefold())

    def you_cast(self, spell_name: str) -> list[Spell]:
        """Spells matching 'You begin casting <name>.' (EQSpells.YouCastSpells)."""
        return self._you_cast_spells.get(spell_name.casefold(), [])

    def cast_on_you(self, message: str) -> list[Spell]:
        return self._cast_on_you_spells.get(message.casefold(), [])

    def cast_on_other(self, message: str) -> list[Spell]:
        return self._cast_other_spells.get(message.casefold(), [])

    def worn_off(self, message: str) -> list[Spell]:
        return self._worn_off_spells.get(message.casefold(), [])

    def is_npc(self, name: str) -> bool:
        # MasterNPCList uses an OrdinalIgnoreCase set; npcs holds casefolded names.
        return name.casefold() in self.npcs

    def _index(self) -> None:
        """Port of EQSpells.BuildSpellInfo (icons themselves are not loaded)."""
        for spell in self.spells:
            if not _has_spell_icon(spell.spell_icon):
                continue
            key = spell.name.casefold()
            if key not in self._all_spells:
                self._all_spells[key] = spell

            if spell.cast_on_other:
                if _INVIS_MESSAGE in spell.cast_on_other:
                    # Can't tell gate from invis for others; EQTool skips these.
                    pass
                else:
                    self._cast_other_spells.setdefault(spell.cast_on_other.casefold(), []).append(
                        spell
                    )

            if spell.name and any(level > 0 for level in spell.class_levels.values()):
                self._you_cast_spells.setdefault(key, []).append(spell)

            if spell.cast_on_you:
                you_key = spell.cast_on_you.casefold()
                existing = self._cast_on_you_spells.get(you_key)
                if existing is None:
                    self._cast_on_you_spells[you_key] = [spell]
                elif not (spell.class_levels and spell.spell_type is SpellType.SELF):
                    existing.append(spell)

            if spell.spell_fades:
                self._worn_off_spells.setdefault(spell.spell_fades.casefold(), []).append(spell)


def _has_spell_icon(spell_icon: int) -> bool:
    """EQTool only keeps spells whose icon maps into sheets 1..7 (Spell.Map)."""
    sheet = int(spell_icon / 36.0) + 1
    return 0 < sheet <= 7


def parse_spell_line(line: str) -> _RawSpell | None:
    """Port of ParseSpells_spells_us.ParseP99Line / ParseLine (offset 0)."""
    s = line.split("^")
    try:
        classes: dict[PlayerClass, int] = {}
        for i in range(104, 104 + int(PlayerClass.ENCHANTER) + 1):
            level = int(s[i])
            if 0 <= level < 255:
                classes[PlayerClass(i - 104)] = level
        return _RawSpell(
            id=int(s[0]),
            name=s[1],
            cast_on_you=s[6].strip(),
            cast_on_other=s[7].strip(),
            spell_fades=s[8].strip(),
            casttime=int(s[13]),
            recast_time=int(s[15]),
            buffdurationformula=int(s[16]),
            pvp_buffdurationformula=int(s[181]) if len(s) >= 182 else 0,
            buffduration=int(s[17]),
            resisttype=int(s[85]),
            target_type=int(s[98]),
            benefit_detriment=int(s[83]),
            descr_number=int(s[157]) if len(s) >= 158 else 0,
            spell_icon=int(s[144]),
            classes=classes,
        )
    except (ValueError, IndexError):
        return None


def _apply_epic_fixup(raw: _RawSpell) -> None:
    epic = _EPIC_SPELLS.get(raw.name)
    if epic is not None:
        raw.casttime, epic_class = epic[0], epic[1]
        raw.classes[epic_class] = 46


def _should_skip(raw: _RawSpell) -> bool:
    """All the `continue` filters of ParseSpells_spells_us.GetSpells, in order."""
    if not raw.name and not raw.cast_on_you and raw.buffduration <= 0 and raw.spell_icon <= 0:
        return True
    if raw.id in _IGNORE_IDS or raw.target_type in _IGNORE_TARGET_TYPES:
        return True
    if any(part in raw.name for part in _NAME_SUBSTRING_SKIPS):
        return True
    if raw.classes and all(60 < level <= 255 for level in raw.classes.values()):
        return True
    if raw.name.startswith(("GM ", "Guide ", "NPC")):
        return True
    lowered = raw.name.lower()
    if "test" in lowered or "beta" in lowered:
        return True
    if raw.resisttype > int(ResistType.DISEASE):
        return True
    if not raw.name.startswith(("Alter Plane", "Primal Essence")) and (
        raw.descr_number in _DESCR_SKIPPED or raw.descr_number >= _DESCR_TRAPS2
    ):
        return True
    if raw.name in _IGNORE_SPELLS:
        return True
    return not any(
        raw.name.startswith(good) for good in _GOOD_ROMAN_NUMERAL_SPELLS
    ) and raw.name.endswith(_IGNORE_ROMAN_NUMERALS)


def _apply_fixups(raw: _RawSpell) -> None:
    """The per-spell data corrections in GetSpells."""
    if raw.name == "Defensive Discipline" and raw.cast_on_you.endswith(".."):
        raw.cast_on_you = raw.cast_on_you.replace("..", ".")
    if raw.name.startswith("Primal Essence"):
        raw.classes.setdefault(PlayerClass.SHAMAN, 35)
    if raw.name == "Pacify":
        raw.buffduration = 35
    if raw.name == "Bind Sight":  # spells file is not correct
        raw.buffduration = 999
    if raw.name in ("Wake of Tranquility", "Rampage"):
        raw.buffduration = 1000  # any number to make the duration work correctly
    if raw.name == "LowerElement":
        raw.classes.setdefault(PlayerClass.WIZARD, 51)
    if raw.name == "Soul Well":
        raw.casttime = 13 * 1000
        raw.classes.setdefault(PlayerClass.NECROMANCER, 50)
    if raw.name in _SPELLS_CASTABLE_BY_EVERYONE:
        raw.classes.setdefault(PlayerClass.OTHER, 46)
    if raw.name == "Maniacal Strength":
        raw.name = "Manicial Strength"
    if raw.name == "Lay on Hands":
        raw.recast_time = 4320000  # 72 minutes in milliseconds


def _to_spell(raw: _RawSpell) -> Spell:
    if raw.benefit_detriment == 0:
        benefit = SpellBenefitDetriment.DETRIMENTAL
    elif raw.benefit_detriment == 2:
        benefit = SpellBenefitDetriment.BENEFICIAL_GROUP_ONLY
    else:
        benefit = SpellBenefitDetriment.BENEFICIAL
    try:
        spell_type = SpellType(raw.target_type)
    except ValueError:
        # A handful of rows carry target types outside EQTool's enum (e.g. 2,
        # group teleport). C# keeps the raw value; none of the ported logic
        # distinguishes them from single-target, so coerce.
        spell_type = SpellType.SINGLE
    try:
        resist = ResistType(raw.resisttype)
    except ValueError:
        resist = ResistType.NONE
    return Spell(
        id=raw.id,
        name=raw.name,
        cast_on_you=raw.cast_on_you,
        cast_on_other=raw.cast_on_other,
        spell_fades=raw.spell_fades,
        cast_time_ms=raw.casttime,
        recast_time_ms=raw.recast_time,
        buff_duration_formula=raw.buffdurationformula,
        pvp_buff_duration_formula=raw.pvp_buffdurationformula,
        buff_duration_ticks=raw.buffduration,
        resist_type=resist,
        spell_type=spell_type,
        class_levels=raw.classes,
        spell_icon=raw.spell_icon,
        descr_number=raw.descr_number,
        benefit_detriment=benefit,
    )


def load_master_npc_list() -> frozenset[str]:
    """Comma-separated NPC names bundled with the package (MasterNPCList).

    Names are casefolded to mirror the C# OrdinalIgnoreCase HashSet.
    """
    try:
        text = resources.files("nparseplus.data").joinpath("npcs/master_npc_list.txt").read_text()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        logger.warning("master_npc_list.txt not found; NPC target detection disabled")
        return frozenset()
    names = frozenset(name.strip().casefold() for name in text.split(",") if name.strip())
    # Our converted list contains a literal "You" entry that EQTool's does not;
    # it would reclassify the ' You ' self group as an NPC target. Drop it.
    return names - {"you"}


def load_spell_book(path: Path, npcs: frozenset[str] | None = None) -> SpellBook:
    """Parse a spells_us.txt and build all lookup dictionaries."""
    by_name: dict[str, _RawSpell] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = parse_spell_line(line)
        if raw is None:
            continue
        _apply_epic_fixup(raw)
        if _should_skip(raw):
            continue
        _apply_fixups(raw)

        if raw.name == "Levitate":
            peggy = parse_spell_line(line)
            if peggy is not None:
                peggy.name = "Peggy Levitate"
                peggy.buffduration = 120
                peggy.buffdurationformula = 12
                peggy.casttime = 6000
                by_name.setdefault(peggy.name, peggy)

        existing = by_name.get(raw.name)
        if existing is not None:
            # Keep the classed variant when a classless duplicate appears.
            if not (existing.classes and not raw.classes):
                by_name[raw.name] = raw
        else:
            by_name[raw.name] = raw

    book = SpellBook(
        spells=[_to_spell(raw) for raw in by_name.values()],
        npcs=npcs if npcs is not None else load_master_npc_list(),
    )
    book._index()
    logger.info("loaded %d spells from %s", len(book.spells), path)
    return book
