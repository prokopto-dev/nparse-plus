"""UnifiedSettingsWindow — dual-config apply, windows grid, character pane."""

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QScrollArea

from nparseplus.config.settings import PlayerInfo, Settings, WindowState, get_player
from nparseplus.core.enums import PlayerClass, Server
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    ClassDetectedEvent,
    WhoPlayer,
    WhoPlayerEvent,
    YouZonedEvent,
)
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database
from nparseplus.helpers.config import PAN_CTRL_DRAG, PAN_DRAG
from nparseplus.ui.settingswindow import (
    MIN_SIZE,
    PLUGIN_WINDOWS_SECTION,
    UnifiedSettingsWindow,
    elide,
)

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


def test_window_title(qtbot) -> None:  # qtbot: needs a QApplication to exist
    # The whole point of the consolidation: one window, the right name.
    assert (
        UnifiedSettingsWindow(
            Settings(), on_save=lambda: None, legacy_config=_legacy()
        ).windowTitle()
        == "nParse+ Settings"
    )


def test_font_size_control_lives_on_appearance_page(qtbot) -> None:
    window = _window(qtbot)

    general_labels = {label.text() for label in window._stack.widget(0).findChildren(QLabel)}
    appearance_labels = {label.text() for label in window._stack.widget(1).findChildren(QLabel)}

    assert "UI / overlay font size" not in general_labels
    assert "UI / overlay font size" in appearance_labels
    assert "Alert headline size" in appearance_labels


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
    window._show_boats.setChecked(False)
    window._show_mob_timers.setChecked(False)
    window._show_roll_timers.setChecked(False)
    window._show_custom_timers.setChecked(False)
    window._bar_fade.setChecked(False)
    window._maps_line_width.setValue(3)
    window._z_closest.setValue(42)
    window._z_fade_min.setValue(35)
    window._z_fade_fallback.setValue(25)
    window.apply()

    # Pydantic side.
    assert settings.general.eq_log_dir == tmp_path
    assert settings.general.font_size == 15
    assert settings.sharing.mode == "off"
    assert settings.spellwindow.you_only_spells is True
    assert settings.spellwindow.best_guess_spells is False
    assert settings.spellwindow.show_boats is False
    assert settings.spellwindow.show_mob_timers is False
    assert settings.spellwindow.show_roll_timers is False
    assert settings.spellwindow.show_custom_timers is False
    assert settings.spellwindow.bar_fade_to_red is False
    # Legacy side.
    assert legacy["maps"]["line_width"] == 3
    assert legacy["maps"]["closest_z_alpha"] == 42
    assert legacy["maps"]["z_fade_min_opacity"] == 35
    assert legacy["maps"]["z_fade_fallback_height"] == 25
    # Bridge callables: exactly once each.
    assert calls == {"save": 1, "legacy_save": 1, "notify": 1, "repaint": 1}
    assert dir_changes == [tmp_path]


def test_pan_mode_picker_round_trips_the_legacy_key(qtbot) -> None:
    """A document written before the setting existed shows the default, not an
    empty picker — and the map's gesture was Ctrl+drag, so the value the picker
    writes is what the canvas reads at press time."""
    legacy = _legacy()
    assert "pan_mode" not in legacy["maps"]
    window = _window(qtbot, legacy=legacy)
    assert window._maps_pan_mode.currentData() == PAN_DRAG

    window._maps_pan_mode.setCurrentIndex(window._maps_pan_mode.findData(PAN_CTRL_DRAG))
    window.apply()
    assert legacy["maps"]["pan_mode"] == PAN_CTRL_DRAG

    reopened = _window(qtbot, legacy=legacy)
    assert reopened._maps_pan_mode.currentData() == PAN_CTRL_DRAG


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
    # Legacy handles get the same direct call — the map must not depend on
    # the config_updated signal that fires later in apply().
    assert maps_handle.applied == 1


def test_overlay_rows_offer_clickthrough_and_tool_windows_do_not(qtbot) -> None:
    # The rule (#167): a HUD drawn over the game may be made click-through; a
    # window you drive with the mouse may not. A config surface you cannot
    # click is not a feature — the Console's one affordance is a right-click,
    # and the editors and the dump library are forms, trees and buttons.
    window = _window(qtbot, Settings())

    for key in ("spells", "dps", "mobinfo"):
        assert window._new_rows[key].clickthrough is not None, key
    for key in ("console", "triggereditor", "macroeditor", "dumps"):
        assert window._new_rows[key].clickthrough is None, key


def test_new_window_row_round_trips_clickthrough(qtbot) -> None:
    settings = Settings()
    handle = FakeHandle()
    window = _window(qtbot, settings, window_handles={"spells": handle})

    window._new_rows["spells"].clickthrough.setChecked(True)
    window.apply()

    assert settings.windows["spells"].clickthrough is True
    assert handle.applied == 1

    window._new_rows["spells"].clickthrough.setChecked(False)
    window.apply()
    assert settings.windows["spells"].clickthrough is False


def test_migrated_clickthrough_shows_ticked(qtbot) -> None:
    # The lockout this closes (#167): config/migrate.py carries nparse's
    # `spells.clickthrough` into windows["spells"], SpellTimerWindow honours
    # it, and before this row existed there was no way in the app to reach it
    # — the tray only toggles visibility, and Reset Window Positions and the
    # layout presets write geometry. The box must open already ticked, or the
    # user has no way to tell that is what happened to their Timers window.
    settings = Settings()
    settings.windows["spells"] = WindowState(clickthrough=True)
    window = _window(qtbot, settings)

    assert window._new_rows["spells"].clickthrough.isChecked() is True


def test_clickthrough_boxes_carry_the_warning_tooltip(qtbot) -> None:
    # Deliberately a tooltip and not a confirmation dialog: the failure mode
    # is puzzlement later, not a lockout, and a modal on one checkbox in a
    # grid of checkboxes trains people to click past it.
    window = _window(qtbot, Settings())
    tip = window._new_rows["spells"].clickthrough.toolTip()
    assert "pass straight through" in tip
    assert "right-click" in tip  # names what stops working, not just the flag


def test_settings_window_is_never_clickthrough(qtbot) -> None:
    # The escape hatch has to stay clickable: this page is the only thing in
    # the app that turns click-through back off. The grid never offers the box
    # for it, so this only guards a hand-edited settings.json — which is
    # exactly the case that would otherwise leave no way back in.
    settings = Settings()
    settings.windows["settings"] = WindowState(clickthrough=True)
    window = _window(qtbot, settings)

    assert not (window.windowFlags() & Qt.WindowType.WindowTransparentForInput)

    # ...and it survives a re-apply, which is what a Save runs through.
    window.apply_window_state()
    assert not (window.windowFlags() & Qt.WindowType.WindowTransparentForInput)


def test_settings_window_has_no_grid_row(qtbot) -> None:
    window = _window(qtbot, Settings())
    assert "settings" not in window._new_rows


def _grid_label(window: UnifiedSettingsWindow, text: str) -> tuple[QLabel, int]:
    """The Windows-grid label whose text is `text`, and its grid row index."""
    grid = window._windows_grid
    for i in range(grid.count()):
        widget = grid.itemAt(i).widget()
        if isinstance(widget, QLabel) and widget.text() == text:
            return widget, grid.getItemPosition(i)[0]
    raise AssertionError(f"no {text!r} label in the Windows grid")


def _grid_row_of(window: UnifiedSettingsWindow, text: str) -> int:
    return _grid_label(window, text)[1]


def test_plugin_window_row_writes_state_and_applies(qtbot) -> None:
    settings = Settings()
    handle = FakeHandle()
    key = "plugin.demo.timer"
    window = _window(
        qtbot,
        settings,
        plugin_windows=[("Demo Plugin — Timer", key, handle)],
    )

    row = window._plugin_rows[key]
    row.opacity.setValue(45)
    row.on_top.setChecked(False)
    # The handle was never in window_handles — the row carries it directly.
    assert handle.opacities[-1] == pytest.approx(0.45)

    window.apply()
    assert settings.windows[key].opacity == pytest.approx(0.45)
    assert settings.windows[key].always_on_top is False
    assert handle.applied == 1


def test_plugin_row_reads_persisted_state(qtbot) -> None:
    # The point of the feature: opacity a plugin window already saved must be
    # what the row shows, not the WindowState default.
    settings = Settings()
    key = "plugin.demo.timer"
    settings.windows[key] = WindowState(opacity=0.6, always_on_top=False)
    window = _window(qtbot, settings, plugin_windows=[("Demo — Timer", key, FakeHandle())])

    row = window._plugin_rows[key]
    assert row.opacity.value() == 60
    assert row.on_top.isChecked() is False


def test_no_plugin_section_without_plugin_windows(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)

    assert window._plugin_rows == {}
    assert not [key for key in settings.windows if key.startswith("plugin.")]
    with pytest.raises(AssertionError):
        _grid_row_of(window, f"<b>{PLUGIN_WINDOWS_SECTION}</b>")


def test_apply_leaves_absent_plugin_window_state_alone(qtbot) -> None:
    # A disabled or uninstalled add-on gets no row; its saved state must
    # survive an unrelated Apply so re-enabling restores what the user set.
    settings = Settings()
    settings.windows["plugin.gone.main"] = WindowState(opacity=0.42, always_on_top=False)
    window = _window(qtbot, settings)

    window.apply()
    state = settings.windows["plugin.gone.main"]
    assert state.opacity == pytest.approx(0.42)
    assert state.always_on_top is False


def test_plugin_section_renders_below_discord_extras(qtbot) -> None:
    # The Discord extras reuse the running row index; appending the plugin
    # block without advancing it put the header above them.
    window = _window(
        qtbot,
        Settings(),
        plugin_windows=[("Demo — Timer", "plugin.demo.timer", FakeHandle())],
    )

    header = _grid_row_of(window, f"<b>{PLUGIN_WINDOWS_SECTION}</b>")
    assert header > _grid_row_of(window, "Discord background")
    assert _grid_row_of(window, "Demo — Timer") > header


def test_plugin_rows_offer_clickthrough(qtbot) -> None:
    # Reversed in #167. The box used to be withheld here because "unlike Maps
    # and Discord an add-on window has no menu bar to reach for once it stops
    # responding" — but WindowTransparentForInput is a *window* flag, so no
    # click reaches a child widget either and the legacy hover bar is exactly
    # as unreachable. The escape hatch is this page, which is host-owned, so a
    # plugin window is no less recoverable than the Timers window.
    settings = Settings()
    key = "plugin.demo.timer"
    handle = FakeHandle()
    window = _window(qtbot, settings, plugin_windows=[("Demo — Timer", key, handle)])

    row = window._plugin_rows[key]
    assert row.clickthrough is not None
    row.clickthrough.setChecked(True)
    window.apply()

    assert settings.windows[key].clickthrough is True
    assert handle.applied == 1


def test_plugin_row_reads_persisted_clickthrough(qtbot) -> None:
    settings = Settings()
    key = "plugin.demo.timer"
    settings.windows[key] = WindowState(clickthrough=True)
    window = _window(qtbot, settings, plugin_windows=[("Demo — Timer", key, FakeHandle())])

    assert window._plugin_rows[key].clickthrough.isChecked() is True


def test_plugin_label_is_not_interpreted_as_markup(qtbot) -> None:
    # Labels are plugin-supplied; QLabel would otherwise render the tags.
    label = "<b>Bold</b> & Co — Timer"
    window = _window(
        qtbot,
        Settings(),
        plugin_windows=[(label, "plugin.demo.timer", FakeHandle())],
    )
    widget, _row = _grid_label(window, label)
    assert widget.textFormat() == Qt.TextFormat.PlainText


def test_long_plugin_label_is_elided_with_a_tooltip(qtbot) -> None:
    long = "P" * 40 + " — " + "W" * 40
    window = _window(qtbot, Settings(), plugin_windows=[(long, "plugin.demo.timer", FakeHandle())])

    row = window._plugin_rows["plugin.demo.timer"]
    assert row.label.endswith("…") and len(row.label) == 60
    assert row.tooltip == long  # the full name stays reachable


def test_short_plugin_label_gets_no_tooltip(qtbot) -> None:
    # A tooltip repeating the visible label is noise.
    window = _window(
        qtbot, Settings(), plugin_windows=[("Demo — Timer", "plugin.demo.timer", FakeHandle())]
    )
    assert window._plugin_rows["plugin.demo.timer"].tooltip is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("short", "short"), ("x" * 60, "x" * 60), ("y" * 61, "y" * 59 + "…")],
)
def test_elide(text: str, expected: str) -> None:
    assert elide(text) == expected


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


def test_live_who_and_zone_events_refresh_backend_fields_only(qtbot) -> None:
    profile = PlayerInfo(name="Xantik", server="green", tracking_skill=120)
    settings = Settings(players=[profile])
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    window = _window(qtbot, settings, backend_player=player)
    window._char_sharing.setCurrentText("off")  # unrelated unsaved edit

    profile.player_class = int(PlayerClass.DRUID)
    profile.level = 54
    window.handle_backend_event(
        WhoPlayerEvent(
            timestamp=datetime.now(),
            player=WhoPlayer(name="Xantik", player_class=PlayerClass.DRUID, level=54),
        )
    )
    assert window._char_class.currentText() == "Druid"
    assert window._char_level.value() == 54
    assert window._char_track.value() == 120
    assert window._char_track.isEnabled()
    assert window._char_sharing.currentText() == "off"

    profile.zone = "gfaydark"
    window.handle_backend_event(
        YouZonedEvent(timestamp=datetime.now(), long_name="greater faydark", short_name="gfaydark")
    )
    assert window._char_zone.currentText() == "greater faydark"
    assert window._char_sharing.currentText() == "off"


def test_who_block_end_to_end_updates_character_fields(qtbot) -> None:
    """Real parsers + PlayerProfileHandler + the window: a /who block must
    land class, level, AND zone in the character page (regression: users saw
    stale fields after /who)."""
    from nparseplus.core.bus import EventBus
    from nparseplus.core.parsers.base import ParseContext
    from nparseplus.core.parsers.who import PlayerWhoLogParse
    from nparseplus.core.parsers.you_zoned import YouZonedParser
    from nparseplus.core.pipeline import LogPipeline

    settings = Settings()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    profile = get_player(settings, "Xantik", "green")

    bus = EventBus()
    from nparseplus.core.handlers.player_profile import PlayerProfileHandler

    PlayerProfileHandler(bus, player, settings)
    # Registry order: YouZonedParser runs before PlayerWhoLogParse.
    pipeline = LogPipeline(
        [YouZonedParser(), PlayerWhoLogParse()],
        ParseContext(bus=bus, player=player, zones=ZONES, settings=settings),
    )

    window = _window(qtbot, settings, backend_player=player)
    bus.subscribe_all(window.handle_backend_event)  # what the Qt bridge does

    stamp = "[Wed Jul 15 12:00:00 2026]"
    for message in (
        "Players on EverQuest:",
        "---------------------------",
        "[54 Wanderer] Xantik (Wood Elf) <Sanctuary>",
        "There are 4 players in Greater Faydark.",
    ):
        pipeline.process(f"{stamp} {message}")

    assert profile.player_class == int(PlayerClass.DRUID)
    assert profile.level == 54
    assert profile.zone == "gfaydark"
    assert window._char_class.currentText() == "Druid"
    assert window._char_level.value() == 54
    assert window._char_zone.currentText() == "greater faydark"


def test_reopening_window_reloads_backend_mutated_fields(qtbot) -> None:
    # Regression: refresh_characters early-returns when the profile list and
    # active character are unchanged, so reopening showed stale fields.
    profile = PlayerInfo(name="Xantik", server="green", level=49)
    settings = Settings(players=[profile])
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    window = _window(qtbot, settings, backend_player=player)
    window.show()
    assert window._char_level.value() == 49
    window.hide()

    profile.level = 50  # ding while the window is hidden
    profile.zone = "gfaydark"
    window.show()
    assert window._char_level.value() == 50
    assert window._char_zone.currentText() == "greater faydark"
    window.hide()


def test_stale_active_character_heals_on_live_event(qtbot) -> None:
    # The profile is created AFTER the window was built (log attaches late)
    # and no AfterPlayerChangedEvent reached us: a live /who event must still
    # re-sync the combo instead of being silently dropped.
    settings = Settings()
    player = ActivePlayer()
    window = _window(qtbot, settings, backend_player=player)
    assert window._char_combo.count() == 0

    player.reset_for("Xantik", Server.GREEN)
    profile = get_player(settings, "Xantik", "green")
    profile.player_class = int(PlayerClass.DRUID)
    profile.level = 54
    window.handle_backend_event(
        WhoPlayerEvent(
            timestamp=datetime.now(),
            player=WhoPlayer(name="Xantik", player_class=PlayerClass.DRUID, level=54),
        )
    )
    assert window._char_combo.currentText() == "Xantik (green)"
    assert window._char_class.currentText() == "Druid"
    assert window._char_level.value() == 54


@pytest.mark.parametrize("stored_class", [int(PlayerClass.OTHER), 99])
def test_unknown_stored_class_loads_as_unknown_without_crash(qtbot, stored_class) -> None:
    # Regression: OTHER (14, the castable-by-everyone spell fixup) or junk in
    # settings.json made PLAYER_CLASSES.index raise ValueError inside a Qt
    # slot, which killed the whole app.
    settings = Settings()
    settings.players.append(PlayerInfo(name="Xantik", server="green", player_class=stored_class))
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    window = _window(qtbot, settings, backend_player=player)
    assert window._char_class.currentIndex() == 0  # "(unknown)"


def test_live_class_event_with_other_class_does_not_crash(qtbot) -> None:
    # Same regression via the live path: settings window open, an item clicky
    # is cast, ClassDetectedEvent triggers the field refresh.
    profile = PlayerInfo(name="Xantik", server="green")
    settings = Settings(players=[profile])
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    window = _window(qtbot, settings, backend_player=player)

    profile.player_class = int(PlayerClass.OTHER)  # e.g. pre-fix polluted file
    window.handle_backend_event(
        ClassDetectedEvent(timestamp=datetime.now(), player_class=PlayerClass.OTHER)
    )
    assert window._char_class.currentIndex() == 0


def test_friends_page_load_and_push_round_trip(qtbot, tmp_path: Path) -> None:
    (tmp_path / "Xantik_P1999Green.ini").write_text("[Friends]\nFriend0=Alice\n")
    (tmp_path / "Beeta_P1999Green.ini").write_text("[Friends]\nFriend0=Bob\n")
    window = _window(qtbot)
    window._install_dir.edit.setText(str(tmp_path))
    window._friends_server.setCurrentText("P1999Green")

    window._load_friends()
    assert window._friends_text.toPlainText() == "Alice\nBob"

    window._friends_text.setPlainText("Alice\nBob\nCara")
    window._push_friends()
    for name in ("Xantik", "Beeta"):
        text = (tmp_path / f"{name}_P1999Green.ini").read_text()
        assert "Friend2=Cara" in text
    assert (tmp_path / "friends_backup" / "Xantik_P1999Green.ini").exists()
    assert (
        "3 friends" in window._friends_status.text() or "Pushed 3" in window._friends_status.text()
    )


def test_discord_login_flow_saves_account(qtbot) -> None:
    from nparseplus.net.discordauth import DiscordAuthResult

    settings = Settings()
    saves = {"count": 0}

    def bump() -> None:
        saves["count"] += 1

    window = _window(
        qtbot,
        settings,
        on_save=bump,
        discord_login_fn=lambda: DiscordAuthResult(
            username="Pig", discord_id="123", api_token="tok"
        ),
    )
    assert "Not logged in" in window._account_status.text()
    assert not window._account_logout.isEnabled()

    with qtbot.waitSignal(window._discord_auth_done, timeout=5000):
        window._start_discord_login()
    # The slot is queued; wait for it to have run on the GUI thread.
    qtbot.waitUntil(lambda: settings.pigparse_account.api_token == "tok", timeout=5000)

    account = settings.pigparse_account
    assert (account.username, account.discord_id, account.api_token) == ("Pig", "123", "tok")
    assert saves["count"] == 1
    assert "Logged in as Pig" in window._account_status.text()
    assert window._account_logout.isEnabled()

    window._discord_logout()
    assert settings.pigparse_account.api_token == ""
    assert "Not logged in" in window._account_status.text()


def test_discord_login_failure_reenables_button(qtbot) -> None:
    window = _window(qtbot, discord_login_fn=lambda: None)
    with qtbot.waitSignal(window._discord_auth_done, timeout=5000):
        window._start_discord_login()
    qtbot.waitUntil(lambda: "failed or timed out" in window._account_status.text(), timeout=5000)
    assert window._account_login.isEnabled()


def test_inventory_upload_target_applies(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)
    assert settings.dumps.upload_target == "off"

    window._upload_target.setCurrentIndex(window._upload_target.findData("p99planner"))
    window.apply()
    assert settings.dumps.upload_target == "p99planner"

    window._upload_target.setCurrentIndex(window._upload_target.findData("pigparse"))
    window.apply()
    assert settings.dumps.upload_target == "pigparse"


def test_upload_note_describes_the_chosen_destination(qtbot) -> None:
    """The two destinations differ in what they need from the user, so the
    page has to say which is which."""
    window = _window(qtbot, Settings())

    window._upload_target.setCurrentIndex(window._upload_target.findData("off"))
    assert "stay on this machine" in window._upload_note.text()

    window._upload_target.setCurrentIndex(window._upload_target.findData("pigparse"))
    assert "Discord login" in window._upload_note.text()

    window._upload_target.setCurrentIndex(window._upload_target.findData("p99planner"))
    note = window._upload_note.text()
    assert "No account or login" in note
    assert "approve" in note


def test_legacy_inventory_upload_bool_migrates_to_the_target() -> None:
    """A settings.json from before the dropdown must not silently stop
    uploading."""
    settings = Settings.model_validate(
        {"pigparse_account": {"api_token": "tok", "inventory_upload": True}}
    )
    assert settings.dumps.upload_target == "pigparse"
    assert settings.pigparse_account.inventory_upload is False  # folded and cleared

    # An explicit newer choice wins over the stale legacy bool.
    switched = Settings.model_validate(
        {
            "pigparse_account": {"inventory_upload": True},
            "dumps": {"upload_target": "p99planner"},
        }
    )
    assert switched.dumps.upload_target == "p99planner"


def test_all_classes_checked_round_trips_to_none(qtbot) -> None:
    settings = Settings()
    settings.players.append(PlayerInfo(name="Xantik", server="green", show_spells_for_classes=None))
    window = _window(qtbot, settings)
    window._char_combo.setCurrentIndex(0)
    # None loads as all-checked.
    assert all(box.isChecked() for box in window._class_filter_boxes.values())
    window.apply()
    assert settings.players[0].show_spells_for_classes is None


# -- TTS voice picker (id in userData, live swap on apply) ---------------------


def _patch_voices(monkeypatch, voices) -> None:
    from nparseplus.ui import settingswindow

    monkeypatch.setattr(settingswindow, "list_voices", lambda: voices)


def test_voice_combo_lists_voices_by_id(qtbot, monkeypatch) -> None:
    from nparseplus.audio.tts import VoiceInfo

    _patch_voices(
        monkeypatch,
        [
            VoiceInfo(id="say:Alex", label="Alex", engine="say"),
            VoiceInfo(id="winrt:Zira Desktop", label="Zira", engine="winrt"),
        ],
    )
    window = _window(qtbot)
    # Index 0 is the empty-id system default; enumerated voices follow, label
    # shown but id stored in userData.
    assert window._voice.itemData(0) == ""
    assert window._voice.itemText(1) == "Alex"
    assert window._voice.itemData(1) == "say:Alex"
    assert window._voice.itemText(2) == "Zira"
    assert window._voice.itemData(2) == "winrt:Zira Desktop"


def test_voice_combo_restores_saved_id_not_label(qtbot, monkeypatch) -> None:
    from nparseplus.audio.tts import VoiceInfo

    _patch_voices(
        monkeypatch,
        [VoiceInfo(id="say:Alex", label="Alex"), VoiceInfo(id="winrt:Zira Desktop", label="Zira")],
    )
    settings = Settings()
    settings.general.tts_voice = "winrt:Zira Desktop"
    window = _window(qtbot, settings)
    assert window._voice.currentData() == "winrt:Zira Desktop"
    assert window._voice.currentText() == "Zira"


def test_voice_combo_readds_missing_saved_id(qtbot, monkeypatch) -> None:
    _patch_voices(monkeypatch, [])  # nothing enumerable (e.g. headless platform)
    settings = Settings()
    settings.general.tts_voice = "say:Vanished"
    window = _window(qtbot, settings)
    assert window._voice.currentData() == "say:Vanished"


def test_test_voice_uses_id_and_apply_persists_id(qtbot, monkeypatch) -> None:
    from nparseplus.audio.tts import VoiceInfo
    from nparseplus.ui import settingswindow

    _patch_voices(monkeypatch, [VoiceInfo(id="say:Alex", label="Alex")])
    used: list[tuple[str, float]] = []

    class _FakeSpeaker:
        def speak(self, text: str) -> None:
            return

    def _fake_default_speaker(voice="", volume=1.0):
        used.append((voice, volume))
        return _FakeSpeaker()

    monkeypatch.setattr(settingswindow, "default_speaker", _fake_default_speaker)
    settings = Settings()
    window = _window(qtbot, settings)
    window._voice.setCurrentIndex(1)  # the Alex row
    window._test_voice()
    assert used[-1][0] == "say:Alex"  # id passed to the speaker, not the label
    window.apply()
    assert settings.general.tts_voice == "say:Alex"


def test_apply_default_voice_stores_none(qtbot, monkeypatch) -> None:
    from nparseplus.audio.tts import VoiceInfo

    _patch_voices(monkeypatch, [VoiceInfo(id="say:Alex", label="Alex")])
    settings = Settings()
    settings.general.tts_voice = "say:Alex"
    window = _window(qtbot, settings)
    window._voice.setCurrentIndex(0)  # (system default)
    window.apply()
    assert settings.general.tts_voice is None


def test_apply_swaps_speaker_only_when_audio_changes(qtbot, monkeypatch) -> None:
    from nparseplus.audio.tts import VoiceInfo

    _patch_voices(monkeypatch, [VoiceInfo(id="say:Alex", label="Alex")])
    swaps: list[None] = []
    settings = Settings()
    window = _window(qtbot, settings, on_audio_changed=lambda: swaps.append(None))
    # Nothing touched -> no swap (avoids churning the speaker on every Apply).
    window.apply()
    assert swaps == []
    # Voice change -> one swap.
    window._voice.setCurrentIndex(1)
    window.apply()
    assert swaps == [None]
    # Volume change (voice steady) -> another swap.
    window._volume.setValue(50)
    window.apply()
    assert len(swaps) == 2


# -- version / update indicator ------------------------------------------------


def test_version_indicator_shows_current_version(qtbot) -> None:
    import nparseplus

    window = _window(qtbot)
    assert nparseplus.__version__ in window._version_label.text()


def test_update_badge_up_to_date(qtbot) -> None:
    window = _window(qtbot)
    window._on_update_status_ready(None)  # None => up to date
    assert "Up to date" in window._update_badge.text()


def test_update_badge_update_available(qtbot) -> None:
    import types

    window = _window(qtbot)
    window._on_update_status_ready(types.SimpleNamespace(version="9.9.9"))
    assert "9.9.9" in window._update_badge.text()


def test_check_now_runs_updater_and_updates_badge(qtbot, monkeypatch) -> None:
    import nparseplus.updater as updater_mod

    monkeypatch.setattr(updater_mod, "check_for_update", lambda: None)
    window = _window(qtbot)
    with qtbot.waitSignal(window._update_status_ready, timeout=3000):
        window._check_for_update_async()
    assert "Up to date" in window._update_badge.text()
    assert window._update_check_button.isEnabled()


def test_ch_cadence_patterns_apply(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)
    window._ch_cadence.setChecked(True)
    window._ch_cadence_patterns.setPlainText("cadence (\\d+)\n\n  chain at (\\d+)  ")
    window.apply()
    # Blank lines dropped, surrounding whitespace stripped.
    assert settings.general.ch_cadence_indicator is True
    assert settings.general.ch_cadence_patterns == ["cadence (\\d+)", "chain at (\\d+)"]


def test_socials_autosync_toggle_applies(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)
    assert settings.general.socials_autosync is False
    window._socials_autosync.setChecked(True)
    window.apply()
    assert settings.general.socials_autosync is True


def test_sync_now_reports_when_the_eq_dir_is_unset(qtbot) -> None:
    class FakeSync:
        def __init__(self) -> None:
            self.calls = 0

        def sync(self, now) -> int:
            self.calls += 1
            return 1

        def status_text(self) -> str:
            return "Last synced at 12:00 — 2 new."

    sync = FakeSync()
    window = _window(qtbot, socials_sync=sync)
    window._sync_socials_now()
    # Preflight fails without an EQ install directory, so no sync is attempted.
    assert sync.calls == 0
    assert "EQ install directory" in window._socials_sync_status.text()


def test_sync_now_runs_and_shows_the_result(qtbot, tmp_path: Path) -> None:
    class FakeSync:
        def __init__(self) -> None:
            self.calls = 0

        def sync(self, now) -> int:
            self.calls += 1
            return 2

        def status_text(self) -> str:
            return "Last synced at 12:00 — 2 new."

    (tmp_path / "uifiles").mkdir()
    (tmp_path / "eqgame.exe").write_text("")
    sync = FakeSync()
    window = _window(qtbot, socials_sync=sync)
    window._install_dir.edit.setText(str(tmp_path))
    window._sync_socials_now()
    assert sync.calls == 1
    assert "2 new" in window._socials_sync_status.text()


def test_sync_now_reports_when_nothing_changed(qtbot, tmp_path: Path) -> None:
    class FakeSync:
        def sync(self, now) -> int:
            return 0

        def status_text(self) -> str:
            return "unused"

    (tmp_path / "uifiles").mkdir()
    (tmp_path / "eqgame.exe").write_text("")
    window = _window(qtbot, socials_sync=FakeSync())
    window._install_dir.edit.setText(str(tmp_path))
    window._sync_socials_now()
    assert "Nothing to sync" in window._socials_sync_status.text()


def test_extra_pages_build_and_apply(qtbot) -> None:
    from PySide6.QtWidgets import QLabel

    from nparseplus.ui.settingswindow import SettingsPageSpec

    built: list[object] = []
    applied: list[object] = []

    def build(parent):
        page = QLabel("plugin page", parent)
        built.append(page)
        return page

    window = _window(
        qtbot,
        extra_pages=[SettingsPageSpec("My Plugin", build, lambda page: applied.append(page))],
    )
    assert built, "extra page builder was not called"
    assert window._sidebar.item(window._sidebar.count() - 1).text() == "My Plugin"
    window.apply()
    assert applied == built  # per-page apply receives the built widget


def test_extra_page_builder_failure_isolated(qtbot) -> None:
    from nparseplus.ui.settingswindow import SettingsPageSpec

    def explode(parent):
        raise RuntimeError("builder boom")

    window = _window(qtbot, extra_pages=[SettingsPageSpec("Broken", explode)])
    # The page slot exists (with a placeholder) and the window still applies.
    assert window._sidebar.item(window._sidebar.count() - 1).text() == "Broken"
    window.apply()


def test_extra_page_apply_failure_isolated(qtbot) -> None:
    from PySide6.QtWidgets import QLabel

    from nparseplus.ui.settingswindow import SettingsPageSpec

    def bad_apply(page):
        raise RuntimeError("apply boom")

    saves: list[None] = []
    window = _window(
        qtbot,
        on_save=lambda: saves.append(None),
        extra_pages=[SettingsPageSpec("Flaky", lambda parent: QLabel(parent), bad_apply)],
    )
    window.apply()  # must not raise
    assert saves  # the built-in apply flow still completed


def test_plugins_toggle_is_off_and_no_plugins_page_by_default(qtbot) -> None:
    # The base-user view: the switch exists on Advanced, but nothing else in
    # the window mentions add-ons.
    window = _window(qtbot)
    assert window._plugins_enabled_box.isChecked() is False
    titles = [window._sidebar.item(i).text() for i in range(window._sidebar.count())]
    assert "Plugins" not in titles


def test_plugins_toggle_persists_and_warns_about_the_restart(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)
    notices: list[bool] = []
    window._notify_plugins_restart = lambda *, enabled: notices.append(enabled)

    window._plugins_enabled_box.setChecked(True)
    window.apply()
    assert settings.plugins.enabled is True
    assert notices == [True]  # takes effect next launch, so say so

    window.apply()
    assert notices == [True]  # unchanged: no second nag

    window._plugins_enabled_box.setChecked(False)
    window.apply()
    assert settings.plugins.enabled is False
    assert notices == [True, False]


# --- how small the window is allowed to get ---------------------------------


def test_the_window_can_be_narrowed_to_its_stated_floor(qtbot) -> None:
    """The complaint this fixes: the window would not shrink.

    A QStackedWidget's minimum is its widest page, so one wide page (Sharing,
    whose rows are a long label beside a combo listing "pigparse.org character
    page") used to pin the whole window at ~550px. What matters is not the
    number but the invariant: Qt's own floor must stay under the floor we
    state, because the larger of the two is what actually wins.
    """
    window = _window(qtbot)

    # Split so a failure says which half moved: the pages, or the chrome.
    assert window._stack.minimumSizeHint().width() <= MIN_SIZE[0] - window._sidebar.width()
    assert window.minimumSizeHint().width() <= MIN_SIZE[0]
    assert window.minimumSizeHint().height() <= MIN_SIZE[1]
    assert (window.minimumWidth(), window.minimumHeight()) == MIN_SIZE


def test_a_bigger_font_never_lets_a_PAGE_set_the_floor(qtbot) -> None:
    """Pages scroll, so a larger font makes this window scroll sooner rather
    than refuse to shrink — which is what it did before, growing its minimum
    with every point of font size.

    Asserted on the *stack* rather than on the window, deliberately. At a big
    enough font the window does get a floor above :data:`MIN_SIZE`, set by
    chrome whose text cannot be made narrower — the sidebar and the two
    buttons — and how wide that is depends on the platform's font (an earlier
    version of this test asserted a whole-window number and failed only on
    Windows, where "Apply & Save" is wider). That floor is honest. A page
    imposing one is the bug, and this is the assertion that catches a page
    added later that cannot wrap or scroll.
    """
    settings = Settings()
    settings.general.font_size = 24
    window = _window(qtbot, settings)

    pages = window._stack.minimumSizeHint().width()
    assert pages <= MIN_SIZE[0] - window._sidebar.width(), (
        f"a settings page wants {pages}px, so it — not the chrome — is the floor"
    )
    assert (window.minimumWidth(), window.minimumHeight()) == MIN_SIZE


def test_every_page_scrolls_including_contributed_ones(qtbot) -> None:
    """Content that no longer fits has to stay reachable."""
    from nparseplus.ui.settingswindow import SettingsPageSpec

    window = _window(
        qtbot,
        extra_pages=[SettingsPageSpec("My Plugin", lambda parent: QLabel("page", parent))],
    )

    for index in range(window._stack.count()):
        page = window._stack.widget(index)
        name = window._sidebar.item(index).text()
        assert isinstance(page, QScrollArea), f"{name} page does not scroll"
        assert page.widgetResizable(), f"{name} page does not fill its viewport"


def test_a_contributed_page_cannot_pin_the_window_open(qtbot) -> None:
    """The window's minimum size is not a plugin's to raise.

    Not hypothetical: the widest page in the app is a contributed one — the
    Plugins manager's table of installed add-ons — so leaving ``extra_pages``
    out of the scroll wrapper would have left the window pinned wide for
    exactly the users who enabled plugins. See
    ``tests/ui/test_pluginmanager.py`` for the same check against the real
    page rather than this stand-in.
    """
    from nparseplus.ui.settingswindow import SettingsPageSpec

    applied: list[object] = []

    def build(parent):
        page = QLabel("a very wide plugin page", parent)
        page.setMinimumWidth(1800)
        return page

    window = _window(
        qtbot,
        extra_pages=[SettingsPageSpec("Greedy", build, applied.append)],
    )

    assert window._stack.minimumSizeHint().width() <= MIN_SIZE[0] - window._sidebar.width()

    # ...and the wrapper stays invisible to the contributor: apply() hands
    # back the widget the builder made, not the QScrollArea around it.
    window.apply()
    assert [type(page) for page in applied] == [QLabel]
    assert applied[0].minimumWidth() == 1800  # untouched, just scrolled


def test_every_form_row_wraps_rather_than_widening_the_window(qtbot) -> None:
    """Swept in one place (``_let_rows_wrap``) precisely so that the forms
    nested inside group boxes — the widest rows here — cannot be missed."""
    window = _window(qtbot)

    forms = window.findChildren(QFormLayout)
    assert forms, "the settings pages are built out of form layouts"
    assert all(form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapLongRows for form in forms)
