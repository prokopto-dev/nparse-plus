"""Pet spell/name tables and current-pet state (port of EQTool Models/Pets.cs
and ViewModels/MobInfoComponents/PetViewModel.cs).

The rank/level tables and the master pet-name set ship as data files under
``nparseplus/data/pets`` (EQValet-style source snippets); ``load_pets``
parses them so the tables stay data, not code.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

logger = logging.getLogger(__name__)

# Max number of displayed pet ranks (5 normal + 1 focused), PetViewModel.
RANK_ROWS_COUNT = 6


@dataclass(frozen=True)
class PetRank:
    """Stats for a single pet rank (Models/Pets.cs PetRank)."""

    rank: int
    pet_level: int
    max_melee: int
    max_bash_kick: int = 0
    max_backstab: int = 0
    lifetap_or_proc: int = 0
    damage_shield: int = 0
    description: str = ""


@dataclass(frozen=True)
class PetSpell:
    """One pet spell and all of its possible ranks (Models/Pets.cs PetSpell)."""

    spell_name: str
    pet_class: str
    caster_level: int
    ranks: tuple[PetRank, ...]


@dataclass(frozen=True)
class Pets:
    """Container of every PetSpell plus the master pet-name set."""

    pet_spells: dict[str, PetSpell]
    pet_names: frozenset[str]

    def spell_for(self, spell_name: str) -> PetSpell | None:
        """Exact spell-name match, falling back to the base name so mage
        elemental variants ("Elementalkin: Air") share one table."""
        found = self.pet_spells.get(spell_name)
        if found is not None:
            return found
        base, _, _ = spell_name.partition(":")
        return self.pet_spells.get(base.strip())

    def is_pet_name(self, name: str) -> bool:
        return name in self.pet_names


_PET_LEVEL_RE = re.compile(
    r"PetLevel\(rank=(?P<rank>\d+), pet_level=(?P<pet_level>\d+), "
    r"max_melee=(?P<max_melee>\d+), max_bashkick=(?P<max_bashkick>\d+), "
    r"max_backstab=(?P<max_backstab>\d+), lt_proc=(?P<lt_proc>\d+)"
    r"(?:, ds=(?P<ds>\d+))?"
    r"(?:, desc='(?P<desc>[^']*)')?\)"
)
_PET_SPELL_RE = re.compile(
    r"PetSpell\('(?P<name>(?:[^'\\]|\\.)+)', '(?P<pet_class>\w+)', "
    r"caster_level=(?P<caster_level>\d+)"
)


def _parse_pet_spells(text: str) -> dict[str, PetSpell]:
    spells: dict[str, PetSpell] = {}
    pending: list[PetRank] = []
    for line in text.splitlines():
        if "pet_level_list.clear()" in line:
            pending = []
            continue
        level = _PET_LEVEL_RE.search(line)
        if level is not None:
            pending.append(
                PetRank(
                    rank=int(level["rank"]),
                    pet_level=int(level["pet_level"]),
                    max_melee=int(level["max_melee"]),
                    max_bash_kick=int(level["max_bashkick"]),
                    max_backstab=int(level["max_backstab"]),
                    lifetap_or_proc=int(level["lt_proc"]),
                    damage_shield=int(level["ds"] or 0),
                    description=level["desc"] or "",
                )
            )
            continue
        spell = _PET_SPELL_RE.search(line)
        if spell is not None:
            name = spell["name"].replace("\\'", "'")
            spells[name] = PetSpell(
                spell_name=name,
                pet_class=spell["pet_class"],
                caster_level=int(spell["caster_level"]),
                ranks=tuple(pending),
            )
    return spells


def _parse_pet_names(text: str) -> frozenset[str]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return frozenset()
    names = ast.literal_eval(text[start : end + 1])
    return frozenset(names)


@lru_cache(maxsize=1)
def load_pets() -> Pets:
    data = resources.files("nparseplus.data") / "pets"
    try:
        spells = _parse_pet_spells((data / "Pet_Spells.txt").read_text(encoding="utf-8"))
    except OSError:
        logger.warning("Pet_Spells.txt not found; pet rank tables disabled")
        spells = {}
    try:
        names = _parse_pet_names((data / "All_Pet_Names.txt").read_text(encoding="utf-8"))
    except OSError:
        logger.warning("All_Pet_Names.txt not found; pet name detection disabled")
        names = frozenset()
    return Pets(pet_spells=spells, pet_names=names)


@dataclass
class PlayerPet:
    """Mutable current-pet state (PetViewModel minus the WPF binding).

    ``rank_index`` is the index into ``pet_spell.ranks`` inferred from the
    highest observed melee hit; -1 means unknown.
    """

    pet_name: str = ""
    pet_spell: PetSpell | None = None
    rank_index: int = -1
    max_observed_melee: int = 0
    on_change: list[Callable[[], None]] = field(default_factory=list)

    @property
    def is_pet_name_known(self) -> bool:
        return self.pet_name != ""

    def _notify(self) -> None:
        for callback in list(self.on_change):
            callback()

    def set_name(self, name: str) -> None:
        self.pet_name = name
        self._notify()

    def set_spell(self, spell: PetSpell | None) -> None:
        self.pet_spell = spell
        self._notify()

    def reset(self) -> None:
        self.pet_name = ""
        self.pet_spell = None
        self.rank_index = -1
        self.max_observed_melee = 0
        self._notify()

    def check_max_melee(self, damage: int) -> None:
        """Update the rank guess from a melee hit (PetViewModel.CheckMaxMelee)."""
        if self.pet_spell is None or not self.is_pet_name_known:
            return
        if damage > self.max_observed_melee or self.rank_index == -1:
            self.max_observed_melee = damage
            # Traverse biggest to smallest so we match the highest rank first.
            for index in range(len(self.pet_spell.ranks) - 1, -1, -1):
                if damage >= self.pet_spell.ranks[index].max_melee:
                    self.rank_index = index
                    self._notify()
                    break
