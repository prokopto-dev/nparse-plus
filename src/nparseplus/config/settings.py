"""Pydantic settings models and JSON persistence for nParse+.

Plain ``BaseModel`` subclasses (not ``BaseSettings``): the whole tree is
persisted as a single human-editable ``settings.json`` written atomically.
No Qt imports allowed in this layer.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from nparseplus.config.paths import ensure_config_dir, settings_path
from nparseplus.core.ch_chain import DEFAULT_CH_CADENCE_PATTERNS

# Triggers persist in the engine's own (Qt-free) schema — one model, no drift.
from nparseplus.core.triggers.model import Trigger

SCHEMA_VERSION = 1


def _default_eq_log_dir() -> Path:
    return Path.home() / "Games/EverQuest/Logs"


class OverlayRegion(BaseModel):
    """Per-region placement for the event overlay's three zones.

    ``anchor`` picks the vertical reference line inside the overlay window
    (top margin / vertical center / bottom margin); ``dx``/``dy`` nudge the
    region off that anchor (px, +x right / +y down). ``width`` overrides the
    region's default host width (None = the region's built-in default).
    """

    anchor: Literal["top", "center", "bottom"] = "top"
    dx: int = 0
    dy: int = 0
    width: int | None = None


class WindowState(BaseModel):
    """Persisted per-window UI state (geometry + overlay flags)."""

    geometry: tuple[int, int, int, int] | None = None
    always_on_top: bool = True
    clickthrough: bool = False
    frameless: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    shown: bool = False
    # Migrated + persisted, but only the legacy ParserWindow path (helpers/
    # parser.py hover-reveal menu bar) consumes it — the new overlays have no
    # such menu bar. Kept write-only for the new windows, and reserved until
    # the legacy maps/discord windows are rebuilt on the new stack (see #8).
    auto_hide_menu: bool = True
    # Event-overlay per-region placement (keys: "lanes", "alert", "bars").
    # None = the legacy stacked QVBoxLayout (regions not independently placed).
    overlay_regions: dict[str, OverlayRegion] | None = None


class WindowLayoutPreset(BaseModel):
    """A named snapshot of window positions and sizes.

    Visibility and overlay behavior deliberately remain live settings: a
    layout only answers where each window belongs and how large it is.
    """

    geometries: dict[str, tuple[int, int, int, int]] = Field(default_factory=dict)


class GeneralSettings(BaseModel):
    eq_log_dir: Path = Field(default_factory=_default_eq_log_dir)
    eq_install_dir: Path | None = None
    update_check: bool = True
    # UI theme (eqtool #148); applied at startup, restart to change.
    theme: Literal["dark", "light"] = "dark"
    font_size: int = Field(default=12, ge=6)
    global_audio_volume: int = Field(default=100, ge=0, le=100)
    tts_voice: str | None = None
    log_archive_enabled: bool = False
    log_archive_size_mb: int = Field(default=100, ge=1)
    # How long overlay alert text (ENRAGED, FTE, CH warnings...) stays on screen.
    overlay_text_seconds: float = Field(default=4.0, ge=1.0, le=30.0)
    # Soft drop-shadow behind overlay alert text. The blur effect re-renders
    # on every repaint of the translucent always-on-top overlay — measurably
    # expensive on macOS compositing; turn off if the overlay stutters.
    overlay_text_shadow: bool = True
    # How long a CH chain lane stays visible after the last CH call for its
    # target (chips in flight always keep the lane alive regardless).
    ch_lane_retention_seconds: float = Field(default=20.0, ge=5.0, le=300.0)
    # Follow only CH calls prefixed with this raid tag (e.g. "GG"); blank =
    # all calls (EQTool ChChainTagOverlay).
    ch_chain_tag: str = ""
    # nparseplus extension (#15): when the raid leader calls a cadence
    # ("healers to 4 seconds"), draw a muted marker in the CH lane at the
    # declared second. Off by default; opt-in.
    ch_cadence_indicator: bool = False
    # User-editable regexes that recognize a cadence callout — each with a
    # first capturing group for the seconds (like a trigger's search text).
    # Defaults to the stock phrasings; empty falls back to the same defaults.
    ch_cadence_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CH_CADENCE_PATTERNS)
    )
    # Bard AoE hit counter: yellow overlay + TTS tally of hits/resists when a
    # bard swarm session finalizes (EQTool BardCountHandler).
    bard_count_enabled: bool = True


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
    # nparseplus extension (#17): EQTool's adaptive raid regrouping, redesigned.
    # When on AND a target group's distinct targets outnumber its distinct
    # spells, that group flips to spell-as-header (targets become the rows) so
    # a raid-wide buff reads as one spell over many people. Strictly opt-in;
    # targets stay the headers by default. Orientation is derived per group
    # per render (never persisted), which is what fixes the old global-flag
    # desync (stuck headers on post-/who target recognition; see
    # core/timers.py). The old ``raid_mode_auto`` key is ignored on load.
    raid_group_by_spell: bool = False
    # nparseplus extension: how rows sort under each group header. Default
    # "time_remaining" puts the soonest-to-expire row at the top (counters,
    # which never expire, sort last); "alphabetical" is the legacy order.
    row_sort: Literal["time_remaining", "alphabetical"] = "time_remaining"
    # nparseplus extension: per-category display toggles for the built-in
    # timer sections (display-only — the timers keep running and expiry
    # audio still fires while hidden).
    show_boats: bool = True
    # show_mob_timers: mob respawn ("--Dead--"), Sirran, FTE-rule countdowns.
    # show_roll_timers: Ring 8 / Scout Charisa server roll windows.
    # show_custom_timers: trigger, chat-command, and shared remote timers
    # (the merged "Custom Timers" heading — replaces the old
    # show_trigger_timers key, which is now ignored on load).
    show_mob_timers: bool = True
    show_roll_timers: bool = True
    show_custom_timers: bool = True
    # nparseplus extension (EQTool's best-guess is always on): when False,
    # ambiguous cast lines (multiple candidate spells) create no timer.
    best_guess_spells: bool = True
    # nparseplus extension (eqtool #239): speak when a respawn timer expires.
    respawn_expiry_audio: bool = False
    # nparseplus extension (GINA parity): warn when a self-buff is about to
    # fade. 0 disables; the time label also turns red inside the window.
    buff_fade_warning_seconds: int = Field(default=0, ge=0, le=300)
    buff_fade_warning_audio: bool = True
    # nparseplus extension (#16): post-expiration spell alerts. When enabled,
    # a spell whose name is in ``post_expiry_flash_spells`` keeps its row for
    # ``post_expiry_flash_seconds`` after it expires, flashing as a rebuff/
    # recast prompt (click the row to dismiss). Opt-in and per-spell.
    post_expiry_flash_enabled: bool = False
    post_expiry_flash_seconds: int = Field(default=30, ge=1, le=300)
    post_expiry_flash_spells: list[str] = Field(default_factory=list)


class DiscordSettings(BaseModel):
    """Discord relay config carried by migration but not yet read at runtime.

    The live discord overlay is still the legacy ParserWindow, which reads its
    url from the legacy ``nparse.config.json`` (not from here). These fields are
    migration-preserved placeholders reserved for the discord-window rebuild on
    the new stack; ``channel`` has no legacy source today either (see #9).
    """

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


class SpawnMarker(BaseModel):
    """A user-placed spawn-point timer on the map (nparse #10 / eqtool #190).

    ``ends_at`` is the running countdown's absolute naive-local end; None (or
    a past time) restores in the idle/popped state.
    """

    x: float
    y: float
    z: float
    length_s: int = 10
    ends_at: datetime | None = None


class WaypointMarker(BaseModel):
    """A user-placed map waypoint (the single navigation WayPoint, or a named
    user waypoint such as a corpse marker)."""

    x: float
    y: float
    z: float
    icon: str = "waypoint"
    name: str = ""


class ZoneMarkers(BaseModel):
    """Per-zone persisted map markers, keyed by the map-file short zone key."""

    spawn_points: list[SpawnMarker] = Field(default_factory=list)
    way_point: WaypointMarker | None = None
    user_waypoints: list[WaypointMarker] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.spawn_points or self.way_point or self.user_waypoints)


class MapMarkerStore:
    """Load/save gate the map canvas uses (the legacy maps code must not grow
    its own settings-writing conventions — this is the only bridge)."""

    def __init__(self, settings: Settings, request_save: Callable[[], None] | None = None):
        self._settings = settings
        self._request_save = request_save

    def load(self, zone_key: str) -> ZoneMarkers:
        return self._settings.map_markers.get(zone_key) or ZoneMarkers()

    def save(self, zone_key: str, markers: ZoneMarkers) -> None:
        if markers.empty:
            self._settings.map_markers.pop(zone_key, None)
        else:
            self._settings.map_markers[zone_key] = markers
        if self._request_save is not None:
            self._request_save()


class SavedTimer(BaseModel):
    """A persisted respawn/custom timer row (nparse #57).

    Unlike YouSpell's seconds-left (buff clocks freeze while camped), respawns
    keep counting in real time, so the absolute end is stored. Naive local
    datetime — the whole pipeline compares naive.
    """

    name: str
    ends_at: datetime
    total_duration_s: float


class PlayerInfo(BaseModel):
    name: str
    server: str
    zone: str = ""
    guild_name: str = ""
    player_class: int | None = None
    level: int | None = None
    map_location_sharing: Literal["everyone", "guild", "off"] = "everyone"
    share_timers: bool = True
    # EQTool PlayerInfo.TimerRecastSetting: recasting a detrimental spell on an
    # NPC either refreshes the running row or stacks a new one per cast.
    timer_recast: Literal["RestartCurrentTimer", "StartNewTimer"] = "RestartCurrentTimer"
    tracking_skill: int = 0
    # Spell-filter classes (PlayerClass wire ints). None = show all classes'
    # spells (EQTool's ShowSpellsForClasses null default).
    show_spells_for_classes: list[int] | None = None
    you_spells: list[YouSpell] = Field(default_factory=list)
    respawn_timers: list[SavedTimer] = Field(default_factory=list)


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
    window_layouts: dict[str, WindowLayoutPreset] = Field(default_factory=dict)
    # Persisted map markers per zone short key (nparse #10 / eqtool #190).
    # Deliberately in the NEW settings, not the legacy maps config: durable
    # user data that must outlive the planned maps-window rebuild.
    map_markers: dict[str, ZoneMarkers] = Field(default_factory=dict)
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
