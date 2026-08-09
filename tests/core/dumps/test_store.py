"""core.dumps.store — the per-character, per-kind snapshot library."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.core.dumps import (
    DumpKind,
    DumpLibrary,
    build_dump,
    read_dump_file,
)
from nparseplus.core.dumps.store import parse_snapshot_filename, snapshot_filename

from .conftest import INVENTORY_TEXT, SPELLBOOK_TEXT, T0, write_dump


def _dump(text: str, kind: DumpKind, character: str, when: datetime):
    dump = build_dump(text, character=character, kind=kind, captured_at=when)
    assert dump is not None
    return dump


def test_snapshot_filename_round_trip() -> None:
    name = snapshot_filename(T0, "abcdef0123456789")
    assert parse_snapshot_filename(name) == (T0, "abcdef0123456789")
    assert parse_snapshot_filename("not-a-snapshot.json") is None
    assert parse_snapshot_filename("20260809.json") is None


def test_each_character_keeps_its_own_inventory_and_spellbook(library_root: Path) -> None:
    """The point of the library: one current version per character AND kind."""
    library = DumpLibrary(library_root)
    library.store(_dump(INVENTORY_TEXT, DumpKind.INVENTORY, "Prokopton", T0))
    library.store(_dump(SPELLBOOK_TEXT, DumpKind.SPELLBOOK, "Prokopton", T0))
    library.store(_dump(INVENTORY_TEXT, DumpKind.INVENTORY, "Untune", T0))

    assert library.characters() == ["Prokopton", "Untune"]
    assert library.kinds("Prokopton") == [DumpKind.INVENTORY, DumpKind.SPELLBOOK]
    assert library.kinds("Untune") == [DumpKind.INVENTORY]

    book = library.load_latest("Prokopton", DumpKind.SPELLBOOK)
    bags = library.load_latest("Prokopton", DumpKind.INVENTORY)
    assert book is not None and bags is not None
    assert book.kind is DumpKind.SPELLBOOK
    assert bags.kind is DumpKind.INVENTORY
    # One character's spellbook must never be another's, or the latter's.
    assert library.load_latest("Untune", DumpKind.SPELLBOOK) is None
    assert library.total_snapshots() == 3


def test_snapshots_are_newest_first_and_load_round_trips(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    old = _dump(SPELLBOOK_TEXT, DumpKind.SPELLBOOK, "A", T0)
    new = _dump(SPELLBOOK_TEXT + "1\tMinor Healing\n", DumpKind.SPELLBOOK, "A", T0 + timedelta(1))
    library.store(old)
    library.store(new)

    refs = library.snapshots("A", DumpKind.SPELLBOOK)
    assert [ref.captured_at for ref in refs] == [T0 + timedelta(1), T0]
    latest = library.load(refs[0])
    assert latest is not None
    assert latest.entry_count == 6
    assert latest.imported_at is not None  # stamped on the way in
    assert library.load_latest("A", DumpKind.SPELLBOOK).digest == new.digest


def test_storing_the_same_dump_twice_is_one_snapshot(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    dump = _dump(INVENTORY_TEXT, DumpKind.INVENTORY, "A", T0)
    first = library.store(dump)
    second = library.store(dump)
    assert first is not None and second is not None
    assert first.path == second.path
    assert len(library.snapshots("A", DumpKind.INVENTORY)) == 1
    assert library.is_duplicate(dump)


def test_is_duplicate_only_looks_at_the_newest(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    original = _dump(SPELLBOOK_TEXT, DumpKind.SPELLBOOK, "A", T0)
    changed = _dump(SPELLBOOK_TEXT + "1\tMinor Healing\n", DumpKind.SPELLBOOK, "A", T0)
    library.store(original)
    assert library.is_duplicate(original)
    assert not library.is_duplicate(changed)
    library.store(changed.model_copy(update={"captured_at": T0 + timedelta(1)}))
    # Reverting to an older state is a change, not a duplicate.
    assert not library.is_duplicate(original)


def test_prune_keeps_the_newest(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    for day in range(5):
        text = SPELLBOOK_TEXT + f"{day + 1}\tSpell {day}\n"
        library.store(_dump(text, DumpKind.SPELLBOOK, "A", T0 + timedelta(days=day)), keep=3)
    refs = library.snapshots("A", DumpKind.SPELLBOOK)
    assert len(refs) == 3
    assert [ref.captured_at for ref in refs] == [
        T0 + timedelta(days=4),
        T0 + timedelta(days=3),
        T0 + timedelta(days=2),
    ]


def test_prune_is_per_kind(library_root: Path) -> None:
    """Keeping 1 inventory snapshot must not evict the spellbook."""
    library = DumpLibrary(library_root)
    library.store(_dump(SPELLBOOK_TEXT, DumpKind.SPELLBOOK, "A", T0), keep=1)
    library.store(_dump(INVENTORY_TEXT, DumpKind.INVENTORY, "A", T0), keep=1)
    library.store(
        _dump(INVENTORY_TEXT.replace("Rusty Sword", "Shiny Sword"), DumpKind.INVENTORY, "A", T0),
        keep=1,
    )
    assert len(library.snapshots("A", DumpKind.INVENTORY)) == 1
    assert len(library.snapshots("A", DumpKind.SPELLBOOK)) == 1


def test_delete_snapshot_and_character(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    library.store(_dump(SPELLBOOK_TEXT, DumpKind.SPELLBOOK, "A", T0))
    library.store(_dump(INVENTORY_TEXT, DumpKind.INVENTORY, "A", T0))
    ref = library.latest("A", DumpKind.SPELLBOOK)
    assert ref is not None and library.delete(ref)
    assert library.snapshots("A", DumpKind.SPELLBOOK) == []
    assert library.snapshots("A", DumpKind.INVENTORY)

    assert library.delete_character("A")
    assert library.characters() == []


def test_missing_and_corrupt_reads_are_empty_not_exceptions(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    assert library.characters() == []
    assert library.snapshots("Nobody", DumpKind.INVENTORY) == []
    assert library.load_latest("Nobody", DumpKind.INVENTORY) is None

    library.store(_dump(SPELLBOOK_TEXT, DumpKind.SPELLBOOK, "A", T0))
    ref = library.latest("A", DumpKind.SPELLBOOK)
    assert ref is not None
    Path(ref.path).write_text("{not json", encoding="utf-8")
    assert library.load(ref) is None


def test_a_snapshot_from_a_newer_nparseplus_is_not_guessed_at(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    dump = _dump(SPELLBOOK_TEXT, DumpKind.SPELLBOOK, "A", T0)
    library.store(dump)
    ref = library.latest("A", DumpKind.SPELLBOOK)
    assert ref is not None
    payload = Path(ref.path).read_text(encoding="utf-8")
    Path(ref.path).write_text(
        payload.replace('"schema_version": 1', '"schema_version": 99'), "utf-8"
    )
    assert library.load(ref) is None


def test_store_a_real_file_end_to_end(eq_dir: Path, library_root: Path) -> None:
    write_dump(eq_dir, "Prokopton-Inventory.txt", INVENTORY_TEXT, when=T0)
    dump = read_dump_file(eq_dir / "Prokopton-Inventory.txt")
    assert dump is not None
    library = DumpLibrary(library_root)
    ref = library.store(dump)
    assert ref is not None
    assert ref.path.exists()
    assert ref.label == "2026-08-09 12:00"
    stored = library.load(ref)
    assert stored is not None
    assert stored.source_file.endswith("Prokopton-Inventory.txt")
