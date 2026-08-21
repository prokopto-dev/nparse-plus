"""pytest-qt tests for the M2 windows (DPS meter, event overlay, mob info, console)."""

import sys
from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.dps import FightTracker
from nparseplus.core.events import DamageEvent, LineEvent, OverlayEvent, TimerBarEvent
from nparseplus.core.handlers.consider import MobInfoState
from nparseplus.core.player import ActivePlayer
from nparseplus.core.timers import seconds_left
from nparseplus.core.zones import load_zone_database
from nparseplus.ui.consolewindow import ConsoleWindow
from nparseplus.ui.dpswindow import DpsMeterWindow
from nparseplus.ui.eventoverlay import EventOverlayWindow, _ChainLane
from nparseplus.ui.mobinfo import MobInfoWindow
from nparseplus.ui.overlaybase import format_mmss, ms_to_next_tick, tick_phase_error_ms

pytestmark = pytest.mark.qt

T0 = datetime(2026, 7, 15, 10, 0, 0)


def _damage(seconds: float, attacker: str, target: str, dmg: int) -> DamageEvent:
    return DamageEvent(
        timestamp=T0 + timedelta(seconds=seconds),
        target_name=target,
        attacker_name=attacker,
        damage_done=dmg,
        damage_type="slash",
    )


class _FakeBackend:
    def __init__(self) -> None:
        self.settings = Settings()
        self.fights = FightTracker()
        self.zones = load_zone_database()
        self.player = ActivePlayer()


@pytest.fixture
def backend() -> _FakeBackend:
    return _FakeBackend()


def test_dps_window_renders_fights(qtbot, backend: _FakeBackend) -> None:
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 20))
    backend.fights.add_damage(_damage(1, "Soandso", "a gnoll", 10))
    window = DpsMeterWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    assert window.current_targets() == ["a gnoll"]
    attackers = window.current_attackers()
    assert set(attackers) == {"You", "Soandso"}
    assert attackers[0] == "You"  # sorted by damage desc
    assert window.your_rows() == ["You"]
    assert "Best" in window.footer_text()


def test_dps_window_marks_your_pet_row(qtbot, backend: _FakeBackend) -> None:
    """A magician's two rows are both theirs, and the pet says which is which."""
    backend.fights.set_pet_name("Vexer")
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 20))
    backend.fights.add_damage(_damage(1, "Vexer", "a gnoll", 30))
    backend.fights.add_damage(_damage(1, "Soandso", "a gnoll", 10))
    window = DpsMeterWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    assert set(window.your_rows()) == {"You", "Vexer"}
    assert "Vexer (pet)" in window.row_names()
    assert "Soandso" in window.row_names()


def test_dps_window_shows_the_counting_mode(qtbot, backend: _FakeBackend) -> None:
    """A mode that excludes spell damage has to say so, not just read zero."""
    window = DpsMeterWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    assert window.mode_text() == "MELEE + MINE"
    backend.fights.configure(damage_sources="melee")
    window.refresh()
    assert window.mode_text() == "MELEE"
    backend.fights.configure(damage_sources="all")
    window.refresh()
    assert window.mode_text() == "ALL"


def test_dps_window_removes_stale_rows(qtbot, backend: _FakeBackend) -> None:
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 20))
    window = DpsMeterWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    assert window.current_targets() == ["a gnoll"]
    backend.fights.clear()
    window.refresh()
    assert window.current_targets() == []
    assert window.current_attackers() == []


def test_event_overlay_text_and_reset(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(OverlayEvent(text="DEATH LOOP", foreground="Red"))
    assert overlay.current_text() == "DEATH LOOP"
    assert overlay.is_active()
    # a reset for different text is ignored
    overlay.handle_event(OverlayEvent(text="other", reset=True))
    assert overlay.current_text() == "DEATH LOOP"
    overlay.handle_event(OverlayEvent(text="DEATH LOOP", reset=True))
    assert overlay.current_text() == ""
    assert not overlay.is_active()


def test_event_overlay_timer_bars(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(TimerBarEvent(name="Stun Breath", total_seconds=12, bar_color="Gold"))
    overlay.handle_event(TimerBarEvent(name="Wave of Heat", total_seconds=8))
    assert overlay.current_bar_names() == ["Stun Breath", "Wave of Heat"]
    # re-raising restarts (still one bar for the name)
    overlay.handle_event(TimerBarEvent(name="Stun Breath", total_seconds=12))
    assert overlay.current_bar_names().count("Stun Breath") == 1
    assert overlay.is_active()


def test_timer_bar_matches_spell_window_for_the_same_countdown(qtbot) -> None:
    """The two countdown surfaces must never disagree: an overlay bar and a
    spell-window row for the same timer show the same number at the same
    instant (the bar used to ceil while the window truncated)."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(TimerBarEvent(name="Stun Breath", total_seconds=12))
    entry = overlay._bars["Stun Breath"]

    for offset in (0.0, 3.4, 7.9, 11.2):
        now = entry.ends_at - timedelta(seconds=12 - offset)
        overlay._render_bar(entry, now)
        bar_seconds = overlay.bar_countdown_text("Stun Breath").removesuffix("s")
        assert format_mmss(seconds_left(entry.ends_at, now)) == f"00:{int(bar_seconds):02d}"


def test_timer_bar_anchor_is_on_the_second_grid(qtbot) -> None:
    """Bars are not TimersService rows, so nothing else would snap them."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(TimerBarEvent(name="Stun Breath", total_seconds=12))
    assert overlay._bars["Stun Breath"].ends_at.microsecond == 0


@pytest.mark.parametrize(
    ("micros", "expected"),
    [
        (0, 20),  # just after the second: wait out the grid offset
        (20_000, 250),  # exactly on a tick point: a whole slot to the next
        (100_000, 170),  # -> fires at .270 = the .250 slot + offset
        (999_000, 21),
    ],
)
def test_ms_to_next_tick(micros: int, expected: int) -> None:
    assert ms_to_next_tick(T0.replace(microsecond=micros), 250) == expected


@pytest.mark.parametrize(
    ("micros", "expected"),
    [
        (20_000, 0),  # on the tick point
        (25_000, 5),  # 5 ms late
        (15_000, -5),  # 5 ms early reads negative, not "245 late"
        (270_000, 0),  # the next slot is equally on-grid
    ],
)
def test_tick_phase_error_is_signed(micros: int, expected: int) -> None:
    assert tick_phase_error_ms(T0.replace(microsecond=micros), 250) == expected


def test_mob_info_renders_state(qtbot) -> None:
    state = MobInfoState()
    window = MobInfoWindow(Settings(), state)
    qtbot.addWidget(window)
    window.refresh()
    assert "Consider" in window.current_name()
    state.name = "Lord Nagafen"
    state.zone = "soldungb"
    state.spawn_seconds = 1320
    state.is_notable = True
    window.refresh()
    assert "Lord Nagafen" in window.current_name()
    assert "✪" in window.current_name()
    assert "22:00" in window.current_detail()


def test_console_appends_and_pauses(qtbot) -> None:
    window = ConsoleWindow(Settings())
    qtbot.addWidget(window)
    window.show()  # hidden consoles buffer to a backlog instead of the widget
    window.handle_event(LineEvent(timestamp=T0, line="You begin casting Clarity.", line_number=1))
    window.handle_event(LineEvent(timestamp=T0, line="You gain experience!!", line_number=2))
    assert window.line_count() >= 2
    before = window.line_count()
    window.set_paused(True)
    window.handle_event(LineEvent(timestamp=T0, line="ignored while paused", line_number=3))
    assert window.line_count() == before


def test_console_hidden_backlog_flushes_on_show(qtbot) -> None:
    window = ConsoleWindow(Settings())
    qtbot.addWidget(window)
    for i in range(3):
        window.handle_event(LineEvent(timestamp=T0, line=f"buffered {i}", line_number=i + 1))
    assert window.line_count() == 1  # empty document, nothing appended yet
    window.show()
    assert window.line_count() == 3
    assert "buffered 2" in window._text.toPlainText()


def test_event_overlay_configurable_duration(qtbot) -> None:
    overlay = EventOverlayWindow(clear_after_s=7.5)
    qtbot.addWidget(overlay)
    assert overlay._clear_timer.interval() == 7500


def test_event_overlay_ch_chain_lanes(qtbot) -> None:
    from nparseplus.core.events import CompleteHealEvent

    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(
            timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="Clericone"
        )
    )
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="002", caster="You")
    )
    overlay.handle_event(
        CompleteHealEvent(
            timestamp=T0, recipient="Backup", tag="CA", position="001", caster="Other"
        )
    )
    lanes = overlay.current_chain_lanes()
    assert lanes["Tanky"] == ["001", "002"]
    assert lanes["Backup"] == ["001"]
    assert overlay.is_active()


def test_ch_lane_timer_does_not_outlive_the_overlay(qtbot) -> None:
    """A pending lane-removal timer must be cancelled when the overlay dies.

    ``_on_complete_heal`` schedules a re-check just past the retention window.
    Unless that timer is bound to the widget's lifetime it still fires after
    the overlay is destroyed, and ``_update_visibility`` then reads
    already-deleted C++ children — raising ``RuntimeError: Internal C++ object
    (QLabel) already deleted`` *into the Qt event loop*, where pytest-qt blames
    whichever unrelated test is running at the time. That is exactly how this
    surfaced: a Windows-only failure in the trigger editor tests.
    """
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop

    from nparseplus.core.events import CompleteHealEvent

    # Tiny retention so the pending timer would fire almost immediately.
    overlay = EventOverlayWindow(ch_lane_retention_s=0.01)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="X")
    )
    assert overlay._chain_lanes  # a removal timer is now pending

    fired: list[str] = []
    original = overlay._maybe_remove_lane
    overlay._maybe_remove_lane = lambda target: (fired.append(target), original(target))

    raised: list[BaseException] = []
    original_hook = sys.excepthook
    sys.excepthook = lambda exc_type, exc, tb: raised.append(exc)
    try:
        # deleteLater() is not enough: processEvents() does not run deferred
        # deletes, so the C++ object would survive and prove nothing. Destroy
        # it outright, which is what widget teardown ends up doing.
        shiboken6.delete(overlay)
        deadline = QDeadlineTimer(600)  # well past the 0.01s + 250ms re-check
        while not deadline.hasExpired():
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    finally:
        sys.excepthook = original_hook

    assert fired == [], "the lane-removal timer ran after the overlay was destroyed"
    assert raised == [], f"pending timer raised after teardown: {raised}"


def test_ch_lane_has_ten_second_marker_cells(qtbot) -> None:
    """The lane is graduated into 10 one-second marker cells, each width/10
    (EQTool GetOrCreateChain's 10-cell strip), derived from the current width."""
    from nparseplus.core.events import CompleteHealEvent

    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="X")
    )
    lane = overlay._chain_lanes["Tanky"]
    cells = lane.cell_geometry()
    assert len(cells) == 10
    expected = lane.width() // 10
    assert all(cell.width() == expected for cell in cells)
    # Cells tile the lane left-to-right from x=0.
    assert [cell.x() for cell in cells] == [i * expected for i in range(10)]


def test_utility_section_routes_and_clears(qtbot) -> None:
    """#14: an OverlayEvent with section='utility' lands in the utility section
    (not the center text), shows its header, and a matching reset clears it."""
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(OverlayEvent(text="Rebuff: Joe", foreground="Gold", section="utility"))
    assert overlay.current_utility_texts() == ["Rebuff: Joe"]
    assert overlay.current_text() == ""  # not the center alert
    assert overlay._utility_header.isVisible()
    assert overlay.is_active()
    # A matching reset removes the line and hides the header.
    overlay.handle_event(OverlayEvent(text="Rebuff: Joe", reset=True, section="utility"))
    assert overlay.current_utility_texts() == []
    assert not overlay._utility_header.isVisible()


def test_ch_cadence_marks_new_and_existing_lanes(qtbot) -> None:
    """#15: a cadence callout sets the muted marker on existing lanes and is
    inherited by lanes created afterwards; new callouts update everything."""
    from nparseplus.core.events import CompleteHealCadenceEvent, CompleteHealEvent

    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    # A lane exists first; a cadence callout marks it.
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="", position="001", caster="X")
    )
    assert overlay._chain_lanes["Tanky"].cadence_seconds is None
    overlay.handle_event(CompleteHealCadenceEvent(timestamp=T0, seconds=4))
    assert overlay._chain_lanes["Tanky"].cadence_seconds == 4
    # A lane created after the callout inherits the current cadence.
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Backup", tag="", position="001", caster="Y")
    )
    assert overlay._chain_lanes["Backup"].cadence_seconds == 4
    # A fresh callout re-marks every lane.
    overlay.handle_event(CompleteHealCadenceEvent(timestamp=T0, seconds=6))
    assert overlay._chain_lanes["Tanky"].cadence_seconds == 6
    assert overlay._chain_lanes["Backup"].cadence_seconds == 6


def test_ch_cadence_preview_marker(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    lanes = [lane for row in overlay._preview_widgets for lane in row.findChildren(_ChainLane)]
    assert lanes and all(lane.cadence_seconds == 4 for lane in lanes)


def test_ch_chip_is_exactly_one_cell_wide(qtbot) -> None:
    """A chip spans exactly one second-marker cell so each cell is 1 s of travel."""
    from nparseplus.core.events import CompleteHealEvent

    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="X")
    )
    lane = overlay._chain_lanes["Tanky"]
    assert lane.chips
    assert lane.chips[0].width() == lane.cell_width()


def test_ch_lane_target_name_sits_beside_the_lane(qtbot) -> None:
    """Regression: the target name lives in its own column beside the lane
    (EQTool's separate grid column), so it cannot cover the "1" marker cell.
    Long names elide and carry the full name as a tooltip; removal tears out
    the whole [name | lane] row."""
    from PySide6.QtWidgets import QLabel

    from nparseplus.core.events import CompleteHealEvent

    target = "A Really Quite Long Npc Heal Target Name"
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient=target, tag="CA", position="001", caster="X")
    )
    lane = overlay._chain_lanes[target]
    # The lane itself holds only chips — no name label over the cell strip.
    assert lane.findChildren(QLabel) == lane.chips
    # The row holds the name label (full name in the tooltip when elided).
    assert lane.row is not None
    row_labels = [w for w in lane.row.findChildren(QLabel) if w not in lane.chips]
    assert [label.toolTip() for label in row_labels] == [target]
    row_widgets = [
        overlay._lanes_layout.itemAt(i).widget() for i in range(overlay._lanes_layout.count())
    ]
    assert lane.row in row_widgets
    # Removal tears out the whole row, name label included.
    overlay._remove_lane(target)
    row_widgets = [
        overlay._lanes_layout.itemAt(i).widget() for i in range(overlay._lanes_layout.count())
    ]
    assert lane.row not in row_widgets


def test_ch_lane_retention_keeps_lane_after_chips(qtbot) -> None:
    from nparseplus.core.events import CompleteHealEvent

    overlay = EventOverlayWindow(ch_lane_retention_s=20.0)
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="X")
    )
    lane = overlay._chain_lanes["Tanky"]
    # Simulate all chips having finished their slide: lane must persist
    # because the retention window since the last call has not elapsed.
    lane.chips.clear()
    overlay._maybe_remove_lane("Tanky")
    assert "Tanky" in overlay.current_chain_lanes()
    assert overlay.is_active()
    # Once the retention window has elapsed (simulated), the lane goes away.
    lane.last_call = lane.last_call - timedelta(seconds=21)
    overlay._maybe_remove_lane("Tanky")
    assert "Tanky" not in overlay.current_chain_lanes()


def test_ch_lane_never_removed_with_chips_in_flight(qtbot) -> None:
    from nparseplus.core.events import CompleteHealEvent

    overlay = EventOverlayWindow(ch_lane_retention_s=20.0)
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="X")
    )
    lane = overlay._chain_lanes["Tanky"]
    # Even with the retention window long past, in-flight chips pin the lane.
    lane.last_call = lane.last_call - timedelta(seconds=999)
    overlay._maybe_remove_lane("Tanky")
    assert "Tanky" in overlay.current_chain_lanes()


def test_ch_lane_sweep_force_removes_stuck_lane(qtbot) -> None:
    """Regression: a lane whose chips never empty (e.g. a chip animation's
    ``finished`` never fired) must still be force-removed by the sweep once it
    is idle past the force threshold — it must not linger indefinitely."""
    from PySide6.QtWidgets import QLabel

    from nparseplus.core.events import CompleteHealEvent

    overlay = EventOverlayWindow(ch_lane_retention_s=20.0)
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="X")
    )
    lane = overlay._chain_lanes["Tanky"]
    # Simulate a wedged chip whose _chip_done never runs: the chips list never
    # empties, so _maybe_remove_lane's gate would stay false forever.
    lane.chips.append(QLabel("stuck", lane))
    lane.last_call = lane.last_call - timedelta(seconds=999)
    overlay._sweep_lanes()
    assert "Tanky" not in overlay._chain_lanes
    assert "Tanky" not in overlay.current_chain_lanes()
    # Gone from the layout too.
    lane_widgets = [
        overlay._lanes_layout.itemAt(i).widget() for i in range(overlay._lanes_layout.count())
    ]
    assert lane not in lane_widgets
    # Sweep timer stands down once no lanes remain.
    assert not overlay._sweep_timer.isActive()


def test_ch_lane_sweep_keeps_recent_lane(qtbot) -> None:
    """Non-regression: a lane with a recent last_call (still within the force
    window) survives the sweep even if it has chips in flight."""
    from PySide6.QtWidgets import QLabel

    from nparseplus.core.events import CompleteHealEvent

    overlay = EventOverlayWindow(ch_lane_retention_s=20.0)
    qtbot.addWidget(overlay)
    overlay.handle_event(
        CompleteHealEvent(timestamp=T0, recipient="Tanky", tag="CA", position="001", caster="X")
    )
    lane = overlay._chain_lanes["Tanky"]
    lane.chips.append(QLabel("inflight", lane))
    lane.last_call = datetime.now()
    overlay._sweep_lanes()
    assert "Tanky" in overlay._chain_lanes
    assert overlay._sweep_timer.isActive()


def test_event_overlay_position_mode_persists_geometry(qtbot) -> None:
    from nparseplus.config.settings import WindowState

    saves = []
    state = WindowState()
    overlay = EventOverlayWindow(state=state, on_save=lambda: saves.append(1))
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    assert overlay.is_edit_mode()
    assert overlay.isVisible()
    overlay.setGeometry(100, 120, 800, 500)
    overlay.set_edit_mode(False)
    assert not overlay.is_edit_mode()
    assert state.geometry == (100, 120, 800, 500)
    assert saves == [1]
    # locked again: hides when idle
    assert not overlay.is_active()


def test_event_overlay_uses_persisted_geometry(qtbot) -> None:
    from nparseplus.config.settings import WindowState

    state = WindowState(geometry=(50, 60, 640, 400))
    overlay = EventOverlayWindow(state=state)
    qtbot.addWidget(overlay)
    geo = overlay.geometry()
    assert (geo.x(), geo.y(), geo.width(), geo.height()) == (50, 60, 640, 400)
