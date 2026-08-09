"""pytest-qt tests for the Character Dumps window."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.dumps import DumpKind, DumpLibrary, build_dump
from nparseplus.ui.dumpswindow import CharacterDumpsWindow

pytestmark = pytest.mark.qt

T0 = datetime(2026, 8, 9, 12, 0, 0)

INVENTORY = (
    "Location\tName\tID\tCount\tSlots\n"
    "Charm\tEmpty\t0\t0\t0\n"
    "Ear\tTreant Tear\t12801\t1\t5\n"
    "Head\tIksar Hide Cap\t5799\t1\t5\n"
)
SPELLBOOK = "51\tSuperior Healing\n14\tSpirit of Wolf\n"
SPELLBOOK_PLUS = SPELLBOOK + "29\tEnsnare\n"


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.settings = Settings()
        self.library = DumpLibrary(tmp_path / "library")
        self.saves = 0
        self.window = CharacterDumpsWindow(
            self.settings, self.library, on_save=self._save, watcher=None
        )
        self.window.confirm_destructive = False

    def _save(self) -> None:
        self.saves += 1

    def store(self, character: str, kind: DumpKind, text: str, when: datetime) -> None:
        dump = build_dump(text, character=character, kind=kind, captured_at=when)
        assert dump is not None
        self.library.store(dump)


@pytest.fixture
def env(qtbot, tmp_path: Path) -> Env:
    environment = Env(tmp_path)
    qtbot.addWidget(environment.window)
    return environment


def _top_level(window) -> list[str]:
    tree = window._tree
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def _children(item) -> list[str]:
    return [item.child(i).text(0) for i in range(item.childCount())]


def test_tree_groups_by_character_then_kind(env: Env) -> None:
    env.store("Prokopton", DumpKind.INVENTORY, INVENTORY, T0)
    env.store("Prokopton", DumpKind.SPELLBOOK, SPELLBOOK, T0)
    env.store("Untune", DumpKind.INVENTORY, INVENTORY, T0)
    env.window.refresh()

    assert _top_level(env.window) == ["Prokopton", "Untune"]
    prokopton = env.window._tree.topLevelItem(0)
    assert _children(prokopton) == ["Inventory", "Spellbook"]
    # Untune has no spellbook — one character's book is never another's.
    assert _children(env.window._tree.topLevelItem(1)) == ["Inventory"]


def test_history_hangs_under_the_current_snapshot(env: Env) -> None:
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK, T0)
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK_PLUS, T0 + timedelta(days=1))
    env.window.refresh()

    kind_item = env.window._tree.topLevelItem(0).child(0)
    # The kind row IS the newest snapshot; older ones are its children.
    assert kind_item.text(1) == "2026-08-10 12:00"
    assert kind_item.childCount() == 1
    assert kind_item.child(0).text(1) == "2026-08-09 12:00"


def test_selecting_a_snapshot_renders_its_entries(env: Env) -> None:
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK_PLUS, T0)
    env.window.refresh()
    ref = env.library.latest("A", DumpKind.SPELLBOOK)
    assert ref is not None
    assert env.window.select_snapshot(ref)

    dump = env.window.current_dump()
    assert dump is not None and dump.entry_count == 3
    entries = env.window._entries
    assert entries.topLevelItemCount() == 3
    assert entries.topLevelItem(0).text(1) == "Superior Healing"
    assert "Spellbook" in env.window._detail_title.text()
    assert "3 entries" in env.window._detail_title.text()


def test_inventory_columns_differ_from_spellbook_columns(env: Env) -> None:
    env.store("A", DumpKind.INVENTORY, INVENTORY, T0)
    env.window.refresh()
    header = env.window._entries.headerItem()
    assert [header.text(i) for i in range(header.columnCount())] == [
        "Location",
        "Item",
        "Count",
        "ID",
    ]
    # The client's empty slots never made it into the library.
    names = [
        env.window._entries.topLevelItem(i).text(1)
        for i in range(env.window._entries.topLevelItemCount())
    ]
    assert names == ["Treant Tear", "Iksar Hide Cap"]


def test_switching_kinds_drops_the_other_kinds_columns(env: Env) -> None:
    """setHeaderLabels only grows the column count — Count/ID used to linger."""
    env.store("A", DumpKind.INVENTORY, INVENTORY, T0)
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK, T0)
    env.window.refresh()

    inventory_ref = env.library.latest("A", DumpKind.INVENTORY)
    spellbook_ref = env.library.latest("A", DumpKind.SPELLBOOK)
    assert inventory_ref is not None and spellbook_ref is not None

    env.window.select_snapshot(inventory_ref)
    assert env.window._entries.columnCount() == 4
    env.window.select_snapshot(spellbook_ref)
    assert env.window._entries.columnCount() == 2
    header = env.window._entries.headerItem()
    assert [header.text(i) for i in range(header.columnCount())] == ["Level", "Spell"]
    # ...and back again.
    env.window.select_snapshot(inventory_ref)
    assert env.window._entries.columnCount() == 4


def test_filter_narrows_the_entries(env: Env) -> None:
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK_PLUS, T0)
    env.window.refresh()
    env.window._filter.setText("wolf")
    assert env.window._entries.topLevelItemCount() == 1
    assert env.window._entries.topLevelItem(0).text(1) == "Spirit of Wolf"
    env.window._filter.setText("")
    assert env.window._entries.topLevelItemCount() == 3


def test_change_line_reports_the_diff_against_the_previous_snapshot(env: Env) -> None:
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK, T0)
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK_PLUS, T0 + timedelta(days=1))
    env.window.refresh()
    ref = env.library.latest("A", DumpKind.SPELLBOOK)
    assert ref is not None
    env.window.select_snapshot(ref)
    assert "+1: Ensnare" in env.window._change_label.text()

    # The oldest one has nothing behind it to compare with.
    older = env.library.snapshots("A", DumpKind.SPELLBOOK)[1]
    env.window.select_snapshot(older)
    assert "Oldest snapshot" in env.window._change_label.text()


def test_toggles_write_through_to_settings_and_save(env: Env) -> None:
    assert env.settings.dumps.auto_import is True
    env.window.auto_import_box.setChecked(False)
    assert env.settings.dumps.auto_import is False

    env.window.auto_update_box.setChecked(False)
    assert env.settings.dumps.auto_update is False

    env.window.keep_spin.setValue(3)
    assert env.settings.dumps.keep_per_character == 3
    assert env.saves == 3


def test_import_file_accepts_a_dump_and_refuses_anything_else(env: Env, tmp_path: Path) -> None:
    good = tmp_path / "Beeta-Spellbook.txt"
    good.write_text(SPELLBOOK, encoding="utf-8")
    assert env.window.import_file(good)
    assert env.library.characters() == ["Beeta"]

    bad = tmp_path / "Beeta-Spellbook.txt.bak"
    bad.write_text("this is not a dump\n", encoding="utf-8")
    assert not env.window.import_file(bad)


def test_import_file_hands_off_to_the_watcher_when_there_is_one(qtbot, tmp_path: Path) -> None:
    """The window must not import on the GUI thread — the watcher publishes."""

    class FakeWatcher:
        def __init__(self) -> None:
            self.scans = 0
            self.files: list[Path] = []

        def request_scan(self) -> None:
            self.scans += 1

        def request_import(self, path: Path) -> None:
            self.files.append(Path(path))

        def status_text(self) -> str:
            return "fake status"

    settings = Settings()
    library = DumpLibrary(tmp_path / "library")
    watcher = FakeWatcher()
    window = CharacterDumpsWindow(settings, library, on_save=lambda: None, watcher=watcher)
    qtbot.addWidget(window)

    path = tmp_path / "Beeta-Spellbook.txt"
    path.write_text(SPELLBOOK, encoding="utf-8")
    assert window.import_file(path)
    assert watcher.files == [path]
    assert library.characters() == []  # nothing written here; the tick does it

    window.import_now()
    assert watcher.scans == 1


def test_delete_removes_the_selected_snapshot(env: Env) -> None:
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK, T0)
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK_PLUS, T0 + timedelta(days=1))
    env.window.refresh()
    ref = env.library.latest("A", DumpKind.SPELLBOOK)
    assert ref is not None
    env.window.select_snapshot(ref)
    env.window.delete_selected()

    remaining = env.library.snapshots("A", DumpKind.SPELLBOOK)
    assert len(remaining) == 1
    assert remaining[0].captured_at == T0


def test_delete_at_the_character_row_forgets_the_character(env: Env) -> None:
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK, T0)
    env.store("A", DumpKind.INVENTORY, INVENTORY, T0)
    env.window.refresh()
    env.window._tree.setCurrentItem(env.window._tree.topLevelItem(0))
    env.window.delete_selected()
    assert env.library.characters() == []


def test_export_writes_the_client_format_back_out(env: Env, tmp_path: Path) -> None:
    env.store("A", DumpKind.SPELLBOOK, SPELLBOOK_PLUS, T0)
    env.window.refresh()
    target = tmp_path / "out.txt"
    assert env.window.export_selected(target)
    assert target.read_text(encoding="utf-8") == SPELLBOOK_PLUS


def test_empty_library_renders_without_a_selection(env: Env) -> None:
    env.window.refresh()
    assert env.window.current_dump() is None
    assert "No snapshot selected" in env.window._detail_title.text()
    assert "0 snapshots stored" in env.window._status.text()
