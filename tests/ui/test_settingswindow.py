"""UnifiedSettingsWindow — dual-config apply, windows grid, character pane."""

from pathlib import Path

import pytest

from datetime import datetime

from nparseplus.config.settings import PlayerInfo, Settings, WindowState, get_player
from nparseplus.core.enums import PlayerClass, Server
from nparseplus.core.events import AfterPlayerChangedEvent
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database
from nparseplus.ui.settingswindow import UnifiedSettingsWindow

pytestmark = pytest.mark.qt

ZONES = load_zone_database()


def _legacy() -> dict:
    return {
        "maps": {
            "line_width": 1,
            "grid_line_width": 1,
            "current_z_alpha": 100,
            "closest_z_alpha": 20,
            "other_z_alpha": 10,
            "opacity": 80,
            "always_on_top": True,
            "clickthrough": False,
        },
        "discord": {
            "opacity": 80,
            "bg_opacity": 25,
            "always_on_top": True,
            "clickthrough": False,
        },
    }


class FakeHandle:
    def __init__(self) -> None:
        self.opacities: list[float] = []
        self.applied = 0

    def setWindowOpacity(self, value: float) -> None:
        self.opacities.append(value)

    def apply_window_state(self) -> None:
        self.applied += 1


def _window(qtbot, settings=None, legacy=None, **kwargs) -> UnifiedSettingsWindow:
    window = UnifiedSettingsWindow(
        settings if settings is not None else Settings(),
        on_save=kwargs.pop("on_save", lambda: None),
        legacy_config=legacy if legacy is not None else _legacy(),
        zones=ZONES,
        **kwargs,
    )
    qtbot.addWidget(window)
    return window


def test_window_title() -> None:
    # The whole point of the consolidation: one window, the right name.
    assert (
        UnifiedSettingsWindow(
            Settings(), on_save=lambda: None, legacy_config=_legacy()
        ).windowTitle()
        == "nParse+ Settings"
    )


def test_apply_dual_writes_and_notifies_once(qtbot, tmp_path: Path) -> None:
    settings = Settings()
    legacy = _legacy()
    calls = {"save": 0, "legacy_save": 0, "notify": 0, "repaint": 0}
    dir_changes: list[Path] = []

    def count(key):
        def _inner():
            calls[key] += 1

        return _inner

    window = _window(
        qtbot,
        settings,
        legacy,
        on_save=count("save"),
        on_legacy_save=count("legacy_save"),
        notify_legacy=count("notify"),
        repaint_maps=count("repaint"),
        on_log_dir_changed=dir_changes.append,
    )
    window._log_dir.edit.setText(str(tmp_path))
    window._font_size.setValue(15)
    window._sharing_mode.setCurrentText("off")
    window._you_only.setChecked(True)
    window._best_guess.setChecked(False)
    window._maps_line_width.setValue(3)
    window._z_closest.setValue(42)
    window.apply()

    # Pydantic side.
    assert settings.general.eq_log_dir == tmp_path
    assert settings.general.font_size == 15
    assert settings.sharing.mode == "off"
    assert settings.spellwindow.you_only_spells is True
    assert settings.spellwindow.best_guess_spells is False
    # Legacy side.
    assert legacy["maps"]["line_width"] == 3
    assert legacy["maps"]["closest_z_alpha"] == 42
    # Bridge callables: exactly once each.
    assert calls == {"save": 1, "legacy_save": 1, "notify": 1, "repaint": 1}
    assert dir_changes == [tmp_path]


def test_windows_grid_writes_both_families_and_applies(qtbot) -> None:
    settings = Settings()
    settings.windows["dps"] = WindowState(opacity=1.0, always_on_top=True)
    legacy = _legacy()
    maps_handle = FakeHandle()
    dps_handle = FakeHandle()
    window = _window(
        qtbot,
        settings,
        legacy,
        window_handles={"maps": maps_handle, "dps": dps_handle},
    )

    maps_row = window._legacy_rows["maps"]
    maps_row.opacity.setValue(55)  # live preview on the handle
    maps_row.on_top.setChecked(False)
    assert maps_handle.opacities[-1] == pytest.approx(0.55)

    dps_row = window._new_rows["dps"]
    dps_row.opacity.setValue(40)
    dps_row.on_top.setChecked(False)
    assert dps_handle.opacities[-1] == pytest.approx(0.40)

    window.apply()
    assert legacy["maps"]["opacity"] == 55
    assert legacy["maps"]["always_on_top"] is False
    state = settings.windows["dps"]
    assert state.opacity == pytest.approx(0.40)
    assert state.always_on_top is False
    assert dps_handle.applied == 1  # apply_window_state called on Apply


def test_character_pane_mutates_in_place_and_pushes_active(qtbot) -> None:
    settings = Settings()
    profile = PlayerInfo(name="Xantik", server="green")
    settings.players.append(profile)
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    window = _window(qtbot, settings, backend_player=player)

    # Active character preselected.
    assert window._char_combo.currentIndex() == 0
    window._char_class.setCurrentText("Druid")
    window._char_level.setValue(54)
    window._char_zone.setCurrentText("greater faydark")
    window._char_track.setValue(120)
    window._char_sharing.setCurrentText("guild")
    window._char_share_timers.setChecked(False)
    window._class_filter_boxes[PlayerClass.WIZARD].setChecked(False)
    window.apply()

    assert settings.players[0] is profile  # mutated in place, same object
    assert profile.player_class == int(PlayerClass.DRUID)
    assert profile.level == 54
    assert profile.zone == "gfaydark"  # long name stored as short key
    assert profile.tracking_skill == 120
    assert profile.map_location_sharing == "guild"
    assert profile.share_timers is False
    assert profile.show_spells_for_classes is not None
    assert int(PlayerClass.WIZARD) not in profile.show_spells_for_classes
    # Pushed into the live backend player (it IS the active character).
    assert player.player_class is PlayerClass.DRUID
    assert player.level == 54
    assert player.zone == "gfaydark"
    assert player.tracking_skill == 120


def test_track_skill_enabled_only_for_trackable_classes(qtbot) -> None:
    settings = Settings()
    settings.players.append(PlayerInfo(name="Xantik", server="green"))
    window = _window(qtbot, settings)
    window._char_combo.setCurrentIndex(0)
    window._char_class.setCurrentText("Warrior")
    assert not window._char_track.isEnabled()
    assert window._char_track.value() == 0  # auto-cleared like PlayerInfo.cs
    window._char_class.setCurrentText("Ranger")
    assert window._char_track.isEnabled()


def test_character_combo_refreshes_after_lazy_profile_creation(qtbot) -> None:
    # The real-life bug: the window is built BEFORE the driver attaches the
    # log and creates the profile, leaving the combo empty forever.
    settings = Settings()
    player = ActivePlayer()
    window = _window(qtbot, settings, backend_player=player)
    assert window._char_combo.count() == 0
    assert not window._char_class.isEnabled()

    # Driver thread attaches the log: profile created, character-change event.
    player.reset_for("Xantik", Server.GREEN)
    get_player(settings, "Xantik", "green")
    window.handle_backend_event(AfterPlayerChangedEvent(timestamp=datetime.now()))

    assert [window._char_combo.itemText(i) for i in range(window._char_combo.count())] == [
        "Xantik (green)"
    ]
    assert window._char_combo.currentIndex() == 0
    assert window._char_class.isEnabled()


def test_character_combo_refreshes_on_show(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)
    assert window._char_combo.count() == 0
    settings.players.append(PlayerInfo(name="Xantik", server="green"))
    window.show()
    assert window._char_combo.count() == 1
    window.hide()


def test_refresh_preserves_unsaved_edits_for_same_character(qtbot) -> None:
    settings = Settings()
    settings.players.append(PlayerInfo(name="Xantik", server="green"))
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    window = _window(qtbot, settings, backend_player=player)
    window._char_level.setValue(37)  # unsaved edit

    # A second profile appears (e.g. loaded elsewhere) but the active
    # character did not change: selection and edits must survive.
    settings.players.append(PlayerInfo(name="Beeta", server="blue"))
    window.refresh_characters()

    assert window._char_combo.count() == 2
    assert window._char_combo.currentText() == "Xantik (green)"
    assert window._char_level.value() == 37


def test_refresh_tracks_character_switch(qtbot) -> None:
    settings = Settings()
    settings.players.append(PlayerInfo(name="Xantik", server="green", level=50))
    settings.players.append(PlayerInfo(name="Beeta", server="blue", level=12))
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    window = _window(qtbot, settings, backend_player=player)
    assert window._char_combo.currentText() == "Xantik (green)"

    player.reset_for("Beeta", Server.BLUE)
    window.handle_backend_event(AfterPlayerChangedEvent(timestamp=datetime.now()))

    assert window._char_combo.currentText() == "Beeta (blue)"
    assert window._char_level.value() == 12


def test_all_classes_checked_round_trips_to_none(qtbot) -> None:
    settings = Settings()
    settings.players.append(PlayerInfo(name="Xantik", server="green", show_spells_for_classes=None))
    window = _window(qtbot, settings)
    window._char_combo.setCurrentIndex(0)
    # None loads as all-checked.
    assert all(box.isChecked() for box in window._class_filter_boxes.values())
    window.apply()
    assert settings.players[0].show_spells_for_classes is None
