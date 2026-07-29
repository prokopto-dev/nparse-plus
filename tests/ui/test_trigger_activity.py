"""pytest-qt tests for the trigger Activity tab (#31)."""

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt

from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.events import LineEvent, TriggerFiredEvent
from nparseplus.core.triggers.builtin import sync_builtin_triggers
from nparseplus.core.triggers.model import Trigger
from nparseplus.ui.triggeractivity import MAX_ROWS, TriggerActivityView, format_action
from nparseplus.ui.triggereditor import TriggerEditorWindow

pytestmark = pytest.mark.qt

T0 = datetime(2026, 7, 14, 21, 4, 11, tzinfo=UTC)


def fire(
    name: str = "Rampage",
    *,
    group: str = "Raid Pack / Sebilis",
    line: str = "Trakanon goes on a rampage!",
    trigger_id: str = "tid-1",
    phase: str = "match",
    **kwargs,
) -> TriggerFiredEvent:
    return TriggerFiredEvent(
        timestamp=T0,
        line=line,
        trigger_id=trigger_id,
        trigger_name=name,
        group=group,
        phase=phase,
        **kwargs,
    )


@pytest.fixture
def view(qtbot) -> TriggerActivityView:
    widget = TriggerActivityView()
    qtbot.addWidget(widget)
    widget.show()  # a hidden view records without rendering; see its own test
    return widget


# -- format_action -------------------------------------------------------------


def test_format_action_joins_every_output() -> None:
    text = format_action(
        fire(display_text="Rampage!", tts_text="rampage", timer_name="Rampage", timer_seconds=30)
    )
    assert text == "Display: Rampage!  •  TTS: rampage  •  Timer: Rampage 30s"


def test_format_action_labels_silent_and_timer_phases() -> None:
    assert format_action(fire()) == "(no output)"
    assert format_action(fire(phase="timer_ended", display_text="Over")).startswith(
        "Timer ended — Display: Over"
    )
    assert format_action(fire(phase="timer_cancelled", timer_name="Rampage")) == (
        "Timer ended early — Timer: Rampage"
    )
    assert "1m30s" in format_action(fire(timer_name="Long", timer_seconds=90))


# -- intake --------------------------------------------------------------------


def test_row_shows_trigger_group_and_line(view: TriggerActivityView) -> None:
    view.handle_event(fire(display_text="Rampage!"))

    assert view.row_count() == 1
    time_text, name, group, action, line = view.row_values(0)
    assert time_text == "21:04:11"
    assert name == "Rampage"
    assert group == "Raid Pack / Sebilis"
    assert action == "Display: Rampage!"
    assert line == "Trakanon goes on a rampage!"


def test_non_trigger_events_are_ignored(view: TriggerActivityView) -> None:
    view.handle_events([LineEvent(timestamp=T0, line="You gain experience!!"), fire()])

    assert view.row_count() == 1


def test_newest_row_is_on_top(view: TriggerActivityView) -> None:
    view.handle_event(fire("First"))
    view.handle_event(fire("Second"))

    assert view.row_values(0)[1] == "Second"
    assert view.row_values(1)[1] == "First"


def test_pause_suppresses_and_resumes(view: TriggerActivityView) -> None:
    view.handle_event(fire("Before"))
    view.set_paused(True)
    view.handle_event(fire("During"))
    assert view.row_count() == 1

    view.set_paused(False)
    view.handle_event(fire("After"))
    assert view.row_count() == 2
    assert view.row_values(0)[1] == "After"


def test_store_is_bounded(view: TriggerActivityView) -> None:
    view.handle_events([fire(f"Trigger {i}") for i in range(MAX_ROWS + 100)])

    assert view.record_count() == MAX_ROWS
    assert view.row_count() == MAX_ROWS
    # The oldest fires fell off the end.
    assert "Trigger 0" not in {view.row_values(i)[1] for i in range(view.row_count())}


def test_timer_phase_rows_render_muted_but_stay_usable(view: TriggerActivityView) -> None:
    view.handle_event(fire(phase="timer_ended", timer_name="Rampage", trigger_id="tid-timer"))
    view.handle_event(fire(phase="match"))

    assert view.row_count() == 2
    muted = view.table.item(1, 1)
    plain = view.table.item(0, 1)
    assert muted.foreground() != plain.foreground()
    # Muted is a colour choice, not a disabled flag: the row must still be
    # selectable and able to jump to its trigger.
    assert muted.flags() & Qt.ItemFlag.ItemIsEnabled
    assert view._trigger_id_at(1) == "tid-timer"


def test_timer_phase_rows_survive_a_hidden_repopulate(qtbot) -> None:
    widget = TriggerActivityView()
    qtbot.addWidget(widget)
    widget.handle_event(fire(phase="timer_cancelled", timer_name="Rampage"))

    widget.show()  # repopulate path renders the muted row for the first time

    assert widget.row_count() == 1
    assert widget.row_values(0)[3] == "Timer ended early — Timer: Rampage"


def test_clear_empties_the_store(view: TriggerActivityView) -> None:
    view.handle_event(fire())
    view.clear()

    assert view.row_count() == 0
    assert view.record_count() == 0


# -- filter --------------------------------------------------------------------


def test_filter_narrows_and_restores(view: TriggerActivityView) -> None:
    view.handle_event(fire("Rampage", group="Raid Pack / Sebilis"))
    view.handle_event(fire("Enrage", group="Custom", line="a gnoll enrages"))

    view.set_filter("rampage")
    assert view.row_count() == 1
    assert view.row_values(0)[1] == "Rampage"
    # The store is untouched by filtering.
    assert view.record_count() == 2

    view.set_filter("")
    assert view.row_count() == 2


def test_filter_also_matches_group_and_line(view: TriggerActivityView) -> None:
    view.handle_event(fire("Rampage", group="Raid Pack / Sebilis"))
    view.handle_event(fire("Enrage", group="Custom", line="a gnoll enrages"))

    view.set_filter("sebilis")
    assert view.row_count() == 1
    view.set_filter("gnoll")
    assert view.row_count() == 1
    assert view.row_values(0)[1] == "Enrage"


def test_filtered_events_still_record_while_filtered(view: TriggerActivityView) -> None:
    view.set_filter("rampage")
    view.handle_event(fire("Enrage", group="Custom", line="a gnoll enrages"))

    assert view.row_count() == 0
    assert view.record_count() == 1
    view.set_filter("")
    assert view.row_count() == 1


# -- visibility gating ---------------------------------------------------------


def test_hidden_view_records_and_repopulates_on_show(qtbot) -> None:
    widget = TriggerActivityView()
    qtbot.addWidget(widget)
    for i in range(3):
        widget.handle_event(fire(f"Trigger {i}"))

    assert widget.record_count() == 3
    assert widget.row_count() == 0  # nothing rendered while hidden

    widget.show()
    assert widget.row_count() == 3
    assert widget.row_values(0)[1] == "Trigger 2"


# -- jumping back to the trigger -----------------------------------------------


def test_double_click_requests_a_jump(view: TriggerActivityView, qtbot) -> None:
    view.handle_event(fire(trigger_id="tid-abc"))

    with qtbot.waitSignal(view.jump_requested) as blocker:
        view.table.itemDoubleClicked.emit(view.table.item(0, 0))
    assert blocker.args == ["tid-abc"]


class FakeEngine:
    """Structurally replaces TriggerEngine for the editor window."""

    def __init__(self) -> None:
        self._triggers: list[Trigger] = []

    @property
    def triggers(self) -> list[Trigger]:
        return self._triggers

    def set_triggers(self, triggers: list[Trigger]) -> None:
        self._triggers = list(triggers)


@pytest.fixture
def editor(qtbot) -> TriggerEditorWindow:
    settings = Settings(players=[PlayerInfo(name="Gandalf", server="green")])
    settings.triggers, _ = sync_builtin_triggers([])
    window = TriggerEditorWindow(settings, FakeEngine(), on_save=lambda: None)
    window.confirm_unsaved = False
    qtbot.addWidget(window)
    return window


def test_editor_has_both_tabs_and_keeps_its_widgets(editor: TriggerEditorWindow) -> None:
    assert editor.tabs.count() == 2
    assert editor.tabs.tabText(0) == "Triggers"
    assert editor.tabs.tabText(1) == "Activity"
    assert editor.tabs.currentIndex() == 0
    # The pre-existing public surface is untouched.
    assert editor.tree is not None
    assert editor.name_edit is not None
    assert editor.apply_button is not None


def test_show_trigger_switches_tab_and_selects(editor: TriggerEditorWindow) -> None:
    trigger_id = editor.trigger_ids()[0]
    editor.tabs.setCurrentIndex(1)

    assert editor.show_trigger(trigger_id) is True
    assert editor.tabs.currentIndex() == 0
    current = editor.current_trigger()
    assert current is not None and current.trigger_id == trigger_id


def test_show_trigger_on_a_deleted_id_is_a_safe_no_op(editor: TriggerEditorWindow) -> None:
    editor.tabs.setCurrentIndex(1)

    assert editor.show_trigger("gone-with-the-wind") is False
    assert editor.tabs.currentIndex() == 1  # left where the user was


def test_editor_passthrough_feeds_the_activity_tab(editor: TriggerEditorWindow) -> None:
    editor.handle_events([fire(), LineEvent(timestamp=T0, line="ignored")])

    assert editor.activity.record_count() == 1


def test_activity_jump_signal_is_wired_to_the_editor(editor: TriggerEditorWindow) -> None:
    trigger_id = editor.trigger_ids()[0]
    editor.tabs.setCurrentIndex(1)

    editor.activity.jump_requested.emit(trigger_id)

    assert editor.tabs.currentIndex() == 0
    current = editor.current_trigger()
    assert current is not None and current.trigger_id == trigger_id
