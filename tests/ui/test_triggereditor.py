"""pytest-qt tests for the trigger editor window."""

import io
import json
import zipfile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.triggers.builtin import EXPECTED_BUILTIN_COUNT, sync_builtin_triggers
from nparseplus.core.triggers.model import Trigger
from nparseplus.ui.triggereditor import TriggerEditorWindow

pytestmark = pytest.mark.qt


class FakeEngine:
    """Records set_triggers calls; structurally replaces TriggerEngine."""

    def __init__(self) -> None:
        self._triggers: list[Trigger] = []
        self.set_calls: list[list[Trigger]] = []

    @property
    def triggers(self) -> list[Trigger]:
        return self._triggers

    def set_triggers(self, triggers: list[Trigger]) -> None:
        self._triggers = list(triggers)
        self.set_calls.append(list(triggers))


class Env:
    def __init__(self) -> None:
        self.settings = Settings(players=[PlayerInfo(name="Gandalf", server="green")])
        self.settings.triggers, _ = sync_builtin_triggers([])
        self.engine = FakeEngine()
        self.saves = 0
        self.window = TriggerEditorWindow(self.settings, self.engine, on_save=self._on_save)
        self.window.confirm_unsaved = False

    def _on_save(self) -> None:
        self.saves += 1

    def settings_trigger(self, trigger_id: str) -> Trigger | None:
        return next((t for t in self.settings.triggers if t.trigger_id == trigger_id), None)


@pytest.fixture
def env(qtbot) -> Env:
    environment = Env()
    qtbot.addWidget(environment.window)
    return environment


def test_tree_shows_builtin_folders_and_all_triggers(env: Env) -> None:
    win = env.window
    assert len(win.trigger_ids()) == EXPECTED_BUILTIN_COUNT
    folders = win.folder_names()
    assert len(folders) > 1
    assert "Encounters" in folders


def test_new_trigger_apply_updates_settings_and_engine(env: Env) -> None:
    win = env.window
    win.new_trigger()
    created = win.current_trigger()
    assert created is not None and created.trigger_enabled
    win.name_edit.setText("My Alert")
    win.search_edit.setText("hello world")
    win.apply()

    saved = env.settings_trigger(created.trigger_id)
    assert saved is not None
    assert saved.trigger_name == "My Alert"
    assert saved.search_text == "hello world"
    assert not saved.is_built_in
    assert env.engine.set_calls, "engine.set_triggers was not called"
    assert any(t.trigger_id == created.trigger_id for t in env.engine.set_calls[-1])
    assert env.saves == 1
    assert win.item_for(created.trigger_id) is not None


def test_checkbox_toggle_flips_enabled_after_apply(env: Env) -> None:
    win = env.window
    trigger_id = win.trigger_ids()[0]
    before = env.settings_trigger(trigger_id).trigger_enabled
    item = win.item_for(trigger_id)
    item.setCheckState(0, Qt.CheckState.Unchecked if before else Qt.CheckState.Checked)
    win.apply()
    assert env.settings_trigger(trigger_id).trigger_enabled is (not before)


def test_editing_builtin_sets_customized(env: Env) -> None:
    win = env.window
    builtin_id = next(t.trigger_id for t in env.settings.triggers if t.is_built_in)
    win.select_trigger(builtin_id)
    win.name_edit.setText(win.name_edit.text() + " edited")
    win.apply()
    saved = env.settings_trigger(builtin_id)
    assert saved.customized is True
    assert "(customized)" in win.item_for(builtin_id).text(0)


def test_enabled_toggle_alone_does_not_customize_builtin(env: Env) -> None:
    win = env.window
    builtin_id = next(t.trigger_id for t in env.settings.triggers if t.is_built_in)
    item = win.item_for(builtin_id)
    was = item.checkState(0) == Qt.CheckState.Checked
    item.setCheckState(0, Qt.CheckState.Unchecked if was else Qt.CheckState.Checked)
    win.apply()
    assert env.settings_trigger(builtin_id).customized is False


def test_testbox_reports_match_and_expanded_output(env: Env) -> None:
    win = env.window
    win.new_trigger()
    win.search_edit.setText("You have been slain")
    win.basic_display_check.setChecked(True)
    win.basic_display_edit.setText("RIP {c}")
    win.test_line_edit.setText("You have been slain by a gnoll!")
    win.run_test()
    text = win.test_result.text()
    assert "Matched" in text
    assert "RIP Gandalf" in text  # {c} expanded from settings.players[0]


def test_testbox_reports_no_match(env: Env) -> None:
    win = env.window
    win.new_trigger()
    win.search_edit.setText("You have been slain")
    win.test_line_edit.setText("A gnoll hits YOU for 12 points of damage.")
    win.run_test()
    assert win.test_result.text() == "No match."


def test_delete_removes_user_trigger(env: Env) -> None:
    win = env.window
    win.new_trigger()
    created_id = win.current_trigger().trigger_id
    win.apply()
    assert env.settings_trigger(created_id) is not None

    win.select_trigger(created_id)
    win.delete_current()
    assert win.item_for(created_id) is None
    win.apply()
    assert env.settings_trigger(created_id) is None
    assert len(env.settings.triggers) == EXPECTED_BUILTIN_COUNT


def test_builtin_cannot_be_deleted_only_disabled(env: Env) -> None:
    win = env.window
    builtin_id = next(
        t.trigger_id for t in env.settings.triggers if t.is_built_in and t.trigger_enabled
    )
    win.select_trigger(builtin_id)
    assert win.delete_button.text() == "Disable"
    win.delete_current()
    win.apply()
    saved = env.settings_trigger(builtin_id)
    assert saved is not None, "built-in must survive Delete"
    assert saved.trigger_enabled is False
    assert len(env.settings.triggers) == EXPECTED_BUILTIN_COUNT


def test_duplicate_builtin_creates_user_copy(env: Env) -> None:
    win = env.window
    builtin = next(t for t in env.settings.triggers if t.is_built_in)
    win.select_trigger(builtin.trigger_id)
    win.duplicate_current()
    copy = win.current_trigger()
    assert copy.trigger_id != builtin.trigger_id
    assert copy.trigger_name.endswith("(copy)")
    assert copy.is_built_in is False and copy.built_in_id is None
    assert copy.search_text == builtin.search_text
    win.apply()
    assert env.settings_trigger(copy.trigger_id) is not None


def test_revert_builtin_restores_stock_definition(env: Env) -> None:
    win = env.window
    builtin = next(t for t in env.settings.triggers if t.is_built_in)
    stock_name = builtin.trigger_name
    win.select_trigger(builtin.trigger_id)
    win.name_edit.setText("Renamed by user")
    win.apply()
    assert env.settings_trigger(builtin.trigger_id).customized is True

    win.select_trigger(builtin.trigger_id)
    win.revert_current()
    win.apply()
    saved = env.settings_trigger(builtin.trigger_id)
    assert saved.customized is False
    assert saved.trigger_name == stock_name
    assert "(customized)" not in win.item_for(builtin.trigger_id).text(0)


def test_close_persists_geometry(env: Env) -> None:
    win = env.window
    win.setGeometry(50, 60, 700, 500)
    win.close()
    state = env.settings.windows["triggereditor"]
    assert state.geometry == (50, 60, 700, 500)
    assert env.saves == 1


def _mute_boxes(monkeypatch) -> dict[str, list[str]]:
    """Silence the modal result boxes, recording their messages."""
    shown: dict[str, list[str]] = {"information": [], "warning": []}
    for kind in shown:
        monkeypatch.setattr(
            QMessageBox,
            kind,
            staticmethod(lambda *args, _k=kind, **kwargs: shown[_k].append(args[2])),
        )
    return shown


def _export_to(env: Env, monkeypatch, tmp_path) -> dict:
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), ""))
    )
    env.window.export_triggers()
    return json.loads(out.read_text(encoding="utf-8"))


def test_export_all_skips_pristine_builtins(env: Env, monkeypatch, tmp_path) -> None:
    win = env.window
    win.new_trigger()
    win.name_edit.setText("Mine")
    win.search_edit.setText("hello")
    builtin_id = next(t.trigger_id for t in env.settings.triggers if t.is_built_in)
    win.select_trigger(builtin_id)
    win.name_edit.setText(win.name_edit.text() + " edited")
    win.tree.setCurrentItem(None)  # no selection -> export everything shareable
    _mute_boxes(monkeypatch)

    payload = _export_to(env, monkeypatch, tmp_path)
    assert payload["format"] == "nparseplus-triggers"
    names = {t["trigger_name"] for t in payload["triggers"]}
    assert "Mine" in names
    assert any(name.endswith("edited") for name in names)  # customized built-in ships
    assert len(payload["triggers"]) == 2  # pristine built-ins do not


def test_export_selected_trigger_exports_just_it(env: Env, monkeypatch, tmp_path) -> None:
    win = env.window
    builtin = next(t for t in env.settings.triggers if t.is_built_in)
    win.select_trigger(builtin.trigger_id)
    _mute_boxes(monkeypatch)

    payload = _export_to(env, monkeypatch, tmp_path)
    assert [t["trigger_name"] for t in payload["triggers"]] == [builtin.trigger_name]


def test_export_selected_folder_exports_its_triggers(env: Env, monkeypatch, tmp_path) -> None:
    win = env.window
    for name in ("One", "Two"):
        win.new_trigger()
        win.name_edit.setText(name)
        win.search_edit.setText(f"search {name}")
    folder = next(
        win.tree.topLevelItem(i)
        for i in range(win.tree.topLevelItemCount())
        if win.tree.topLevelItem(i).text(0) == "Custom"
    )
    win.tree.setCurrentItem(folder)
    _mute_boxes(monkeypatch)

    payload = _export_to(env, monkeypatch, tmp_path)
    assert {t["trigger_name"] for t in payload["triggers"]} == {"One", "Two"}


def test_import_appends_sanitized_and_apply_lands_them(env: Env, monkeypatch, tmp_path) -> None:
    win = env.window
    pack = tmp_path / "pack.json"
    shared = Trigger(
        trigger_name="Guild FTE",
        search_text="engages",
        category="Raids",
        is_built_in=True,
        built_in_id="SHOULD-BE-STRIPPED",
        folder_id="foreign",
        built_in_folder="Encounters",
    )
    pack.write_text(
        json.dumps(
            {
                "format": "nparseplus-triggers",
                "version": 1,
                "triggers": [shared.model_dump(mode="json")],
            }
        )
    )
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(pack), ""))
    )
    shown = _mute_boxes(monkeypatch)

    win.import_triggers()
    assert shown["information"] and "Imported 1 trigger" in shown["information"][0]
    win.apply()
    saved = next(t for t in env.settings.triggers if t.trigger_name == "Guild FTE")
    assert saved.is_built_in is False and saved.built_in_id is None
    assert saved.folder_id is None
    assert saved.category == "Encounters"
    assert saved.trigger_id != shared.trigger_id
    assert any(t.trigger_name == "Guild FTE" for t in env.engine.triggers)

    # Re-importing the same pack only skips duplicates.
    win.import_triggers()
    assert "skipped 1 duplicate" in shown["information"][-1]
    assert sum(t.trigger_name == "Guild FTE" for t in env.settings.triggers) == 1


def test_import_gina_gtp_package(env: Env, monkeypatch, tmp_path) -> None:
    win = env.window
    gtp = tmp_path / "pack.gtp"
    xml = (
        b"<SharedData><TriggerGroups><TriggerGroup><Name>Pack</Name><Triggers>"
        b"<Trigger><Name>Gina One</Name><TriggerText>{S} hits you</TriggerText>"
        b"<UseText>True</UseText><DisplayText>ow {S}</DisplayText></Trigger>"
        b"</Triggers></TriggerGroup></TriggerGroups></SharedData>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ShareData.xml", xml)
    gtp.write_bytes(buffer.getvalue())
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(gtp), ""))
    )
    _mute_boxes(monkeypatch)

    win.import_triggers()
    win.apply()
    imported = next(t for t in env.settings.triggers if t.trigger_name == "Gina One")
    assert imported.category == "Pack"
    assert imported.use_regex is True  # tokenized plain text promoted to regex


def test_import_invalid_file_warns_and_changes_nothing(env: Env, monkeypatch, tmp_path) -> None:
    win = env.window
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), ""))
    )
    shown = _mute_boxes(monkeypatch)

    before = len(env.settings.triggers)
    win.import_triggers()
    assert shown["warning"] and "Could not import" in shown["warning"][0]
    assert len(env.settings.triggers) == before
    assert len(win.trigger_ids()) == before


def test_move_trigger_to_group_relocates_and_dirties(env: Env) -> None:
    win = env.window
    win.new_trigger()
    tid = win.current_trigger().trigger_id
    win._dirty = False
    assert win.move_trigger_to_group(tid, "My Raid") is True
    assert win._dirty is True
    assert win.item_for(tid).parent().text(0) == "My Raid"
    assert win._trigger_by_id(tid).category == "My Raid"


def test_move_trigger_to_group_refuses_builtin(env: Env) -> None:
    win = env.window
    builtin = next(t for t in win._working if t.is_built_in)
    before = builtin.category
    assert win.move_trigger_to_group(builtin.trigger_id, "Nope") is False
    assert win._trigger_by_id(builtin.trigger_id).category == before


def test_combo_move_relocates_trigger(env: Env) -> None:
    win = env.window
    win.new_trigger()
    win.name_edit.setText("Combo Move")
    tid = win.current_trigger().trigger_id
    win.select_trigger(tid)
    win.group_combo.setCurrentText("Raiders")
    win.group_combo.lineEdit().editingFinished.emit()
    assert win.item_for(tid).parent().text(0) == "Raiders"
    assert win._trigger_by_id(tid).category == "Raiders"


def test_rename_group_rewrites_all_members(env: Env) -> None:
    win = env.window
    win.new_trigger()
    a_id = win.current_trigger().trigger_id
    win.move_trigger_to_group(a_id, "Old")
    win.tree.setCurrentItem(None)
    win.new_trigger()
    b_id = win.current_trigger().trigger_id
    win.move_trigger_to_group(b_id, "Old")

    assert win.rename_group("Old", "New") is True
    assert win._trigger_by_id(a_id).category == "New"
    assert win._trigger_by_id(b_id).category == "New"
    assert "New" in win.folder_names()
    assert "Old" not in win.folder_names()


def test_rename_group_refuses_builtin_folder(env: Env) -> None:
    win = env.window
    builtin = next(t for t in win._working if t.is_built_in)
    folder = builtin.built_in_folder or "Built-in"
    assert win.rename_group(folder, "Nope") is False
    assert win._trigger_by_id(builtin.trigger_id).built_in_folder == builtin.built_in_folder


def test_create_group_shows_empty_folder_until_populated(env: Env) -> None:
    win = env.window
    win.create_group("Empties")
    assert "Empties" in win.folder_names()
    win._rebuild_tree()
    assert "Empties" in win.folder_names()  # survives a rebuild while empty

    win.new_trigger()
    nid = win.current_trigger().trigger_id
    win.move_trigger_to_group(nid, "Empties")
    assert "Empties" not in win._extra_groups  # placeholder dropped once populated
    assert win.item_for(nid).parent().text(0) == "Empties"


def test_delete_group_removes_all_members(env: Env) -> None:
    win = env.window
    win.new_trigger()
    a_id = win.current_trigger().trigger_id
    win.move_trigger_to_group(a_id, "Doomed")
    win.tree.setCurrentItem(None)
    win.new_trigger()
    b_id = win.current_trigger().trigger_id
    win.move_trigger_to_group(b_id, "Doomed")

    win._dirty = False
    assert win.delete_group("Doomed") is True
    assert win._dirty is True
    assert win._trigger_by_id(a_id) is None
    assert win._trigger_by_id(b_id) is None
    assert "Doomed" not in win.folder_names()


def test_delete_group_refuses_builtin_folder(env: Env) -> None:
    win = env.window
    builtin = next(t for t in win._working if t.is_built_in)
    folder = builtin.built_in_folder or "Built-in"
    before = len(win._working)
    win._dirty = False
    assert win.delete_group(folder) is False
    assert len(win._working) == before
    assert win._dirty is False
    assert win._trigger_by_id(builtin.trigger_id) is not None


def test_delete_group_removes_empty_in_session_group(env: Env) -> None:
    win = env.window
    win.create_group("Empties")
    assert "Empties" in win.folder_names()
    assert win.delete_group("Empties") is True
    assert "Empties" not in win.folder_names()
    assert "Empties" not in win._extra_groups


def test_delete_group_clears_current_selection(env: Env) -> None:
    win = env.window
    win.new_trigger()
    tid = win.current_trigger().trigger_id
    win.move_trigger_to_group(tid, "Selected")
    win.select_trigger(tid)
    assert win.current_trigger() is not None

    assert win.delete_group("Selected") is True
    assert win._current is None


def test_delete_group_then_apply_drops_triggers_from_settings(env: Env) -> None:
    win = env.window
    win.new_trigger()
    win.name_edit.setText("Gone")
    tid = win.current_trigger().trigger_id
    win.move_trigger_to_group(tid, "Trash")
    win.apply()
    assert env.settings_trigger(tid) is not None

    win.delete_group("Trash")
    win.apply()
    assert env.settings_trigger(tid) is None


def test_delete_group_menu_path_without_confirm(env: Env) -> None:
    win = env.window
    win.confirm_delete = False
    win.new_trigger()
    tid = win.current_trigger().trigger_id
    win.move_trigger_to_group(tid, "MenuGone")

    win._delete_group_prompt("MenuGone")
    assert win._trigger_by_id(tid) is None
    assert "MenuGone" not in win.folder_names()


def test_new_trigger_inherits_selected_user_folder(env: Env) -> None:
    win = env.window
    win.new_trigger()
    first = win.current_trigger().trigger_id
    win.move_trigger_to_group(first, "MyGroup")
    win.select_trigger(first)
    win.new_trigger()
    assert win.current_trigger().category == "MyGroup"


def test_apply_persists_moved_categories(env: Env) -> None:
    win = env.window
    win.new_trigger()
    win.name_edit.setText("Persist")
    tid = win.current_trigger().trigger_id
    win.move_trigger_to_group(tid, "Kept")
    win.apply()
    saved = env.settings_trigger(tid)
    assert saved is not None
    assert saved.category == "Kept"


def test_export_import_round_trip_preserves_category(
    env: Env, monkeypatch, tmp_path, qtbot
) -> None:
    win = env.window
    win.new_trigger()
    win.name_edit.setText("Raider")
    win.search_edit.setText("engages you")
    tid = win.current_trigger().trigger_id
    win.move_trigger_to_group(tid, "My Raid")
    win.select_trigger(tid)  # export just this trigger
    _mute_boxes(monkeypatch)
    out = tmp_path / "rt.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), ""))
    )
    win.export_triggers()

    settings2 = Settings(players=[PlayerInfo(name="Frodo", server="green")])
    settings2.triggers, _ = sync_builtin_triggers([])
    win2 = TriggerEditorWindow(settings2, FakeEngine(), on_save=lambda: None)
    win2.confirm_unsaved = False
    qtbot.addWidget(win2)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(out), ""))
    )
    win2.import_triggers()
    win2.apply()
    imported = next(t for t in settings2.triggers if t.trigger_name == "Raider")
    assert imported.category == "My Raid"


def test_unsaved_changes_discard_on_close(env: Env, monkeypatch) -> None:
    win = env.window
    win.confirm_unsaved = True
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Discard),
    )
    win.new_trigger()
    win.close()
    assert len(env.settings.triggers) == EXPECTED_BUILTIN_COUNT  # edit was discarded
    assert not env.engine.set_calls
