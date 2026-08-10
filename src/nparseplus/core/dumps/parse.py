"""Recognizing and reading P99 ``/outputfile`` dumps (Qt-free).

Two file shapes, both tab-separated, both written into the EQ install
directory next to ``eqclient.ini``:

``<Character>-Inventory.txt``
    A ``Location  Name  ID  Count  Slots`` header followed by one row per
    slot, including empty ones. Already parsed by
    :mod:`nparseplus.core.inventory` (the EQTool port that feeds the pigparse
    uploader); this module reuses that parser rather than growing a second
    one, and adds the persistence-facing shaping on top.

``<Character>-Spellbook.txt``
    **No header at all** — every line is ``<level>\\t<Spell Name>``, in book
    page order. There is nothing else in the file, so recognizing one is
    purely structural: it is a spellbook if every non-empty line looks like
    that. Hence :func:`parse_spellbook_text` refuses the whole file on a
    single bad line, where the inventory parser (which has a header to key
    off) can afford to skip junk rows.

Recognition normally starts from the filename, because the EQ directory holds
hundreds of unrelated ``.txt`` files and reading them all every poll would be
silly. :func:`sniff_kind` exists for the one case where the name cannot be
trusted: a file the user picked by hand in the import dialog.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nparseplus.core.dumps.models import (
    CharacterDump,
    DumpKind,
    InventoryEntry,
    SpellbookEntry,
    content_digest,
)
from nparseplus.core.inventory import InventoryLocation, parse_inventory_text

#: Filename suffixes the client uses, lower-cased.
KIND_SUFFIXES: dict[str, DumpKind] = {
    "-inventory": DumpKind.INVENTORY,
    "-spellbook": DumpKind.SPELLBOOK,
}

#: Spell levels a Titanium-era character can have. Headroom over the level 60
#: cap (P99 spells go to 60; 65 exists in the file format) so a server that
#: raises it does not make every spellbook unrecognizable.
MIN_SPELL_LEVEL = 1
MAX_SPELL_LEVEL = 70

#: The client writes a row for every slot, occupied or not. They carry no
#: information a library should keep, and they would swamp every diff.
EMPTY_ITEM_NAME = "Empty"


def parse_spellbook_text(text: str) -> list[SpellbookEntry] | None:
    """Parse an ``/outputfile spellbook`` dump; None if it isn't one.

    Strict by necessity: the file has no header, so "every line parses" is
    the only thing separating it from any other two-column TSV.
    """
    entries: list[SpellbookEntry] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            return None
        level_text, name = parts[0].strip(), parts[1].strip()
        if not name:
            return None
        try:
            level = int(level_text)
        except ValueError:
            return None
        if not MIN_SPELL_LEVEL <= level <= MAX_SPELL_LEVEL:
            return None
        entries.append(SpellbookEntry(level=level, name=name))
    return entries or None


def inventory_entries(text: str) -> list[InventoryEntry] | None:
    """Parse an inventory dump into storable entries; None if it isn't one.

    Empty slots are dropped — see :data:`EMPTY_ITEM_NAME`.
    """
    items = parse_inventory_text(text)
    if items is None:
        return None
    entries = [
        InventoryEntry(
            location=item.location,
            location_name=InventoryLocation(item.location).name,
            name=item.name,
            item_id=item.item_id,
            count=item.count,
            slots=item.slots,
        )
        for item in items
        if item.name != EMPTY_ITEM_NAME and item.item_id
    ]
    return entries or None


def dump_target(path: Path) -> tuple[str, str, DumpKind] | None:
    """``(character, server, kind)`` implied by a dump's filename, or None.

    ``Prokopton-Inventory.txt`` -> ``("Prokopton", "", INVENTORY)``. A
    ``Name_Server-Kind.txt`` spelling is accepted too, since other client
    outputs use it and a user may well rename a file that way.
    """
    stem = Path(path).stem
    lowered = stem.lower()
    for suffix, kind in KIND_SUFFIXES.items():
        if not lowered.endswith(suffix):
            continue
        head = stem[: -len(suffix)]
        character, _, server = head.partition("_")
        if not character.isalnum():
            return None
        return character, server, kind
    return None


def sniff_kind(text: str) -> DumpKind | None:
    """Which kind of dump ``text`` is, judged on content alone.

    For hand-picked files whose name says nothing. Inventory is checked
    first: it has a real header, so it can never be mistaken for a
    spellbook.
    """
    if parse_inventory_text(text) is not None:
        return DumpKind.INVENTORY
    if parse_spellbook_text(text) is not None:
        return DumpKind.SPELLBOOK
    return None


def build_dump(
    text: str,
    *,
    character: str,
    kind: DumpKind,
    captured_at: datetime,
    server: str = "",
    source_file: str = "",
) -> CharacterDump | None:
    """Turn dump text into a :class:`CharacterDump`; None if it doesn't parse."""
    dump = CharacterDump(
        character=character,
        server=server,
        kind=kind,
        captured_at=captured_at,
        source_file=source_file,
    )
    if kind is DumpKind.INVENTORY:
        items = inventory_entries(text)
        if items is None:
            return None
        dump.items = items
    else:
        spells = parse_spellbook_text(text)
        if spells is None:
            return None
        dump.spells = spells
    dump.digest = content_digest(dump)
    return dump


def read_dump_file(
    path: Path,
    *,
    character: str = "",
    kind: DumpKind | None = None,
    sniff: bool = False,
) -> CharacterDump | None:
    """Read one file into a :class:`CharacterDump`; None if it isn't a dump.

    With no arguments this is the watcher's path: the filename decides who
    and what, and a name that says nothing costs one ``stat`` and no read —
    the EQ directory holds hundreds of unrelated ``.txt`` files and reading
    them all every poll would be silly.

    ``sniff=True`` is the import-a-file-by-hand path: the user has already
    said this particular file is a dump, so look inside it even when the name
    gives nothing away (a backup saved as ``bankmule-backup.txt``, an export
    off another machine). ``character``/``kind`` override whatever is found.
    """
    path = Path(path)
    target = dump_target(path)
    if target is None and kind is None and not character and not sniff:
        return None
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    resolved_kind = kind
    resolved_character = character
    server = ""
    if target is not None:
        target_character, server, target_kind = target
        resolved_kind = resolved_kind or target_kind
        resolved_character = resolved_character or target_character
    if resolved_kind is None:
        resolved_kind = sniff_kind(text)
    if resolved_kind is None:
        return None
    if not resolved_character:
        resolved_character = _character_from_stem(path.stem)
    if not resolved_character:
        return None

    return build_dump(
        text,
        character=resolved_character,
        kind=resolved_kind,
        captured_at=datetime.fromtimestamp(stat.st_mtime),
        server=server,
        source_file=str(path),
    )


def render_dump_text(dump: CharacterDump) -> str:
    """Render a stored snapshot back into the client's own file format.

    The inverse of the parsers above, so an exported snapshot is a file the
    game could have written — that is what makes the library's copies useful
    to every other P99 tool, rather than only to nParse+.

    Not byte-identical to the original: the empty slots the client emits were
    dropped on import and are not invented back.
    """
    if dump.kind is DumpKind.INVENTORY:
        lines = ["\t".join(("Location", "Name", "ID", "Count", "Slots"))]
        lines += [
            "\t".join(
                (
                    item.location_name,
                    item.name,
                    str(item.item_id),
                    str(item.count),
                    str(item.slots),
                )
            )
            for item in dump.items
        ]
    else:
        lines = [f"{spell.level}\t{spell.name}" for spell in dump.spells]
    return "\n".join(lines) + "\n"


def _character_from_stem(stem: str) -> str:
    """Best-effort character name for a file that carries no dump suffix."""
    head = stem.split("-")[0].split("_")[0]
    cleaned = "".join(ch for ch in head if ch.isalnum())
    return cleaned[:32]
