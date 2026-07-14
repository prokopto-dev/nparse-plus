"""Filesystem locations for nParse+ configuration and cache data.

All paths are derived via platformdirs so they land in the conventional
per-user location on each OS (e.g. ~/Library/Application Support on macOS,
%APPDATA% on Windows, ~/.config on Linux).
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

APP_NAME = "nparseplus"

SETTINGS_FILENAME = "settings.json"


def config_dir() -> Path:
    """Per-user configuration directory for nParse+."""
    return Path(user_config_dir(APP_NAME))


def cache_dir() -> Path:
    """Per-user cache directory for nParse+."""
    return Path(user_cache_dir(APP_NAME))


def settings_path() -> Path:
    """Full path of the main settings file."""
    return config_dir() / SETTINGS_FILENAME


def ensure_config_dir() -> Path:
    """Create the config directory (and parents) if needed; return it."""
    path = config_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_cache_dir() -> Path:
    """Create the cache directory (and parents) if needed; return it."""
    path = cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
