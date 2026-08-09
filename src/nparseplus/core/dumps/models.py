"""The shapes a character dump takes on disk (Qt-free).

A *dump* is what the EQ client writes when you type ``/outputfile inventory``
or ``/outputfile spellbook``: a tab-separated snapshot of one character,
dropped into the EQ install directory and overwritten every time you run the
command. The client keeps no history and never names the file by date, so a
second dump silently destroys the first.

This module is the persisted form of one such snapshot. It is deliberately a
*copy* rather than a reference to the game file: the whole point of the
library is to outlive the next ``/outputfile``.

Timestamps are naive local datetimes, like everything else in the pipeline.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class DumpKind(StrEnum):
    """Which ``/outputfile`` command produced a dump."""

    INVENTORY = "inventory"
    SPELLBOOK = "spellbook"

    @property
    def label(self) -> str:
        return "Inventory" if self is DumpKind.INVENTORY else "Spellbook"


class InventoryEntry(BaseModel):
    """One occupied inventory slot.

    Mirrors ``core.inventory.InventoryItem`` (the EQTool port that feeds the
    pigparse uploader) in pydantic form, plus the resolved location name so a
    stored snapshot stays readable without the enum. Empty slots are dropped
    on import — see :func:`nparseplus.core.dumps.parse.inventory_entries`.
    """

    model_config = ConfigDict(extra="ignore")

    location: int = 0  # InventoryLocation wire ordinal
    location_name: str = ""
    name: str
    item_id: int = 0
    count: int = 1
    slots: int = 0


class SpellbookEntry(BaseModel):
    """One spell in a character's book.

    The spellbook dump carries no spell id — just the level the character's
    class learns the spell at, and its name. That is the whole file.
    """

    model_config = ConfigDict(extra="ignore")

    level: int
    name: str


class CharacterDump(BaseModel):
    """One imported snapshot of one character's inventory or spellbook."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    character: str
    #: Server suffix when the filename carried one (``Name_P1999Green``);
    #: P99's own dumps are just ``Name-Inventory.txt``, so usually "".
    server: str = ""
    kind: DumpKind
    #: Mtime of the game file this came from — when the *player* took the
    #: dump, which is the time that means something. ``imported_at`` is when
    #: nParse+ noticed.
    captured_at: datetime
    imported_at: datetime | None = None
    source_file: str = ""
    digest: str = ""
    items: list[InventoryEntry] = Field(default_factory=list)
    spells: list[SpellbookEntry] = Field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return len(self.items) if self.kind is DumpKind.INVENTORY else len(self.spells)

    def names(self) -> list[str]:
        """Every entry's name, in file order (duplicates kept — two Woven
        Grass Bracelets are two rows, and a diff should say so)."""
        if self.kind is DumpKind.INVENTORY:
            return [item.name for item in self.items]
        return [spell.name for spell in self.spells]


class DumpDiff(BaseModel):
    """What changed between two snapshots of the same character and kind.

    Compared by name as a multiset, so losing one of a stacked pair reads as
    one removal. Names only: an item that merely moved between bags has not
    changed in any way worth reporting.
    """

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    count_before: int = 0
    count_after: int = 0

    @property
    def empty(self) -> bool:
        return not self.added and not self.removed


def content_digest(dump: CharacterDump) -> str:
    """Stable digest of a dump's meaningful content.

    Covers what the snapshot *says*, not where it came from or when: two
    dumps of an unchanged character collide, which is exactly how the watcher
    tells "the player re-ran /outputfile" from "something actually changed".
    """
    if dump.kind is DumpKind.INVENTORY:
        payload = [
            [item.location_name, item.name, item.item_id, item.count, item.slots]
            for item in dump.items
        ]
    else:
        payload = [[spell.level, spell.name] for spell in dump.spells]
    encoded = json.dumps([str(dump.kind), payload], sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def diff_dumps(before: CharacterDump | None, after: CharacterDump) -> DumpDiff:
    """Multiset name diff of ``after`` against ``before`` (None = all added)."""
    old = Counter(before.names()) if before is not None else Counter()
    new = Counter(after.names())
    added = sorted((new - old).elements())
    removed = sorted((old - new).elements())
    return DumpDiff(
        added=added,
        removed=removed,
        count_before=sum(old.values()),
        count_after=sum(new.values()),
    )
