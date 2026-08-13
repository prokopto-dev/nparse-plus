"""Settings that used to claim a restart and now apply on Apply.

Several issues meet here because the page copy is half the fix: overlay alert
duration + CH lane retention (#67), the sharing mode's "off" direction (#69),
the dump upload destination (#68) and the EQ install directory's spell
database (#70). All follow the ``on_dps_changed`` seam — the window mutates
settings and fires a callback the app wired to the live object.
"""

from datetime import datetime

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.player import ActivePlayer
from nparseplus.core.sharing import SharingCoordinator
from nparseplus.core.timers import TimersService
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.ui.eventoverlay import EventOverlayWindow
from nparseplus.ui.settingswindow import UnifiedSettingsWindow

pytestmark = pytest.mark.qt


class _NullSpeaker:
    def speak(self, *_a, **_k) -> None: ...
    def interrupt(self) -> None: ...


class _NullTimers:
    def add_timer(self, *_a, **_k) -> None: ...
    def cancel(self, *_a, **_k) -> None: ...


def _legacy() -> dict:
    return {
        "maps": {"line_width": 1, "grid_line_width": 1, "opacity": 80},
        "discord": {"opacity": 80, "bg_opacity": 25},
    }


def _window(qtbot, settings: Settings, **kwargs) -> UnifiedSettingsWindow:
    window = UnifiedSettingsWindow(
        settings,
        on_save=lambda: None,
        legacy_config=_legacy(),
        **kwargs,
    )
    qtbot.addWidget(window)
    return window


def _hints(window: UnifiedSettingsWindow, page: str) -> str:
    """Every label on one page, as one string — the page's visible copy."""
    from PySide6.QtWidgets import QLabel

    index = next(
        i for i in range(window._sidebar.count()) if window._sidebar.item(i).text() == page
    )
    widget = window._stack.widget(index)
    return "\n".join(label.text() for label in widget.findChildren(QLabel))


# --- the overlay's own timings (#67) --------------------------------------------


def test_apply_timings_moves_both_attributes_and_the_live_timer(qtbot) -> None:
    overlay = EventOverlayWindow(clear_after_s=4.0, ch_lane_retention_s=20.0)
    qtbot.addWidget(overlay)

    overlay.apply_timings(clear_after_s=9.0, ch_lane_retention_s=45.0)

    assert overlay._clear_after_ms == 9000
    assert overlay._clear_timer.interval() == 9000  # the running QTimer, not a copy
    assert overlay._ch_lane_retention_s == 45.0


def test_apply_timings_takes_one_setting_at_a_time(qtbot) -> None:
    overlay = EventOverlayWindow(clear_after_s=4.0, ch_lane_retention_s=20.0)
    qtbot.addWidget(overlay)
    overlay.apply_timings(ch_lane_retention_s=30.0)
    assert overlay._ch_lane_retention_s == 30.0
    assert overlay._clear_timer.interval() == 4000


def test_apply_timings_keeps_the_floor(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.apply_timings(clear_after_s=0.1, ch_lane_retention_s=-5.0)
    assert overlay._clear_after_ms == 1000  # same clamp as the constructor
    assert overlay._ch_lane_retention_s == 0.0


def test_a_running_alert_is_retimed(qtbot) -> None:
    """Qt restarts a running timer when the interval changes, which is what
    lets the change reach the alert already on screen."""
    from nparseplus.core.events import OverlayEvent

    overlay = EventOverlayWindow(clear_after_s=4.0)
    qtbot.addWidget(overlay)
    overlay.handle_event(OverlayEvent(timestamp=datetime.now(), text="Gorenaire — ENRAGED"))
    assert overlay._clear_timer.isActive()

    overlay.apply_timings(clear_after_s=20.0)

    assert overlay._clear_timer.isActive()
    assert overlay._clear_timer.interval() == 20000


def test_the_skin_path_does_not_touch_the_timings(qtbot) -> None:
    """apply_skin doubles as the skin picker's live PREVIEW: clicking a card
    must not restart the alert timers (#67)."""
    overlay = EventOverlayWindow(clear_after_s=6.0, ch_lane_retention_s=25.0)
    qtbot.addWidget(overlay)
    overlay.apply_skin(font_size=14, text_size=48, emphasis="pulse", shadow=False)
    assert overlay._clear_timer.interval() == 6000
    assert overlay._ch_lane_retention_s == 25.0


# --- the settings window's end of both seams -------------------------------------


def test_apply_fires_the_new_callbacks(qtbot) -> None:
    calls: list[str] = []
    window = _window(
        qtbot,
        Settings(),
        on_overlay_timing_changed=lambda: calls.append("timings"),
        on_sharing_changed=lambda: calls.append("sharing"),
    )
    window.apply()
    assert calls == ["timings", "sharing"]


def test_apply_reaches_the_overlay_and_the_engine_end_to_end(qtbot) -> None:
    """Stands in for create_app's wiring: Apply -> _apply_overlay_timings ->
    the trigger engine + the overlay window."""
    settings = Settings()
    overlay = EventOverlayWindow(
        clear_after_s=settings.general.overlay_text_seconds,
        ch_lane_retention_s=settings.general.ch_lane_retention_seconds,
    )
    qtbot.addWidget(overlay)
    engine = TriggerEngine(
        bus=EventBus(),
        player=ActivePlayer(),
        speaker=_NullSpeaker(),
        timers=_NullTimers(),
        display_text_seconds=settings.general.overlay_text_seconds,
    )

    def push() -> None:
        engine.display_text_seconds = settings.general.overlay_text_seconds
        overlay.apply_timings(
            clear_after_s=settings.general.overlay_text_seconds,
            ch_lane_retention_s=settings.general.ch_lane_retention_seconds,
        )

    window = _window(qtbot, settings, on_overlay_timing_changed=push)
    window._overlay_seconds.setValue(11.0)
    window._ch_retention.setValue(60.0)
    window.apply()

    assert settings.general.overlay_text_seconds == 11.0
    assert engine.display_text_seconds == 11.0
    assert overlay._clear_timer.interval() == 11000
    assert overlay._ch_lane_retention_s == 60.0


def test_apply_turns_sharing_off_end_to_end(qtbot) -> None:
    """Apply -> Backend.apply_sharing_mode -> SharingCoordinator.apply_mode."""

    class FakeClient:
        status = "connected"

        def __init__(self) -> None:
            self.stopped = 0

        def start(self) -> None: ...
        def stop(self) -> None:
            self.stopped += 1

        def set_server(self, server) -> None: ...
        def send_location(self, **kwargs) -> None: ...
        def send_dragon_roar(self, **kwargs) -> None: ...
        def send_waypoint(self, **kwargs) -> None: ...

    settings = Settings()
    settings.sharing.mode = "pigparse"
    client = FakeClient()
    coordinator = SharingCoordinator(
        bus=EventBus(),
        player=ActivePlayer(),
        settings=settings,
        timers=TimersService(),
        last_you_activity=lambda: None,
        client=client,
    )

    window = _window(qtbot, settings, on_sharing_changed=coordinator.apply_mode)
    window._sharing_mode.setCurrentText("off")
    window.apply()

    assert settings.sharing.mode == "off"
    assert client.stopped == 1
    assert coordinator.status == "off"


# --- the copy, which is half of each fix ------------------------------------------


def test_the_general_page_no_longer_claims_a_restart(qtbot) -> None:
    """TTS has live-swapped since 1.9 and the durations do now too, so the
    whole sentence went (#67)."""
    text = _hints(_window(qtbot, Settings()), "General")
    assert "EQ Logs directory" in text  # the scan really reached the page
    assert "restart" not in text.lower()


def test_the_sharing_page_says_what_is_true_of_each_direction(qtbot) -> None:
    text = _hints(_window(qtbot, Settings()), "Sharing").lower()
    assert "turning sharing off applies immediately" in text
    assert "restart" in text  # ...but turning it on still needs one


# --- the dump upload destination (#68) --------------------------------------------


def test_apply_fires_the_upload_callback_only_when_the_destination_moved(qtbot) -> None:
    calls: list[str] = []
    settings = Settings()
    window = _window(qtbot, settings, on_upload_target_changed=lambda: calls.append("upload"))

    window.apply()
    assert calls == []  # unchanged: nothing to build

    window._upload_target.setCurrentIndex(window._upload_target.findData("p99planner"))
    window.apply()
    assert settings.dumps.upload_target == "p99planner"
    assert calls == ["upload"]

    window.apply()
    assert calls == ["upload"]  # still p99planner


def test_the_upload_note_no_longer_claims_a_restart(qtbot) -> None:
    window = _window(qtbot, Settings())
    window._upload_target.setCurrentIndex(window._upload_target.findData("pigparse"))
    note = window._upload_note.text().lower()
    assert "restart" not in note
    assert "auto-import" in note  # the limitation that IS still true


# --- the EQ install directory's spell database (#70) ------------------------------


def test_apply_fires_the_install_dir_callback_only_when_it_moved(qtbot, tmp_path) -> None:
    calls: list[str] = []
    settings = Settings()
    window = _window(qtbot, settings, on_install_dir_changed=lambda: calls.append("install"))

    window.apply()
    assert calls == []

    window._install_dir.edit.setText(str(tmp_path))
    window.apply()
    assert settings.general.eq_install_dir == tmp_path
    assert calls == ["install"]

    window.apply()
    assert calls == ["install"]  # same directory, no reload


def test_apply_reaches_the_spell_book_end_to_end(qtbot, tmp_path) -> None:
    """Apply -> Backend.reload_spell_book -> the driver tick -> the very book
    the parse context holds. The first-run case: install directory set
    mid-session, spell data live for the next cast."""
    from pathlib import Path

    from nparseplus.composition import build_backend

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "spells_us.txt"
    clarity = next(
        line
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.split("^")[1:2] == ["Clarity"]
    )
    (tmp_path / "spells_us.txt").write_text(clarity + "\n", encoding="utf-8")

    settings = Settings()
    backend = build_backend(settings, speaker=_NullSpeaker())
    book = backend.spells
    assert len(book.spells) > 1

    window = _window(qtbot, settings, on_install_dir_changed=backend.reload_spell_book)
    window._install_dir.edit.setText(str(tmp_path))
    window.apply()

    # What the driver thread does: one tick starts the parse off-thread (it
    # costs ~1.1 s and must not sit on the log tail), a later one adopts it.
    assert backend.spell_reload is not None
    backend.spell_reload.tick(datetime.now())
    assert backend.spell_reload.wait()
    backend.spell_reload.tick(datetime.now())

    assert backend.spells is book  # the handlers' handle never moved
    assert [spell.name for spell in book.spells] == ["Clarity"]
