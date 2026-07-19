"""UnifiedSettingsWindow — dual-config apply, windows grid, character pane."""

from datetime import datetime
from pathlib import Path

import pytest

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


def test_window_title(qtbot) -> None:  # qtbot: needs a QApplication to exist
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
    window._show_boats.setChecked(False)
    window._show_mob_timers.setChecked(False)
    window._show_roll_timers.setChecked(False)
    window._show_custom_timers.setChecked(False)
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
    # Legacy side.
    assert legacy["maps"]["line_width"] == 3
    assert legacy["maps"]["closest_z_alpha"] == 42
    assert legacy["maps"]["z_fade_min_opacity"] == 35
    assert legacy["maps"]["z_fade_fallback_height"] == 25
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
    # Legacy handles get the same direct call — the map must not depend on
    # the config_updated signal that fires later in apply().
    assert maps_handle.applied == 1


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


def test_inventory_upload_toggle_applies(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)
    window._inventory_upload.setChecked(True)
    window.apply()
    assert settings.pigparse_account.inventory_upload is True


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
