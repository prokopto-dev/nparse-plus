"""Console right-click -> a prefilled trigger (#82).

The acceptance bar is a round trip: the menu action on a console row must
produce a trigger whose pattern matches the line it came from, with the user
editing nothing. These tests drive the real menu and the real editor, so the
timestamp strip, the tokenisation and the editor's own test box all have to
agree — the failure mode this feature has is a suggestion that only *looks*
right.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.events import LineEvent
from nparseplus.core.triggers.model import Trigger
from nparseplus.ui.consolewindow import (
    CREATE_TRIGGER_EXACT_LABEL,
    CREATE_TRIGGER_LABEL,
    ConsoleWindow,
)
from nparseplus.ui.triggereditor import DEFAULT_USER_GROUP, TriggerEditorWindow

pytestmark = pytest.mark.qt

T0 = datetime(2026, 7, 15, 10, 0, 0)


class FakeEngine:
    """Records set_triggers calls; structurally replaces TriggerEngine."""

    def __init__(self) -> None:
        self._triggers: list[Trigger] = []

    @property
    def triggers(self) -> list[Trigger]:
        return self._triggers

    def set_triggers(self, triggers: list[Trigger]) -> None:
        self._triggers = list(triggers)


class Wired:
    """A console and an editor wired together the way app.py wires them."""

    def __init__(self, qtbot, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.editor = TriggerEditorWindow(self.settings, FakeEngine(), on_save=lambda: None)
        self.editor.confirm_unsaved = False
        self.console = ConsoleWindow(self.settings)
        qtbot.addWidget(self.editor)
        qtbot.addWidget(self.console)
        self.console.player_name = self.editor.test_player_name
        self.console.create_trigger_requested.connect(self.editor.create_trigger_from_line)
        self.console.show()

    def push(self, line: str) -> None:
        self.console.handle_event(LineEvent(timestamp=T0, line=line, line_number=1))

    def point_of_first_row(self):
        """A widget-coordinate point inside the console's first line."""
        cursor = self.console._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        rect = self.console._text.cursorRect(cursor)
        return self.console._text.viewport().mapTo(self.console._text, rect.center())

    def menu_actions(self) -> dict[str, object]:
        menu = self.console.build_context_menu(self.point_of_first_row())
        return {action.text(): action for action in menu.actions()}


def test_the_console_offers_a_create_trigger_action(qtbot) -> None:
    wired = Wired(qtbot)
    wired.push("Gorenaire begins to cast a spell.")
    actions = wired.menu_actions()

    # The standard menu survives: Copy is still there.
    assert any("Copy" in text for text in actions)
    assert CREATE_TRIGGER_LABEL in actions
    # A tokenised line offers the literal form too — the user picks.
    assert CREATE_TRIGGER_EXACT_LABEL in actions


def test_the_action_makes_a_trigger_that_matches_its_own_line(qtbot) -> None:
    wired = Wired(qtbot)
    line = "Gorenaire begins to cast a spell."
    wired.push(line)

    wired.menu_actions()[CREATE_TRIGGER_LABEL].trigger()

    trigger = wired.editor.current_trigger()
    assert trigger is not None
    assert trigger.search_text == r"{name}\ begins\ to\ cast\ a\ spell\."
    assert trigger.matches(line)
    # The console's [HH:MM:SS] prefix never reaches the pattern: the pipeline
    # matches on LineInfo.message.
    assert "10:00:00" not in trigger.search_text


def test_the_prefilled_trigger_reports_matched_without_any_editing(qtbot) -> None:
    wired = Wired(qtbot)
    wired.push("Gorenaire begins to cast a spell.")

    wired.menu_actions()[CREATE_TRIGGER_LABEL].trigger()

    # create_trigger_from_line primes the test box and runs it, so the window
    # opens already reporting the answer.
    assert wired.editor.test_line_edit.text() == "Gorenaire begins to cast a spell."
    assert wired.editor.test_result.text().startswith("Matched.")
    wired.editor.run_test()
    assert wired.editor.test_result.text().startswith("Matched.")


def test_the_player_name_becomes_a_context_token_the_editor_can_test(qtbot) -> None:
    settings = Settings(players=[PlayerInfo(name="Soandso", server="green")])
    wired = Wired(qtbot, settings)
    line = "Gorenaire hits Soandso for 500 points of damage."
    wired.push(line)

    wired.menu_actions()[CREATE_TRIGGER_LABEL].trigger()

    trigger = wired.editor.current_trigger()
    assert trigger is not None
    assert "{c}" in trigger.search_text
    # The {c} pattern only matches once the player name is bound — the editor
    # binds the same name the console tokenised with, which is the whole
    # reason both sides read test_player_name().
    assert wired.editor.test_result.text().startswith("Matched.")


def test_a_metacharacter_line_still_matches(qtbot) -> None:
    wired = Wired(qtbot)
    line = "Gorenaire says, 'Who dares (again)? [3+4]'"
    wired.push(line)

    wired.menu_actions()[CREATE_TRIGGER_LABEL].trigger()

    trigger = wired.editor.current_trigger()
    assert trigger is not None
    assert trigger.matches(line)
    assert wired.editor.test_result.text().startswith("Matched.")


def test_the_exact_action_makes_a_plain_text_trigger(qtbot) -> None:
    wired = Wired(qtbot)
    line = "Gorenaire begins to cast a spell."
    wired.push(line)

    wired.menu_actions()[CREATE_TRIGGER_EXACT_LABEL].trigger()

    trigger = wired.editor.current_trigger()
    assert trigger is not None
    assert trigger.search_text == line
    assert trigger.use_regex is False
    assert trigger.matches(line)
    # Literal means literal: another mob's line must not match it.
    assert not trigger.matches("Lord Nagafen begins to cast a spell.")


@pytest.mark.parametrize("label", [CREATE_TRIGGER_LABEL, CREATE_TRIGGER_EXACT_LABEL])
def test_a_brace_in_the_log_never_rewrites_the_overlay_text(qtbot, label: str) -> None:
    # End to end, both offers: the log line literally contains "{c}", and the
    # display text is EXPANDED before it reaches the overlay. Copying the
    # braces through made the alert announce the player's own name.
    settings = Settings(players=[PlayerInfo(name="Zzz", server="green")])
    wired = Wired(qtbot, settings)
    line = "Gorenaire tells you, 'cast {c} on me'"
    wired.push(line)

    wired.menu_actions()[label].trigger()

    trigger = wired.editor.current_trigger()
    assert trigger is not None
    assert trigger.matches(line)
    said = trigger.expand(trigger.effective_basic().display_text)
    assert "Zzz" not in said
    assert said == "Gorenaire tells you, 'cast c on me'"
    # The editor's own test box shows the same expansion, so what the user
    # reads before saving is what the overlay will say.
    assert "Zzz" not in wired.editor.test_result.text()


def test_a_counter_token_in_the_log_is_not_a_live_counter(qtbot) -> None:
    wired = Wired(qtbot)
    line = "Gorenaire yells {COUNTER} times"
    wired.push(line)

    wired.menu_actions()[CREATE_TRIGGER_LABEL].trigger()

    trigger = wired.editor.current_trigger()
    assert trigger is not None
    assert trigger.matches(line)  # populates the {name} capture
    trigger.current_counter = 42
    assert trigger.expand(trigger.effective_basic().display_text) == (
        "Gorenaire yells COUNTER times"
    )


def test_a_line_with_no_token_offers_only_one_action(qtbot) -> None:
    wired = Wired(qtbot)
    wired.push("You begin casting Clarity.")
    actions = wired.menu_actions()

    # With nothing tokenised the two offers would be the same trigger.
    assert CREATE_TRIGGER_LABEL in actions
    assert CREATE_TRIGGER_EXACT_LABEL not in actions


def test_a_new_trigger_lands_in_a_user_group_and_is_enabled(qtbot) -> None:
    wired = Wired(qtbot)
    wired.push("Gorenaire begins to cast a spell.")

    wired.menu_actions()[CREATE_TRIGGER_LABEL].trigger()

    trigger = wired.editor.current_trigger()
    assert trigger is not None
    # Built-ins are read-only, so a created trigger can only be a user one.
    assert trigger.is_built_in is False
    assert trigger.category == DEFAULT_USER_GROUP
    assert trigger.trigger_enabled is True
    assert trigger.trigger_name == "Gorenaire begins to cast a spell"


def test_an_empty_console_row_offers_nothing(qtbot) -> None:
    wired = Wired(qtbot)
    actions = wired.menu_actions()

    assert CREATE_TRIGGER_LABEL not in actions
    assert wired.editor.create_trigger_from_line("   ") is None
