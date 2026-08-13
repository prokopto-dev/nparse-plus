"""SpellBook.reload — swapping the database under the handlers holding it.

The switch from the bundled spells_us.txt to the one in the user's EQ install
(#70) cannot hand out a new object: ``ParseContext``, ``SpellTimerHandler``,
``AbilityCooldownHandler`` and ``TimerPersistenceHandler`` all captured the
book at construction. So the contract these tests pin is "same object, new
contents".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nparseplus.core.spells.spells_us import SpellBook, load_spell_book

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "spells_us.txt"


@pytest.fixture(scope="module")
def clarity_line() -> str:
    """The fixture's Clarity row, which the variants below are built from."""
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.split("^")[1:2] == ["Clarity"]:
            return line
    raise AssertionError("the pinned fixture no longer has Clarity")


def write_book(tmp_path: Path, clarity_line: str, name: str) -> Path:
    """A one-spell database — a stand-in for "a different spells_us.txt"."""
    fields = clarity_line.split("^")
    fields[1] = name
    path = tmp_path / "spells_us.txt"
    path.write_text("^".join(fields) + "\n", encoding="utf-8")
    return path


def test_load_records_where_it_came_from() -> None:
    book = load_spell_book(FIXTURE, npcs=frozenset())
    assert book.source_path == FIXTURE


def test_reload_swaps_the_data_and_keeps_the_identity(tmp_path, clarity_line) -> None:
    book = load_spell_book(FIXTURE, npcs=frozenset())

    class Handler:
        """Stands in for the four holders: captures the book, not the dicts."""

        def __init__(self, spells: SpellBook) -> None:
            self.spells = spells

    handler = Handler(book)
    assert book.spell_by_name("Clarity") is not None

    book.reload(write_book(tmp_path, clarity_line, "Kaladim Clarity"))

    assert handler.spells is book  # the whole point
    assert book.spell_by_name("Kaladim Clarity") is not None  # ...and it sees the new data
    assert book.spell_by_name("Clarity") is None  # ...and not the old
    assert len(book.spells) == 1
    assert book.source_path == tmp_path / "spells_us.txt"


def test_reload_rebuilds_every_lookup_table(tmp_path, clarity_line) -> None:
    """Not just ``spells``: the message-keyed indexes are what the parsers
    actually read, and a stale one would keep matching a removed spell."""
    book = load_spell_book(FIXTURE, npcs=frozenset())
    breeze = "A cool breeze slips through your mind."
    assert book.cast_on_you(breeze)

    book.reload(write_book(tmp_path, clarity_line, "Kaladim Clarity"))

    assert book.you_cast("Kaladim Clarity")
    assert not book.you_cast("Clarity")
    assert book.cast_on_you(breeze)  # the renamed spell kept the message
    assert [spell.name for spell in book.cast_on_you(breeze)] == ["Kaladim Clarity"]


def test_reload_keeps_the_npc_list(tmp_path, clarity_line) -> None:
    """The master NPC list is bundled data with nothing to do with the EQ
    install, so a reload reuses it rather than paying ~100 ms to re-read it."""
    book = load_spell_book(FIXTURE, npcs=frozenset({"a froglok"}))
    book.reload(write_book(tmp_path, clarity_line, "Kaladim Clarity"))
    assert book.is_npc("A Froglok")


def test_reload_does_not_disturb_an_in_flight_cast(tmp_path, clarity_line) -> None:
    """Casting state is session state, not database content — clearing it
    would read as an interrupt that never happened."""
    book = load_spell_book(FIXTURE, npcs=frozenset())
    spell = book.spell_by_name("Clarity")
    assert spell is not None
    started = datetime(2026, 7, 8, 21, 59, 36)
    book.casting.begin(spell, started)

    book.reload(write_book(tmp_path, clarity_line, "Kaladim Clarity"))

    assert book.casting.spell is spell
    assert book.casting.started_at == started


def test_a_failed_reload_leaves_the_old_database_intact(tmp_path) -> None:
    """Parse first, rebind after: an unreadable path raises before anything
    moves, so the driver keeps reading the book it had."""
    book = load_spell_book(FIXTURE, npcs=frozenset())
    before = len(book.spells)

    with pytest.raises(OSError):
        book.reload(tmp_path / "not-a-file.txt")

    assert len(book.spells) == before
    assert book.spell_by_name("Clarity") is not None
    assert book.source_path == FIXTURE
