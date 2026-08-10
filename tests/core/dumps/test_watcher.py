"""core.dumps.watcher — the auto-import gates and the plugin-facing events."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.dumps import DumpKind, DumpLibrary, DumpWatcher
from nparseplus.core.dumps.watcher import SCAN_INTERVAL_SECONDS
from nparseplus.core.events import (
    CharacterDumpImportedEvent,
    CharacterDumpUpdatedEvent,
)

from .conftest import INVENTORY_TEXT, SPELLBOOK_TEXT, T0, write_dump

CHANGED_SPELLBOOK = SPELLBOOK_TEXT + "1\tMinor Healing\n"


class Recorder:
    """Collects the dump events off the bus."""

    def __init__(self, bus: EventBus) -> None:
        self.imported: list[CharacterDumpImportedEvent] = []
        self.updated: list[CharacterDumpUpdatedEvent] = []
        bus.subscribe(CharacterDumpImportedEvent, self.imported.append)
        bus.subscribe(CharacterDumpUpdatedEvent, self.updated.append)


@pytest.fixture
def setup(eq_dir: Path, library_root: Path):
    """A watcher over a scratch EQ dir, with both toggles on by default."""
    bus = EventBus()
    library = DumpLibrary(library_root)
    state = {"import": True, "update": True, "keep": 10}
    watcher = DumpWatcher(
        library,
        get_eq_dir=lambda: eq_dir,
        is_enabled=lambda: state["import"],
        is_update_enabled=lambda: state["update"],
        get_keep=lambda: state["keep"],
        bus=bus,
    )
    return watcher, library, state, Recorder(bus), eq_dir


def test_first_tick_imports_existing_dumps(setup) -> None:
    """No priming, unlike InventoryWatcher: files already there are the point."""
    watcher, library, _state, events, eq_dir = setup
    write_dump(eq_dir, "Prokopton-Inventory.txt", INVENTORY_TEXT, when=T0)
    write_dump(eq_dir, "Prokopton-Spellbook.txt", SPELLBOOK_TEXT, when=T0)

    watcher.tick(T0)

    assert library.characters() == ["Prokopton"]
    assert library.kinds("Prokopton") == [DumpKind.INVENTORY, DumpKind.SPELLBOOK]
    assert len(events.imported) == 2
    assert not events.updated
    kinds = sorted(event.kind for event in events.imported)
    assert kinds == ["inventory", "spellbook"]


def test_unrelated_txt_files_are_never_read(setup) -> None:
    watcher, library, _state, events, eq_dir = setup
    # Right content, wrong name — the EQ directory is full of these.
    write_dump(eq_dir, "SomeTool.txt", INVENTORY_TEXT, when=T0)
    watcher.tick(T0)
    assert library.characters() == []
    assert not events.imported


def test_unchanged_dump_does_not_accumulate(setup) -> None:
    watcher, library, _state, events, eq_dir = setup
    path = write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    # The player re-runs /outputfile without having learned anything.
    write_dump(eq_dir, path.name, SPELLBOOK_TEXT, when=T0 + timedelta(hours=1))
    watcher.tick(T0 + timedelta(seconds=SCAN_INTERVAL_SECONDS + 1))

    assert len(library.snapshots("A", DumpKind.SPELLBOOK)) == 1
    assert len(events.imported) == 1
    assert not events.updated


def test_changed_dump_stores_a_new_snapshot_and_publishes_the_diff(setup) -> None:
    watcher, library, _state, events, eq_dir = setup
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    write_dump(eq_dir, "A-Spellbook.txt", CHANGED_SPELLBOOK, when=T0 + timedelta(hours=1))
    watcher.tick(T0 + timedelta(seconds=SCAN_INTERVAL_SECONDS + 1))

    assert len(library.snapshots("A", DumpKind.SPELLBOOK)) == 2
    assert len(events.updated) == 1
    event = events.updated[0]
    assert event.character == "A"
    assert event.kind == "spellbook"
    assert event.added == ("Minor Healing",)
    assert event.removed == ()
    assert event.entry_count == 6
    assert Path(event.path).exists()


def test_auto_import_off_does_nothing_at_all(setup) -> None:
    watcher, library, state, events, eq_dir = setup
    state["import"] = False
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    assert library.characters() == []
    assert not events.imported

    # Turning it on picks up promptly, without waiting out a scan interval.
    state["import"] = True
    watcher.tick(T0 + timedelta(seconds=1))
    assert len(events.imported) == 1


def test_auto_update_off_keeps_the_first_snapshot(setup) -> None:
    watcher, library, state, events, eq_dir = setup
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    state["update"] = False
    write_dump(eq_dir, "A-Spellbook.txt", CHANGED_SPELLBOOK, when=T0 + timedelta(hours=1))
    watcher.tick(T0 + timedelta(seconds=SCAN_INTERVAL_SECONDS + 1))

    assert len(library.snapshots("A", DumpKind.SPELLBOOK)) == 1
    assert not events.updated
    assert watcher.last_result is not None
    assert watcher.last_result.skipped == 1


def test_a_change_held_back_is_taken_once_auto_update_returns(setup) -> None:
    """The mtime cache must not swallow the change it was told to ignore."""
    watcher, library, state, events, eq_dir = setup
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    state["update"] = False
    write_dump(eq_dir, "A-Spellbook.txt", CHANGED_SPELLBOOK, when=T0 + timedelta(hours=1))
    watcher.tick(T0 + timedelta(seconds=SCAN_INTERVAL_SECONDS + 1))
    assert not events.updated

    state["update"] = True
    watcher.tick(T0 + timedelta(seconds=2 * SCAN_INTERVAL_SECONDS + 2))
    assert len(events.updated) == 1
    assert len(library.snapshots("A", DumpKind.SPELLBOOK)) == 2


def test_auto_import_off_still_imports_a_new_character_when_back_on(setup) -> None:
    watcher, library, state, events, eq_dir = setup
    write_dump(eq_dir, "A-Inventory.txt", INVENTORY_TEXT, when=T0)
    watcher.tick(T0)
    write_dump(eq_dir, "B-Inventory.txt", INVENTORY_TEXT, when=T0)
    state["import"] = False
    watcher.tick(T0 + timedelta(seconds=SCAN_INTERVAL_SECONDS + 1))
    assert library.characters() == ["A"]
    state["import"] = True
    watcher.tick(T0 + timedelta(seconds=2 * SCAN_INTERVAL_SECONDS + 2))
    assert library.characters() == ["A", "B"]
    assert len(events.imported) == 2


def test_scanning_respects_the_interval(setup) -> None:
    watcher, library, _state, _events, eq_dir = setup
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    write_dump(eq_dir, "B-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0 + timedelta(seconds=1))  # too soon
    assert library.characters() == ["A"]
    watcher.tick(T0 + timedelta(seconds=SCAN_INTERVAL_SECONDS + 1))
    assert library.characters() == ["A", "B"]


def test_request_scan_ignores_the_toggles(setup) -> None:
    """A button the user pressed always works."""
    watcher, _library, state, events, eq_dir = setup
    state["import"] = False
    state["update"] = False
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)

    watcher.request_scan()
    watcher.tick(T0)
    assert len(events.imported) == 1

    write_dump(eq_dir, "A-Spellbook.txt", CHANGED_SPELLBOOK, when=T0 + timedelta(hours=1))
    watcher.request_scan()
    watcher.tick(T0 + timedelta(seconds=1))
    assert len(events.updated) == 1


def test_request_import_takes_a_hand_picked_file(setup, tmp_path: Path) -> None:
    watcher, library, state, events, _eq_dir = setup
    state["import"] = False
    path = write_dump(tmp_path, "Elsewhere-Spellbook.txt", SPELLBOOK_TEXT, when=T0)

    watcher.request_import(path)
    watcher.tick(T0)

    assert library.characters() == ["Elsewhere"]
    assert len(events.imported) == 1
    assert events.imported[0].source_file == str(path)


def test_retention_is_honoured_by_the_watcher(setup) -> None:
    watcher, library, state, _events, eq_dir = setup
    state["keep"] = 2
    for day in range(4):
        text = SPELLBOOK_TEXT + f"{day + 1}\tSpell {day}\n"
        write_dump(eq_dir, "A-Spellbook.txt", text, when=T0 + timedelta(days=day))
        watcher.tick(T0 + timedelta(days=day, seconds=day * (SCAN_INTERVAL_SECONDS + 1)))
    assert len(library.snapshots("A", DumpKind.SPELLBOOK)) == 2


def test_no_eq_dir_is_a_no_op(library_root: Path) -> None:
    library = DumpLibrary(library_root)
    watcher = DumpWatcher(
        library,
        get_eq_dir=lambda: None,
        is_enabled=lambda: True,
        is_update_enabled=lambda: True,
    )
    watcher.tick(T0)
    assert library.characters() == []


def test_tick_never_raises(library_root: Path) -> None:
    def boom() -> Path:
        raise RuntimeError("no")

    watcher = DumpWatcher(
        DumpLibrary(library_root),
        get_eq_dir=boom,
        is_enabled=lambda: True,
        is_update_enabled=lambda: True,
    )
    watcher.tick(T0)  # swallowed, like every other driver-tick service


def test_status_text_reports_the_last_scan(setup) -> None:
    watcher, _library, _state, _events, eq_dir = setup
    assert "Not scanned yet" in watcher.status_text()
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    text = watcher.status_text()
    assert "1 snapshot stored" in text
    assert "1 imported" in text


def test_events_are_optional(eq_dir: Path, library_root: Path) -> None:
    """A watcher with no bus still imports (the window's fallback path)."""
    library = DumpLibrary(library_root)
    watcher = DumpWatcher(
        library,
        get_eq_dir=lambda: eq_dir,
        is_enabled=lambda: True,
        is_update_enabled=lambda: True,
    )
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    assert library.characters() == ["A"]


def test_events_carry_naive_local_timestamps(setup) -> None:
    """The whole pipeline compares naive datetimes — never introduce tz-aware."""
    watcher, _library, _state, events, eq_dir = setup
    write_dump(eq_dir, "A-Spellbook.txt", SPELLBOOK_TEXT, when=T0)
    watcher.tick(T0)
    captured = events.imported[0].captured_at
    assert isinstance(captured, datetime)
    assert captured.tzinfo is None
