"""Pydantic settings models and JSON persistence for nParse+.

Plain ``BaseModel`` subclasses (not ``BaseSettings``): the whole tree is
persisted as a single human-editable ``settings.json`` written atomically.
No Qt imports allowed in this layer.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from nparseplus.config.paths import ensure_config_dir, settings_path

# Triggers persist in the engine's own (Qt-free) schema — one model, no drift.
from nparseplus.core.triggers.model import Trigger

SCHEMA_VERSION = 1


def _default_eq_log_dir() -> Path:
    return Path.home() / "Games/EverQuest/Logs"


class WindowState(BaseModel):
    """Persisted per-window UI state (geometry + overlay flags)."""

    geometry: tuple[int, int, int, int] | None = None
    always_on_top: bool = True
    clickthrough: bool = False
    frameless: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    shown: bool = False
    auto_hide_menu: bool = True


class GeneralSettings(BaseModel):
    eq_log_dir: Path = Field(default_factory=_default_eq_log_dir)
    eq_install_dir: Path | None = None
    update_check: bool = True
    font_size: int = Field(default=12, ge=6)
    global_audio_volume: int = Field(default=100, ge=0, le=100)
    tts_voice: str | None = None
    log_archive_enabled: bool = False
    log_archive_size_mb: int = Field(default=100, ge=1)
    # How long overlay alert text (ENRAGED, FTE, CH warnings...) stays on screen.
    overlay_text_seconds: float = Field(default=4.0, ge=1.0, le=30.0)
    # How long a CH chain lane stays visible after the last CH call for its
    # target (chips in flight always keep the lane alive regardless).
    ch_lane_retention_seconds: float = Field(default=20.0, ge=5.0, le=300.0)


class SharingSettings(BaseModel):
    mode: Literal["pigparse", "nparse", "off"] = "pigparse"
    pigparse_hub_url: str = "https://www.pigparse.org/PP"
    pigparse_api_url: str = "https://pigparse.azurewebsites.net"
    nparse_ws_url: str = "ws://sheeplauncher.net:8424"
    nparse_group_key: str = "public"
    player_name_override: str | None = None


class MapSettings(BaseModel):
    """Map rendering options (ported from the legacy 'maps' section)."""

    line_width: int = Field(default=1, ge=1, le=10)
    grid_line_width: int = Field(default=1, ge=1, le=10)
    show_poi: bool = True
    show_grid: bool = True
    show_mouse_location: bool = True
    use_z_layers: bool = False
    closest_z_alpha: int = Field(default=20, ge=1, le=100)
    current_z_alpha: int = Field(default=100, ge=1, le=100)
    other_z_alpha: int = Field(default=10, ge=1, le=100)
    last_zone: str = ""
    scale: float = 0.07
    auto_follow: bool = True


class SpellWindowSettings(BaseModel):
    """Spell timer window options (ported from the legacy 'spells' section)."""

    casting_window_buffer: int = Field(default=1000, ge=1, le=4000)
    delay_self_buffs_on_zone: bool = True
    level: int = Field(default=1, ge=1, le=65)
    sound_enabled: bool = True
    sound_file: str = ""
    use_casting_window: bool = True
    use_item_triggers: bool = False
    use_custom_triggers: bool = True
    use_secondary: list[str] = Field(default_factory=lambda: ["levitate"])
    use_secondary_all: bool = False
    # New (EQTool parity) options:
    you_only_spells: bool = False
    show_random_rolls: bool = True
    raid_mode_auto: bool = True
    # nparseplus extension (EQTool's best-guess is always on): when False,
    # ambiguous cast lines (multiple candidate spells) create no timer.
    best_guess_spells: bool = True


class DiscordSettings(BaseModel):
    url: str = ""
    channel: str = ""


class PigParseAccountSettings(BaseModel):
    """pigparse.org Discord-login credentials (EQTool DiscordId/ApiToken).

    ``api_token`` is a bearer credential for the inventory/auction APIs —
    treat it like a password (never log it)."""

    username: str = ""
    discord_id: str = ""
    api_token: str = ""
    inventory_upload: bool = False  # gate for the inventory watcher


class YouSpell(BaseModel):
    name: str
    seconds_left: int


class PlayerInfo(BaseModel):
    name: str
    server: str
    zone: str = ""
    guild_name: str = ""
    player_class: int | None = None
    level: int | None = None
    map_location_sharing: Literal["everyone", "guild", "off"] = "everyone"
    share_timers: bool = True
    tracking_skill: int = 0
    # Spell-filter classes (PlayerClass wire ints). None = show all classes'
    # spells (EQTool's ShowSpellsForClasses null default).
    show_spells_for_classes: list[int] | None = None
    you_spells: list[YouSpell] = Field(default_factory=list)
    best_dps: float = 0.0


class Settings(BaseModel):
    """Root settings document persisted to settings.json."""

    schema_version: int = SCHEMA_VERSION
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    sharing: SharingSettings = Field(default_factory=SharingSettings)
    maps: MapSettings = Field(default_factory=MapSettings)
    spellwindow: SpellWindowSettings = Field(default_factory=SpellWindowSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    pigparse_account: PigParseAccountSettings = Field(default_factory=PigParseAccountSettings)
    windows: dict[str, WindowState] = Field(default_factory=dict)
    players: list[PlayerInfo] = Field(default_factory=list)
    triggers: list[Trigger] = Field(default_factory=list)
    # Raw legacy custom timers ([name, matchtext, "hh:mm:ss"]) kept verbatim so a
    # legacy import is lossless even after conversion to Trigger entries.
    custom_timers: list[list[str]] = Field(default_factory=list)


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from ``path`` (default: the platform settings path).

    Missing file: returns defaults, first attempting a legacy
    ``nparse.config.json`` migration (CWD and beside the settings dir).
    A corrupt file falls back to defaults rather than raising.
    """
    if path is None:
        path = settings_path()
    if not path.exists():
        # Local import: migrate.py imports the models above.
        from nparseplus.config.migrate import find_legacy_config, migrate_legacy

        legacy = find_legacy_config(settings_dir=path.parent)
        if legacy is not None:
            migrated = migrate_legacy(legacy)
            if migrated is not None:
                return migrated
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(raw)
    except (OSError, ValueError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Atomically write ``settings`` as indented JSON (tmp file + rename)."""
    if path is None:
        ensure_config_dir()
        path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.model_dump(mode="json"), indent=2)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)


def find_player(settings: Settings, name: str, server: str) -> PlayerInfo | None:
    """Return the PlayerInfo for (name, server) if one exists (no creation)."""
    for player in settings.players:
        if player.name == name and player.server == server:
            return player
    return None


def get_player(settings: Settings, name: str, server: str) -> PlayerInfo:
    """Return the PlayerInfo for (name, server), creating and appending it if absent."""
    player = find_player(settings, name, server)
    if player is None:
        player = PlayerInfo(name=name, server=server)
        settings.players.append(player)
    return player


class DebouncedSaver:
    """Coalesces bursts of save requests into a single deferred save.

    Thread-safe. Each ``request_save()`` (re)arms a ``threading.Timer``; only
    the last request within ``delay`` seconds triggers the save callable.
    ``flush()`` runs any pending save immediately; ``cancel()`` discards it.
    """

    def __init__(self, save: Callable[[], None], delay: float = 1.0) -> None:
        self._save = save
        self._delay = delay
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending = False

    def request_save(self) -> None:
        with self._lock:
            self._pending = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._on_timer)
            self._timer.daemon = True
            self._timer.start()

    def _on_timer(self) -> None:
        with self._lock:
            if not self._pending:
                return
            self._pending = False
            self._timer = None
        self._save()

    def flush(self) -> None:
        """Run any pending save now (synchronously) and disarm the timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._pending
            self._pending = False
        if pending:
            self._save()

    def cancel(self) -> None:
        """Discard any pending save without running it."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = False
