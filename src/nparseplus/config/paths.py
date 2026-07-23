"""Filesystem locations for nParse+ configuration and cache data.

All paths are derived via platformdirs so they land in the conventional
per-user location on each OS (e.g. ~/Library/Application Support on macOS,
%APPDATA% on Windows, ~/.config on Linux).
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

APP_NAME = "nparseplus"

SETTINGS_FILENAME = "settings.json"


def config_dir() -> Path:
    """Per-user configuration directory for nParse+."""
    return Path(user_config_dir(APP_NAME))


def settings_path() -> Path:
    """Full path of the main settings file."""
    return config_dir() / SETTINGS_FILENAME


def ensure_config_dir() -> Path:
    """Create the config directory (and parents) if needed; return it."""
    path = config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def plugins_dir() -> Path:
    """Directory scanned for user-installed plugins (files or packages)."""
    return config_dir() / "plugins"


def ensure_plugins_dir() -> Path:
    """Create the plugins directory (and parents) if needed; return it."""
    path = plugins_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def plugin_data_dir(plugin_id: str) -> Path:
    """Per-plugin private data directory (storage.json + free-form files)."""
    return config_dir() / "plugin-data" / plugin_id


def log_dir() -> Path:
    """Per-user log directory for nParse+ (crash log lives here)."""
    return Path(user_log_dir(APP_NAME))


def ensure_log_dir() -> Path:
    """Create the log directory (and parents) if needed; return it."""
    path = log_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
