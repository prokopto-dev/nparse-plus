"""pytest-qt tests for the Macro Editor window."""

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from nparseplus.config.settings import Settings
from nparseplus.core import socials as socials_core
from nparseplus.core.socials import Social
from nparseplus.core.socials_exchange import dump_socials
from nparseplus.core.socialstore import SocialOrigin
from nparseplus.ui import macroeditor
from nparseplus.ui.macroeditor import (
    CONFLICT_FREE,
    CONFLICT_OVERWRITE,
    CONFLICT_SKIP,
    DUPLICATE_PLACE,
    MacroEditorWindow,
    describe_import,
)

pytestmark = pytest.mark.qt

XANTIK = """[Defaults]
Version=1

[Socials]
Page1Button1Name=Assist
Page1Button1Color=13
Page1Button1Line1=/assist
Page2Button3Name=Sit
Page2Button3Color=4
Page2Button3Line1=/sit

[Friends]
Friend0=Alice

[KeyMaps]
Forward=W
"""

BEETA = """[Socials]
Page1Button1Name=Old
Page1Button1Color=1
Page1Button1Line1=/old
"""


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.eq_dir = tmp_path / "eq"
        (self.eq_dir / "uifiles").mkdir(parents=True)
        (self.eq_dir / "eqgame.exe").write_text("")
        self.xantik = self.eq_dir / "Xantik_P1999Green.ini"
        self.xantik.write_text(XANTIK)
        self.beeta = self.eq_dir / "Beeta_P1999Green.ini"
        self.beeta.write_text(BEETA)

        self.store_dir = tmp_path / "store"
        self.store_dir.mkdir()
        self.settings = Settings()
        self.settings.general.eq_install_dir = self.eq_dir
        self.saves = 0
        self.window = MacroEditorWindow(
            self.settings, on_save=self._on_save, store_dir=self.store_dir
        )
        self.window.confirm_unsaved = False
        self.window.warn_eq_running = False

    def _on_save(self) -> None:
        self.saves += 1

    def select_character(self, name: str) -> None:
        index = self.window.character_combo.findText(name)
        assert index >= 0, f"no character {name}"
        self.window.character_combo.setCurrentIndex(index)


@pytest.fixture
def env(qtbot, tmp_path: Path) -> Env:
    environment = Env(tmp_path)
    qtbot.addWidget(environment.window)
    return environment


def _loaded(env: Env, character: str = "Xantik") -> MacroEditorWindow:
    env.select_character(character)
    env.window.load()
    return env.window


# -- loading -----------------------------------------------------------------


def test_load_populates_characters_and_grid(env: Env) -> None:
    win = _loaded(env)
    assert [
        env.window.character_combo.itemText(i) for i in range(env.window.character_combo.count())
    ] == ["Beeta", "Xantik"]
    assert win.social_at(1, 1).name == "Assist"
    assert win.social_at(2, 3).lines == ["/sit"]
    assert win.social_at(1, 2) is None


def test_load_marks_everything_from_the_game(env: Env) -> None:
    win = _loaded(env)
    assert win.store().origin_at(1, 1) is SocialOrigin.GAME


def test_preflight_disables_writes_without_an_eq_install(env: Env) -> None:
    env.settings.general.eq_install_dir = None
    win = env.window
    win.load()
    assert "EQ install directory" in win.status.text()
    assert not win.save_button.isEnabled()
    assert not win.import_button.isEnabled()


# -- editing and saving ------------------------------------------------------


def test_save_writes_backs_up_and_preserves_other_sections(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    win.name_edit.setText("Assist Main")
    assert win.save_to_character() is True

    text = env.xantik.read_text()
    assert "Page1Button1Name=Assist Main" in text
    assert "[Friends]" in text and "Friend0=Alice" in text
    assert "[KeyMaps]" in text and "Forward=W" in text
    backup = env.eq_dir / socials_core.BACKUP_DIR_NAME / env.xantik.name
    assert "Page1Button1Name=Assist" in backup.read_text()


def test_edits_are_not_written_until_save(env: Env) -> None:
    win = _loaded(env)
    before = env.xantik.read_text()
    win.select_slot(1, 1)
    win.name_edit.setText("Not yet")
    assert env.xantik.read_text() == before


def test_clearing_a_slot_removes_it_on_save(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(2, 3)
    win._clear_current()
    assert win.save_to_character() is True
    assert socials_core.read_socials(env.xantik).at(2, 3) is None


def test_eq_running_warning_can_cancel_the_save(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    win = _loaded(env)
    win.warn_eq_running = True
    monkeypatch.setattr(macroeditor, "eq_is_running", lambda: True)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    before = env.xantik.read_text()

    win.select_slot(1, 1)
    win.name_edit.setText("Blocked")
    assert win.save_to_character() is False
    assert env.xantik.read_text() == before


# -- provenance --------------------------------------------------------------


def test_edited_slot_comes_back_as_local_and_untouched_stays_game(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    win.name_edit.setText("Assist Main")
    win.save_to_character()

    win.load()
    assert win.store().origin_at(1, 1) is SocialOrigin.LOCAL
    assert win.store().origin_at(2, 3) is SocialOrigin.GAME


def test_a_no_op_save_does_not_relabel_existing_macros(env: Env) -> None:
    """Saving with nothing edited must not wipe provenance."""
    win = _loaded(env)
    win.select_slot(1, 1)
    win.name_edit.setText("Assist Main")
    win.save_to_character()
    win.load()
    assert win.store().origin_at(1, 1) is SocialOrigin.LOCAL

    win.save_to_character()  # nothing edited this time
    win.load()
    assert win.store().origin_at(1, 1) is SocialOrigin.LOCAL


def test_a_slot_changed_behind_our_back_flips_to_game(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    win.name_edit.setText("Assist Main")
    win.save_to_character()

    # Simulate the player editing that macro in game.
    env.xantik.write_text(env.xantik.read_text().replace("Assist Main", "Edited In Game"))
    win.load()
    assert win.store().origin_at(1, 1) is SocialOrigin.GAME


def test_origin_filter_dims_rather_than_hides(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    win.name_edit.setText("Mine")
    win.save_to_character()
    win.load()

    total = len(win._buttons)
    win.filter_combo.setCurrentIndex(2)  # "Created here"
    assert len(win._buttons) == total  # nothing removed from the grid
    assert win._buttons[(1, 1)].property("dimmed") is False
    assert win._buttons[(2, 3)].property("dimmed") is True


# -- local library and restore ----------------------------------------------


def test_library_reports_lost_macros_and_restore_puts_them_back(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    win.name_edit.setText("Assist Main")
    win.save_to_character()

    # The client rewrites the ini on camp and drops the whole slot.
    stripped = "\n".join(
        line for line in env.xantik.read_text().splitlines() if "Page1Button1" not in line
    )
    env.xantik.write_text(stripped + "\n")
    win.load()
    assert [record.slot for record in win.store().lost()] == [(1, 1)]
    assert "no longer in the character's file" in win.library_status.text()

    assert win.restore_from_local_copy() == 1
    assert win.save_to_character() is True
    assert socials_core.read_socials(env.xantik).at(1, 1).name == "Assist Main"


def test_restore_with_nothing_lost_is_a_no_op(env: Env) -> None:
    win = _loaded(env)
    assert win.restore_from_local_copy() == 0
    assert "Nothing to restore" in win.status.text()


def test_a_broken_store_dir_still_lets_save_succeed(env: Env, tmp_path: Path) -> None:
    win = _loaded(env)
    # Point the mirror at a path that cannot be written.
    win._store_dir = tmp_path / "eq" / "Xantik_P1999Green.ini" / "nested"
    win.select_slot(1, 1)
    win.name_edit.setText("Still saved")

    assert win.save_to_character() is True
    assert "Page1Button1Name=Still saved" in env.xantik.read_text()
    assert "Local copy not updated" in win.status.text()


# -- export ------------------------------------------------------------------


def test_export_writes_a_socials_envelope(env: Env, tmp_path: Path) -> None:
    win = _loaded(env)
    out = tmp_path / "pack.json"
    assert win.export_pack(out, origins=None) == 2

    payload = json.loads(out.read_text())
    assert payload["format"] == "nparseplus-socials"
    assert payload["label"] == "Xantik (P1999Green)"
    assert {s["name"] for s in payload["socials"]} == {"Assist", "Sit"}


def test_export_only_what_i_authored_omits_game_macros(env: Env, tmp_path: Path) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    win.name_edit.setText("Mine")
    win.save_to_character()
    win.load()

    out = tmp_path / "mine.json"
    count = win.export_pack(out, origins=frozenset({SocialOrigin.LOCAL, SocialOrigin.IMPORTED}))
    assert count == 1
    assert [s["name"] for s in json.loads(out.read_text())["socials"]] == ["Mine"]


# -- import ------------------------------------------------------------------


def _pack(*socials: Social, label: str = "Friend (P1999Green)") -> bytes:
    return json.dumps(dump_socials(list(socials), label=label)).encode()


def test_import_places_into_the_pack_slot_when_free(env: Env) -> None:
    win = _loaded(env)
    raw = _pack(Social(page=4, button=4, name="Pull", lines=["/shout pulling"]))
    summary = win.import_pack(raw)
    assert summary["imported"] == 1
    assert win.social_at(4, 4).name == "Pull"


def test_import_prompts_on_a_slot_conflict_and_can_skip(env: Env) -> None:
    win = _loaded(env)
    raw = _pack(Social(page=1, button=1, name="Theirs", lines=["/theirs"]))
    summary = win.import_pack(raw, conflict_resolver=lambda _i, _o: CONFLICT_SKIP)
    assert summary["skipped"] == 1 and summary["imported"] == 0
    assert win.social_at(1, 1).name == "Assist"


def test_import_conflict_can_overwrite(env: Env) -> None:
    win = _loaded(env)
    raw = _pack(Social(page=1, button=1, name="Theirs", lines=["/theirs"]))
    summary = win.import_pack(raw, conflict_resolver=lambda _i, _o: CONFLICT_OVERWRITE)
    assert summary["overwritten"] == 1
    assert win.social_at(1, 1).name == "Theirs"


def test_import_conflict_can_move_to_a_free_slot(env: Env) -> None:
    win = _loaded(env)
    raw = _pack(Social(page=1, button=1, name="Theirs", lines=["/theirs"]))
    summary = win.import_pack(raw, conflict_resolver=lambda _i, _o: CONFLICT_FREE)
    assert summary["imported"] == 1
    assert win.social_at(1, 1).name == "Assist"  # untouched
    moved = [s for s in win.grid().socials if s.name == "Theirs"]
    assert moved and moved[0].slot != (1, 1)


def test_import_skips_a_macro_the_character_already_has(env: Env) -> None:
    win = _loaded(env)
    # Same macro, different slot — a duplicate, not a conflict.
    raw = _pack(Social(page=7, button=7, name="Assist", lines=["/assist"]))
    summary = win.import_pack(raw)
    assert summary["duplicates"] == 1
    assert summary["imported"] == 0
    assert win.social_at(7, 7) is None


def test_import_duplicate_can_be_placed_anyway(env: Env) -> None:
    win = _loaded(env)
    raw = _pack(Social(page=7, button=7, name="Assist", lines=["/assist"]))
    summary = win.import_pack(raw, duplicate_resolver=lambda _i, _t: DUPLICATE_PLACE)
    assert summary["imported"] == 1
    assert win.social_at(7, 7).name == "Assist"


def test_import_marks_arrivals_as_imported_after_save(env: Env) -> None:
    win = _loaded(env)
    win.import_pack(_pack(Social(page=4, button=4, name="Pull", lines=["/pull"])))
    win.save_to_character()
    win.load()
    assert win.store().origin_at(4, 4) is SocialOrigin.IMPORTED
    assert win.store().at(4, 4).source_label == "Friend (P1999Green)"


def test_import_rejects_a_trigger_pack(env: Env) -> None:
    win = _loaded(env)
    raw = json.dumps({"format": "nparseplus-triggers", "triggers": []}).encode()
    with pytest.raises(ValueError, match="nparseplus-triggers"):
        win.import_pack(raw)


def test_import_does_not_write_the_ini(env: Env) -> None:
    win = _loaded(env)
    before = env.xantik.read_text()
    win.import_pack(_pack(Social(page=4, button=4, name="Pull", lines=["/pull"])))
    assert env.xantik.read_text() == before


# -- copy to characters ------------------------------------------------------


def test_copy_to_replaces_the_target_grid(env: Env) -> None:
    win = _loaded(env)
    assert win.copy_to(["Beeta"], replace=True) == []

    target = socials_core.read_socials(env.beeta)
    assert [s.name for s in target.socials] == ["Assist", "Sit"]
    assert target.at(1, 1).name == "Assist"  # the old "Old" macro is gone


def test_copy_to_merge_keeps_the_targets_other_macros(env: Env) -> None:
    env.beeta.write_text(BEETA + "Page5Button5Name=Keep\nPage5Button5Line1=/keep\n")
    win = _loaded(env)
    assert win.copy_to(["Beeta"], replace=False) == []

    target = socials_core.read_socials(env.beeta)
    assert target.at(5, 5).name == "Keep"
    assert target.at(2, 3).name == "Sit"


def test_copy_to_backs_up_the_target(env: Env) -> None:
    win = _loaded(env)
    win.copy_to(["Beeta"], replace=True)
    backup = env.eq_dir / socials_core.BACKUP_DIR_NAME / env.beeta.name
    assert "Page1Button1Name=Old" in backup.read_text()


def test_copy_to_records_provenance_on_the_target(env: Env) -> None:
    win = _loaded(env)
    win.copy_to(["Beeta"], replace=True)

    env.select_character("Beeta")
    win.load()
    assert win.store().origin_at(1, 1) is SocialOrigin.IMPORTED
    assert win.store().at(1, 1).source_label == "Xantik"


def test_copy_to_never_writes_the_source(env: Env) -> None:
    win = _loaded(env)
    before = env.xantik.read_text()
    win.copy_to(["Xantik", "Beeta"], replace=True)
    assert env.xantik.read_text() == before


# -- duplicates --------------------------------------------------------------


def test_duplicate_groups_match_the_core_finder(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(3, 3)
    win.name_edit.setText("Assist")
    win.line_edits[0].setText("/assist")

    groups = win.duplicate_groups()
    assert len(groups) == 1
    assert {s.slot for s in groups[0].socials} == {(1, 1), (3, 3)}
    assert socials_core.find_duplicates(win.grid().socials) == groups


def test_duplicate_slots_are_badged_in_the_grid(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(3, 3)
    win.name_edit.setText("Assist")
    win.line_edits[0].setText("/assist")
    assert macroeditor.DUPLICATE_BADGE in win._buttons[(3, 3)].text()


# -- autocomplete ------------------------------------------------------------


def test_completion_context_finds_a_command_at_the_start_of_a_line() -> None:
    assert macroeditor.completion_context("/pet at", 7) == (0, "/pet at")
    # Commands only complete at the start; mid-line words are not commands.
    assert macroeditor.completion_context("say /pet", 8) is None


def test_completion_context_finds_a_token_anywhere() -> None:
    assert macroeditor.completion_context("/shout Pulling %T", 17) == (15, "%T")
    assert macroeditor.completion_context("/say %", 6) == (5, "%")


def test_completion_context_stops_at_a_space_after_a_token() -> None:
    # "%T " is finished; fall back to the command at the start of the line.
    assert macroeditor.completion_context("/shout %T now", 13) == (0, "/shout %T now")


def test_completion_context_uses_the_cursor_not_the_end() -> None:
    assert macroeditor.completion_context("/pet attack", 4) == (0, "/pet")


def test_completion_context_ignores_plain_text() -> None:
    assert macroeditor.completion_context("hello there", 11) is None
    assert macroeditor.completion_context("", 0) is None


def test_line_edit_offers_command_completions(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    edit = win.line_edits[0]
    edit.setText("/pet at")
    edit.textEdited.emit("/pet at")

    completer = edit.completer()
    assert completer.completionPrefix() == "/pet at"
    assert completer.currentCompletion() == "/pet attack"


def test_line_edit_offers_token_completions(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    edit = win.line_edits[0]
    edit.setText("/shout Pulling %T")
    edit.textEdited.emit("/shout Pulling %T")

    assert edit.completer().completionPrefix() == "%T"


def test_line_edit_inserts_a_token_without_eating_the_line(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    edit = win.line_edits[0]
    edit.setText("/shout Pulling %T")
    edit.setCursorPosition(17)
    edit._insert_completion("%T")

    assert edit.text() == "/shout Pulling %T"


def test_line_edit_inserts_a_command_over_the_typed_prefix(env: Env) -> None:
    win = _loaded(env)
    win.select_slot(1, 1)
    edit = win.line_edits[0]
    edit.setText("/pet at")
    edit.setCursorPosition(7)
    edit._insert_completion("/pet attack")

    assert edit.text() == "/pet attack"
    assert win.social_at(1, 1).lines[0] == "/pet attack"


# -- window plumbing ---------------------------------------------------------


def test_geometry_persists_on_close(env: Env) -> None:
    win = _loaded(env)
    win.setGeometry(30, 40, 800, 600)
    win.close()
    state = env.settings.windows["macroeditor"]
    assert state.geometry == (30, 40, 800, 600)
    assert env.saves >= 1


def test_unsaved_changes_prompt_can_save(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    win = _loaded(env)
    win.confirm_unsaved = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    win.select_slot(1, 1)
    win.name_edit.setText("Saved On Close")
    win.close()
    assert "Page1Button1Name=Saved On Close" in env.xantik.read_text()


# -- import: relative page layout (#34) --------------------------------------

TRIO = """[Socials]
Page1Button1Name=Held one
Page1Button1Color=1
Page1Button1Line1=/one
Page1Button4Name=Held two
Page1Button4Color=1
Page1Button4Line1=/two
Page1Button7Name=Held three
Page1Button7Color=1
Page1Button7Line1=/three
"""


def _trio(env: Env) -> MacroEditorWindow:
    """A character whose page 1 holds the three slots the pack wants."""
    (env.eq_dir / "Trio_P1999Green.ini").write_text(TRIO)
    env.window._refresh_characters()  # pick up the file just written
    return _loaded(env, "Trio")


def _rotation() -> bytes:
    return _pack(
        Social(page=1, button=1, name="Pull", lines=["/shout pulling"]),
        Social(page=1, button=4, name="Snare", lines=["/cast 2"]),
        Social(page=1, button=7, name="Med", lines=["/sit"]),
    )


def test_import_keeps_a_displaced_page_group_together(env: Env) -> None:
    win = _trio(env)
    summary = win.import_pack(_rotation(), conflict_resolver=lambda _i, _o: CONFLICT_FREE)

    assert summary["imported"] == 3
    assert summary["moved"] == 3 and summary["moved_intact"] == 1
    # Page 1 is full of the character's own; page 2 is the first wholly free
    # one, and every button is where the pack had it.
    assert summary["moved_page"] == 2
    assert [win.social_at(2, b).name for b in (1, 4, 7)] == ["Pull", "Snare", "Med"]
    # Nothing the character already had moved.
    assert [win.social_at(1, b).name for b in (1, 4, 7)] == ["Held one", "Held two", "Held three"]


def test_import_falls_back_to_the_flat_fill_with_no_free_page(env: Env) -> None:
    win = _trio(env)
    # Leave one macro on every page, so no page is wholly free.
    for page in range(2, win.grid().pages + 1):
        win.import_pack(
            _pack(Social(page=page, button=10, name=f"Filler {page}", lines=[f"/f{page}"]))
        )

    summary = win.import_pack(_rotation(), conflict_resolver=lambda _i, _o: CONFLICT_FREE)
    assert summary["imported"] == 3
    assert summary["moved"] == 3 and summary["moved_intact"] == 0
    assert summary["moved_page"] == 0
    placed = {s.name: s.slot for s in win.grid().socials}
    assert placed["Pull"] == (1, 2)  # the first hole, in order
    assert placed["Snare"] == (1, 3)


def test_import_moving_one_macro_still_takes_the_next_free_slot(env: Env) -> None:
    """A single macro has no relative arrangement; it must not claim a page."""
    win = _loaded(env)
    summary = win.import_pack(
        _pack(Social(page=1, button=1, name="Theirs", lines=["/theirs"])),
        conflict_resolver=lambda _i, _o: CONFLICT_FREE,
    )
    assert summary["moved"] == 1 and summary["moved_intact"] == 0
    assert win.social_at(1, 2).name == "Theirs"


def test_import_summary_says_which_strategy_moved_them() -> None:
    base = {
        "imported": 3,
        "duplicates": 0,
        "overwritten": 0,
        "skipped": 0,
        "unplaceable": 0,
        "moved": 3,
        "moved_intact": 1,
        "moved_page": 2,
    }
    intact = describe_import(base)
    assert "3 moved together to page 2, keeping their button positions" in intact

    flat = describe_import({**base, "moved_intact": 0, "moved_page": 0})
    assert "3 moved to free slots" in flat

    quiet = describe_import({**base, "moved": 0})
    assert "moved" not in quiet
