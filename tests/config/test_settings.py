"""Tests for nparseplus.config.settings persistence and helpers."""

import json
import threading
import time
from pathlib import Path

from nparseplus.config.settings import (
    DebouncedSaver,
    OverlayRegion,
    PlayerInfo,
    PluginEntry,
    Settings,
    WindowLayoutPreset,
    WindowState,
    get_player,
    load_settings,
    save_settings,
)
from nparseplus.core.triggers.model import Trigger, TriggerTimer


def test_defaults_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded == original


def test_populated_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    original.general.font_size = 14
    original.sharing.mode = "nparse"
    original.windows["maps"] = WindowState(geometry=(10, 20, 300, 400), opacity=0.8, shown=True)
    original.window_layouts["Laptop mode"] = WindowLayoutPreset(
        geometries={"maps": (0, 0, 800, 600), "spells": (800, 0, 220, 600)}
    )
    original.players.append(PlayerInfo(name="Xantik", server="green", level=54))
    original.triggers.append(
        Trigger(
            trigger_name="Journeyman Boots",
            search_text="Your feet feel quick\\.",
            timer=TriggerTimer(timer_name="JBoots", minutes=18),
        )
    )
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded == original
    assert loaded.windows["maps"].geometry == (10, 20, 300, 400)
    assert loaded.window_layouts["Laptop mode"].geometries["spells"] == (800, 0, 220, 600)
    assert loaded.triggers[0].trigger_id == original.triggers[0].trigger_id


def test_window_state_dict_handling(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.windows["spells"] = WindowState(clickthrough=True, always_on_top=False)
    settings.windows["dps"] = WindowState()  # geometry stays None
    save_settings(settings, path)
    loaded = load_settings(path)
    assert set(loaded.windows) == {"spells", "dps"}
    assert loaded.windows["spells"].clickthrough is True
    assert loaded.windows["spells"].always_on_top is False
    assert loaded.windows["dps"].geometry is None
    # Unknown window keys are the norm: the dict accepts any name.
    loaded.windows["brand_new_overlay"] = WindowState(shown=True)
    save_settings(loaded, path)
    assert load_settings(path).windows["brand_new_overlay"].shown is True


def test_overlay_regions_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.windows["events"] = WindowState(
        overlay_regions={
            "lanes": OverlayRegion(anchor="top", dx=10, dy=-4, width=540),
            "alert": OverlayRegion(anchor="center", dx=-8, dy=20),
            "bars": OverlayRegion(anchor="bottom"),
        }
    )
    save_settings(settings, path)
    loaded = load_settings(path)
    regions = loaded.windows["events"].overlay_regions
    assert regions is not None
    assert regions["lanes"] == OverlayRegion(anchor="top", dx=10, dy=-4, width=540)
    assert regions["alert"].anchor == "center"
    assert regions["bars"].width is None


def test_legacy_settings_without_overlay_regions_load_none(tmp_path: Path) -> None:
    # A settings.json predating the feature has no overlay_regions key; it must
    # load with the field defaulting to None (legacy stacked layout).
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.windows["events"] = WindowState(geometry=(0, 0, 800, 600))
    save_settings(settings, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["windows"]["events"]["overlay_regions"]
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.windows["events"].overlay_regions is None


def test_load_ignores_removed_spellwindow_keys(tmp_path: Path) -> None:
    # Removed keys: show_trigger_timers (the old single "Custom Timer" section
    # split into Mob/Roll/Custom) and raid_mode_auto (the EQTool global-flag
    # raid regrouping — replaced by the opt-in, per-row raid_group_by_spell in
    # #17). An older settings.json still carrying them must load without error
    # (extras are ignored) and the new toggles fall back to their defaults.
    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["spellwindow"]["show_trigger_timers"] = False
    data["spellwindow"]["raid_mode_auto"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.spellwindow.show_mob_timers is True
    assert loaded.spellwindow.show_roll_timers is True
    assert loaded.spellwindow.show_custom_timers is True
    assert not hasattr(loaded.spellwindow, "show_trigger_timers")
    assert not hasattr(loaded.spellwindow, "raid_mode_auto")
    # The redesigned raid mode is a NEW, distinct key that defaults off.
    assert loaded.spellwindow.raid_group_by_spell is False


def test_new_1_11_optin_defaults_and_roundtrip(tmp_path: Path) -> None:
    """The 1.11-batch opt-ins default off and survive a save/load round-trip."""
    s = Settings()
    assert s.general.ch_cadence_indicator is False
    assert s.general.ch_cadence_patterns  # defaults to the stock cadence regexes
    assert s.spellwindow.raid_group_by_spell is False
    assert s.spellwindow.post_expiry_flash_enabled is False
    assert s.spellwindow.post_expiry_flash_seconds == 30
    assert s.spellwindow.post_expiry_flash_spells == []

    s.general.ch_cadence_indicator = True
    s.general.ch_cadence_patterns = [r"cadence (\d+)"]
    s.spellwindow.raid_group_by_spell = True
    s.spellwindow.post_expiry_flash_enabled = True
    s.spellwindow.post_expiry_flash_seconds = 45
    s.spellwindow.post_expiry_flash_spells = ["Clarity", "Aegolism"]
    path = tmp_path / "settings.json"
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.general.ch_cadence_indicator is True
    assert loaded.general.ch_cadence_patterns == [r"cadence (\d+)"]
    assert loaded.spellwindow.raid_group_by_spell is True
    assert loaded.spellwindow.post_expiry_flash_enabled is True
    assert loaded.spellwindow.post_expiry_flash_seconds == 45
    assert loaded.spellwindow.post_expiry_flash_spells == ["Clarity", "Aegolism"]


def test_atomic_write_leaves_valid_json_and_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    settings = Settings()
    settings.maps.last_zone = "gfaydark"
    save_settings(settings, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["maps"]["last_zone"] == "gfaydark"
    assert data["schema_version"] == 1
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


def test_load_missing_file_returns_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # ensure no stray legacy config in CWD is picked up
    loaded = load_settings(tmp_path / "does-not-exist.json")
    assert loaded == Settings()


def test_load_corrupt_file_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_get_player_find_or_create() -> None:
    settings = Settings()
    first = get_player(settings, "Xantik", "green")
    assert settings.players == [first]
    again = get_player(settings, "Xantik", "green")
    assert again is first
    other_server = get_player(settings, "Xantik", "blue")
    assert other_server is not first
    assert len(settings.players) == 2


def test_debounced_saver_coalesces_bursts() -> None:
    calls: list[float] = []
    done = threading.Event()

    def record() -> None:
        calls.append(time.monotonic())
        done.set()

    saver = DebouncedSaver(record, delay=0.05)
    for _ in range(10):
        saver.request_save()
    assert calls == []  # nothing until the delay elapses
    assert done.wait(timeout=2.0)
    time.sleep(0.1)  # allow any (incorrect) extra timers to fire
    assert len(calls) == 1


def test_debounced_saver_flush_runs_pending_save_immediately() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=60.0)
    saver.request_save()
    saver.flush()
    assert calls == [1]
    saver.flush()  # nothing pending: no double save
    assert calls == [1]


def test_debounced_saver_cancel_discards_pending_save() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=0.05)
    saver.request_save()
    saver.cancel()
    time.sleep(0.15)
    assert calls == []


def test_debounced_saver_thread_safety() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=0.02)

    def hammer() -> None:
        for _ in range(50):
            saver.request_save()

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    saver.flush()
    time.sleep(0.1)
    # 400 requests must coalesce into a handful of saves at most (usually 1).
    assert 1 <= len(calls) <= 5


def test_plugins_entries_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    original.plugins.entries["merchant-prices"] = PluginEntry(
        enabled=True, approved=True, last_version="1.2.0"
    )
    original.plugins.entries["dkp"] = PluginEntry(
        enabled=False,
        approved=True,
        source_url="https://example.com/dkp.zip",
        sha256="f" * 64,
    )
    original.plugins.registry_url = "https://example.com/index.json"
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded.plugins.entries == original.plugins.entries
    assert loaded.plugins.entries["dkp"].enabled is False
    assert loaded.plugins.entries["dkp"].sha256 == "f" * 64
    assert loaded.plugins.registry_url == "https://example.com/index.json"
