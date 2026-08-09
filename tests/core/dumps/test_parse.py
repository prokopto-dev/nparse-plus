"""core.dumps.parse — recognizing and reading the two P99 dump shapes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nparseplus.core.dumps import (
    DumpKind,
    build_dump,
    content_digest,
    diff_dumps,
    dump_target,
    inventory_entries,
    parse_spellbook_text,
    read_dump_file,
    render_dump_text,
    sniff_kind,
)

from .conftest import INVENTORY_TEXT, SPELLBOOK_TEXT, T0, write_dump


def test_parse_spellbook_text() -> None:
    entries = parse_spellbook_text(SPELLBOOK_TEXT)
    assert entries is not None
    assert [entry.name for entry in entries] == [
        "Superior Healing",
        "Shield of Thorns",
        "Spirit of Wolf",
        "Ensnare",
        "Chloroplast",
    ]
    assert entries[0].level == 51
    assert entries[2].level == 14


def test_parse_spellbook_keeps_book_order() -> None:
    """Book page order is information — do not sort it away."""
    entries = parse_spellbook_text("49\tZeb\n14\tAlpha\n")
    assert entries is not None
    assert [entry.name for entry in entries] == ["Zeb", "Alpha"]


def test_parse_spellbook_refuses_anything_else() -> None:
    # No header means "every line parses" is the only discriminator, so one
    # bad line has to reject the whole file.
    assert parse_spellbook_text("") is None
    assert parse_spellbook_text("just a log line\n") is None
    assert parse_spellbook_text("51\tSuperior Healing\nnot a row\n") is None
    assert parse_spellbook_text("51\tSuperior Healing\t1234\n") is None
    assert parse_spellbook_text("51\t\n") is None
    assert parse_spellbook_text("0\tToo Low\n") is None
    assert parse_spellbook_text("999\tToo High\n") is None
    assert parse_spellbook_text(INVENTORY_TEXT) is None


def test_inventory_entries_drop_empty_slots() -> None:
    entries = inventory_entries(INVENTORY_TEXT)
    assert entries is not None
    assert "Empty" not in [entry.name for entry in entries]
    assert entries[0].name == "Treant Tear"
    assert entries[0].location_name == "Ear"
    # The dash form the client writes resolves to the enum name.
    assert entries[-1].location_name == "General1Slot1"


def test_dump_target_from_filename() -> None:
    assert dump_target(Path("Prokopton-Inventory.txt")) == ("Prokopton", "", DumpKind.INVENTORY)
    assert dump_target(Path("Prokopton-Spellbook.txt")) == ("Prokopton", "", DumpKind.SPELLBOOK)
    # Case-insensitive, and the Name_Server spelling keeps the server.
    assert dump_target(Path("Untune_P1999Green-inventory.txt")) == (
        "Untune",
        "P1999Green",
        DumpKind.INVENTORY,
    )
    assert dump_target(Path("AutoChannels.txt")) is None
    assert dump_target(Path("eqlog_Prokopton_P1999Green.txt")) is None


def test_sniff_kind_reads_content_only() -> None:
    assert sniff_kind(INVENTORY_TEXT) is DumpKind.INVENTORY
    assert sniff_kind(SPELLBOOK_TEXT) is DumpKind.SPELLBOOK
    assert sniff_kind("nothing to see\n") is None


def test_read_dump_file_uses_the_filename(tmp_path: Path) -> None:
    path = write_dump(tmp_path, "Prokopton-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    dump = read_dump_file(path)
    assert dump is not None
    assert dump.character == "Prokopton"
    assert dump.kind is DumpKind.SPELLBOOK
    assert dump.entry_count == 5
    assert dump.captured_at == T0  # the file's mtime, i.e. when it was taken
    assert dump.digest


def test_read_dump_file_ignores_files_that_are_not_dumps(tmp_path: Path) -> None:
    noise = write_dump(tmp_path, "AutoChannels.txt", INVENTORY_TEXT)
    # Right content, wrong name: the watcher must not read the whole EQ dir.
    assert read_dump_file(noise) is None
    # ...but a hand-picked file gets sniffed.
    named = read_dump_file(noise, character="Someone")
    assert named is not None
    assert named.kind is DumpKind.INVENTORY

    wrong = write_dump(tmp_path, "Prokopton-Spellbook.txt", "not a dump at all\n")
    assert read_dump_file(wrong) is None


def test_digest_ignores_provenance_but_not_content() -> None:
    first = build_dump(
        SPELLBOOK_TEXT, character="A", kind=DumpKind.SPELLBOOK, captured_at=T0, source_file="a.txt"
    )
    later = build_dump(
        SPELLBOOK_TEXT,
        character="B",
        kind=DumpKind.SPELLBOOK,
        captured_at=datetime(2027, 1, 1),
        source_file="b.txt",
    )
    changed = build_dump(
        SPELLBOOK_TEXT + "1\tMinor Healing\n",
        character="A",
        kind=DumpKind.SPELLBOOK,
        captured_at=T0,
    )
    assert first is not None and later is not None and changed is not None
    assert content_digest(first) == content_digest(later)
    assert content_digest(first) != content_digest(changed)


def test_diff_is_a_multiset_of_names() -> None:
    before = build_dump(INVENTORY_TEXT, character="A", kind=DumpKind.INVENTORY, captured_at=T0)
    after_text = INVENTORY_TEXT.replace(
        "Wrist\tWoven Grass Bracelet\t31150\t1\t5\nWrist\tWoven Grass Bracelet\t31150\t1\t5\n",
        "Wrist\tWoven Grass Bracelet\t31150\t1\t5\nNeck\tQeynos Badge of Honor\t2388\t1\t5\n",
    )
    after = build_dump(after_text, character="A", kind=DumpKind.INVENTORY, captured_at=T0)
    assert before is not None and after is not None
    change = diff_dumps(before, after)
    # One of the pair went, one new item arrived.
    assert change.added == ["Qeynos Badge of Honor"]
    assert change.removed == ["Woven Grass Bracelet"]
    assert change.count_before == change.count_after == 6
    assert not change.empty
    assert diff_dumps(before, before).empty


def test_diff_against_nothing_is_all_added() -> None:
    dump = build_dump(SPELLBOOK_TEXT, character="A", kind=DumpKind.SPELLBOOK, captured_at=T0)
    assert dump is not None
    change = diff_dumps(None, dump)
    assert len(change.added) == 5
    assert change.count_before == 0


def test_render_round_trips_through_the_parsers() -> None:
    for text, kind in ((INVENTORY_TEXT, DumpKind.INVENTORY), (SPELLBOOK_TEXT, DumpKind.SPELLBOOK)):
        dump = build_dump(text, character="A", kind=kind, captured_at=T0)
        assert dump is not None
        rendered = render_dump_text(dump)
        again = build_dump(rendered, character="A", kind=kind, captured_at=T0)
        assert again is not None
        assert again.names() == dump.names()
        assert content_digest(again) == content_digest(dump)
