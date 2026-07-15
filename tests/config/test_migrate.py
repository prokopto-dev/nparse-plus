"""Tests for legacy nparse.config.json migration."""

import json
from pathlib import Path

from nparseplus.config.migrate import find_legacy_config, migrate_legacy
from nparseplus.config.settings import Settings, load_settings

LEGACY_CONFIG = {
    "general": {
        "eq_log_dir": "/Users/someone/Games/EverQuest/Logs",
        "update_check": False,
        "window_flush": True,
    },
    "sharing": {
        "discord_channel": False,
        "enabled": True,
        "group_key": "myguild",
        "player_name": "Xantik",
        "player_name_override": True,
        "reconnect_delay": 5,
        "url": "ws://example.net:8424",
    },
    "maps": {
        "always_on_top": True,
        "auto_follow": False,
        "auto_hide_menu": True,
        "clickthrough": True,
        "closest_z_alpha": 25,
        "color": "#000000",
        "current_z_alpha": 90,
        "frameless": False,
        "geometry": [0, 30, 400, 500],
        "grid_line_width": 2,
        "last_zone": "Neriak Foreign Quarter",
        "line_width": 3,
        "opacity": 80,
        "other_z_alpha": 15,
        "scale": 0.1136,
        "show_grid": False,
        "show_mouse_location": True,
        "show_poi": False,
        "toggled": True,
        "use_z_layers": True,
    },
    "spells": {
        "always_on_top": True,
        "auto_hide_menu": False,
        "casting_window_buffer": 2000,
        "clickthrough": False,
        "color": "#000000",
        "custom_timers": [
            ["Journeyman Boots", "Your feet feel quick.", "00:18:00"],
            ["Ring 8", "* has been slain by *", "00:00:30"],
        ],
        "delay_self_buffs_on_zone": False,
        "frameless": True,
        "geometry": [400, 0, 200, 400],
        "level": 54,
        "opacity": 50,
        "toggled": False,
        "use_casting_window": False,
        "use_custom_triggers": True,
        "use_item_triggers": True,
        "use_secondary": ["levitate", "malise"],
        "use_secondary_all": True,
    },
    "discord": {
        "always_on_top": False,
        "auto_hide_menu": True,
        "bg_opacity": 25,
        "clickthrough": False,
        "color": "#000000",
        "frameless": True,
        "geometry": [0, 400, 200, 400],
        "opacity": 80,
        "toggled": False,
        "url": "wss://discord.example/stream",
    },
}


def write_legacy(directory: Path) -> Path:
    path = directory / "nparse.config.json"
    path.write_text(json.dumps(LEGACY_CONFIG, indent=4), encoding="utf-8")
    return path


def test_migrate_general_and_sharing(tmp_path: Path) -> None:
    settings = migrate_legacy(write_legacy(tmp_path))
    assert settings is not None
    assert settings.general.eq_log_dir == Path("/Users/someone/Games/EverQuest/Logs")
    assert settings.general.update_check is False
    assert settings.sharing.mode == "nparse"  # legacy sharing was enabled
    assert settings.sharing.nparse_ws_url == "ws://example.net:8424"
    assert settings.sharing.nparse_group_key == "myguild"
    assert settings.sharing.player_name_override == "Xantik"


def test_migrate_maps_and_spellwindow(tmp_path: Path) -> None:
    settings = migrate_legacy(write_legacy(tmp_path))
    assert settings is not None
    maps = settings.maps
    assert (maps.line_width, maps.grid_line_width) == (3, 2)
    assert maps.show_poi is False
    assert maps.show_grid is False
    assert maps.use_z_layers is True
    assert (maps.closest_z_alpha, maps.current_z_alpha, maps.other_z_alpha) == (25, 90, 15)
    assert maps.last_zone == "Neriak Foreign Quarter"
    assert maps.scale == 0.1136
    assert maps.auto_follow is False

    spells = settings.spellwindow
    assert spells.casting_window_buffer == 2000
    assert spells.delay_self_buffs_on_zone is False
    assert spells.level == 54
    assert spells.use_casting_window is False
    assert spells.use_item_triggers is True
    assert spells.use_secondary == ["levitate", "malise"]
    assert spells.use_secondary_all is True
    # New options keep their defaults.
    assert spells.you_only_spells is False
    assert spells.show_random_rolls is True
    assert spells.raid_mode_auto is True


def test_migrate_window_states(tmp_path: Path) -> None:
    settings = migrate_legacy(write_legacy(tmp_path))
    assert settings is not None
    assert set(settings.windows) == {"maps", "spells", "discord"}
    maps = settings.windows["maps"]
    assert maps.geometry == (0, 30, 400, 500)
    assert maps.shown is True  # legacy "toggled"
    assert maps.clickthrough is True
    assert maps.frameless is False
    assert maps.opacity == 0.8  # legacy 0-100 scale
    spells = settings.windows["spells"]
    assert spells.opacity == 0.5
    assert spells.auto_hide_menu is False
    assert spells.shown is False
    assert settings.windows["discord"].always_on_top is False
    assert settings.discord.url == "wss://discord.example/stream"


def test_migrate_custom_timers_to_triggers(tmp_path: Path) -> None:
    settings = migrate_legacy(write_legacy(tmp_path))
    assert settings is not None
    assert len(settings.triggers) == 2
    jboots, ring8 = settings.triggers
    assert jboots.trigger_name == "Journeyman Boots"
    assert jboots.trigger_enabled is True
    assert jboots.use_regex is True
    assert jboots.search_text == r"Your\ feet\ feel\ quick\."
    assert jboots.timer is not None
    assert jboots.timer.duration == 18 * 60
    assert ring8.search_text == r".*\ has\ been\ slain\ by\ .*"
    assert ring8.timer is not None
    assert ring8.timer.duration == 30
    # Raw legacy entries are preserved for lossless import.
    assert settings.custom_timers == [
        ["Journeyman Boots", "Your feet feel quick.", "00:18:00"],
        ["Ring 8", "* has been slain by *", "00:00:30"],
    ]


def test_migrated_settings_roundtrip(tmp_path: Path) -> None:
    settings = migrate_legacy(write_legacy(tmp_path))
    assert settings is not None
    from nparseplus.config.settings import save_settings

    out = tmp_path / "settings.json"
    save_settings(settings, out)
    assert load_settings(out) == settings


def test_migrate_missing_or_corrupt_returns_none(tmp_path: Path) -> None:
    assert migrate_legacy(tmp_path / "nope.json") is None
    corrupt = tmp_path / "nparse.config.json"
    corrupt.write_text("{oops", encoding="utf-8")
    assert migrate_legacy(corrupt) is None
    not_a_dict = tmp_path / "list.json"
    not_a_dict.write_text("[1, 2, 3]", encoding="utf-8")
    assert migrate_legacy(not_a_dict) is None


def test_migrate_partial_legacy_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / "nparse.config.json"
    path.write_text(json.dumps({"general": {"eq_log_dir": "/tmp/logs"}}), encoding="utf-8")
    settings = migrate_legacy(path)
    assert settings is not None
    assert settings.general.eq_log_dir == Path("/tmp/logs")
    assert settings.maps == Settings().maps
    assert settings.sharing.mode == "off"  # legacy present but sharing not enabled
    assert settings.triggers == []


def test_find_legacy_config_in_cwd_and_settings_dir(tmp_path: Path, monkeypatch) -> None:
    settings_dir = tmp_path / "confdir"
    settings_dir.mkdir()
    empty_cwd = tmp_path / "cwd"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)
    assert find_legacy_config(settings_dir=settings_dir) is None
    beside = write_legacy(settings_dir)
    assert find_legacy_config(settings_dir=settings_dir) == beside
    in_cwd = write_legacy(empty_cwd)
    assert find_legacy_config(settings_dir=settings_dir) == in_cwd  # CWD wins


def test_load_settings_migrates_when_settings_file_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_legacy(tmp_path)
    settings = load_settings(tmp_path / "settings.json")
    assert settings.sharing.nparse_group_key == "myguild"
    assert len(settings.triggers) == 2
