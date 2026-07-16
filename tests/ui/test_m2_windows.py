"""pytest-qt tests for the M2 windows (DPS meter, event overlay, mob info, console)."""

from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.dps import FightTracker
from nparseplus.core.events import DamageEvent, LineEvent, OverlayEvent, TimerBarEvent
from nparseplus.core.handlers.con import MobInfoState
from nparseplus.ui.consolewindow import ConsoleWindow
from nparseplus.ui.dpswindow import DpsMeterWindow
from nparseplus.ui.eventoverlay import EventOverlayWindow
from nparseplus.ui.mobinfo import MobInfoWindow

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
    window.handle_event(LineEvent(timestamp=T0, line="You begin casting Clarity.", line_number=1))
    window.handle_event(LineEvent(timestamp=T0, line="You gain experience!!", line_number=2))
    assert window.line_count() >= 2
    before = window.line_count()
    window.set_paused(True)
    window.handle_event(LineEvent(timestamp=T0, line="ignored while paused", line_number=3))
    assert window.line_count() == before


def test_event_overlay_configurable_duration(qtbot) -> None:
    overlay = EventOverlayWindow(clear_after_s=7.5)
    qtbot.addWidget(overlay)
    assert overlay._clear_timer.interval() == 7500
