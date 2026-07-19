"""Settings and configuration layer for nParse+ (Qt-free)."""

from nparseplus.config.migrate import find_legacy_config, migrate_legacy
from nparseplus.config.paths import (
    config_dir,
    ensure_config_dir,
    settings_path,
)
from nparseplus.config.settings import (
    DebouncedSaver,
    DiscordSettings,
    GeneralSettings,
    MapSettings,
    PlayerInfo,
    Settings,
    SharingSettings,
    SpellWindowSettings,
    Trigger,
    WindowLayoutPreset,
    WindowState,
    YouSpell,
    get_player,
    load_settings,
    save_settings,
)

__all__ = [
    "DebouncedSaver",
    "DiscordSettings",
    "GeneralSettings",
    "MapSettings",
    "PlayerInfo",
    "Settings",
    "SharingSettings",
    "SpellWindowSettings",
    "Trigger",
    "WindowLayoutPreset",
    "WindowState",
    "YouSpell",
    "config_dir",
    "ensure_config_dir",
    "find_legacy_config",
    "get_player",
    "load_settings",
    "migrate_legacy",
    "save_settings",
    "settings_path",
]
