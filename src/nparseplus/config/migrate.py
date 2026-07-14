"""Migration of legacy nparse ``nparse.config.json`` files into the new model.

The legacy schema is defined by ``nparseplus.helpers.config.verify_settings``:
sections ``general`` / ``sharing`` / ``maps`` / ``spells`` / ``discord``, where
the maps/spells/discord sections mix window state (geometry, toggled, opacity,
clickthrough, ...) with feature settings.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nparseplus.config.paths import settings_path
from nparseplus.config.settings import (
    DiscordSettings,
    GeneralSettings,
    MapSettings,
    Settings,
    SharingSettings,
    SpellWindowSettings,
    TriggerModel,
    TriggerTimer,
    WindowState,
)

LEGACY_FILENAME = "nparse.config.json"

LEGACY_TRIGGER_FOLDER = "Legacy Custom Timers"


def find_legacy_config(settings_dir: Path | None = None) -> Path | None:
    """Locate a legacy nparse.config.json in CWD or beside the settings dir."""
    if settings_dir is None:
        settings_dir = settings_path().parent
    for candidate in (Path.cwd() / LEGACY_FILENAME, settings_dir / LEGACY_FILENAME):
        if candidate.is_file():
            return candidate
    return None


def migrate_legacy(path: Path) -> Settings | None:
    """Read a legacy nparse.config.json and map it into a new ``Settings``.

    Returns None when the file is absent or unreadable/corrupt. Unknown or
    ill-typed legacy values fall back to the new-model defaults, so a partial
    or hand-edited legacy file still migrates.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    general = _section(raw, "general")
    sharing = _section(raw, "sharing")
    maps = _section(raw, "maps")
    spells = _section(raw, "spells")
    discord = _section(raw, "discord")

    settings = Settings(
        general=_migrate_general(general),
        sharing=_migrate_sharing(sharing),
        maps=_migrate_maps(maps),
        spellwindow=_migrate_spellwindow(spells),
        discord=_migrate_discord(discord),
        windows={
            "maps": _window_state(maps),
            "spells": _window_state(spells),
            "discord": _window_state(discord),
        },
    )

    custom_timers = spells.get("custom_timers")
    if isinstance(custom_timers, list):
        for entry in custom_timers:
            trigger = _custom_timer_to_trigger(entry)
            if trigger is not None:
                settings.triggers.append(trigger)
                settings.custom_timers.append([str(part) for part in entry[:3]])
    return settings


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _get(section: dict[str, Any], key: str, kind: type, default: Any) -> Any:
    """Fetch a legacy value, insisting on its type (bool is not an int here)."""
    value = section.get(key, default)
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        return default
    return value


def _migrate_general(section: dict[str, Any]) -> GeneralSettings:
    general = GeneralSettings(update_check=_get(section, "update_check", bool, True))
    eq_log_dir = _get(section, "eq_log_dir", str, "")
    if eq_log_dir:
        general.eq_log_dir = Path(eq_log_dir)
    return general


def _migrate_sharing(section: dict[str, Any]) -> SharingSettings:
    sharing = SharingSettings()
    # Legacy sharing was the nparse websocket protocol; keep the user's choice
    # of on/off but leave new installs (no legacy file) on the pigparse default.
    sharing.mode = "nparse" if _get(section, "enabled", bool, False) else "off"
    sharing.nparse_ws_url = _get(section, "url", str, sharing.nparse_ws_url)
    sharing.nparse_group_key = _get(section, "group_key", str, sharing.nparse_group_key)
    if _get(section, "player_name_override", bool, False):
        player_name = _get(section, "player_name", str, "")
        if player_name and player_name != "ConfigureMe":
            sharing.player_name_override = player_name
    return sharing


def _migrate_maps(section: dict[str, Any]) -> MapSettings:
    defaults = MapSettings()
    return MapSettings(
        line_width=_ranged(section, "line_width", defaults.line_width, 1, 10),
        grid_line_width=_ranged(section, "grid_line_width", defaults.grid_line_width, 1, 10),
        show_poi=_get(section, "show_poi", bool, defaults.show_poi),
        show_grid=_get(section, "show_grid", bool, defaults.show_grid),
        show_mouse_location=_get(
            section, "show_mouse_location", bool, defaults.show_mouse_location
        ),
        use_z_layers=_get(section, "use_z_layers", bool, defaults.use_z_layers),
        closest_z_alpha=_ranged(section, "closest_z_alpha", defaults.closest_z_alpha, 1, 100),
        current_z_alpha=_ranged(section, "current_z_alpha", defaults.current_z_alpha, 1, 100),
        other_z_alpha=_ranged(section, "other_z_alpha", defaults.other_z_alpha, 1, 100),
        last_zone=_get(section, "last_zone", str, defaults.last_zone),
        scale=_get(section, "scale", float, defaults.scale),
        auto_follow=_get(section, "auto_follow", bool, defaults.auto_follow),
    )


def _migrate_spellwindow(section: dict[str, Any]) -> SpellWindowSettings:
    defaults = SpellWindowSettings()
    use_secondary = section.get("use_secondary", defaults.use_secondary)
    if not (isinstance(use_secondary, list) and all(isinstance(x, str) for x in use_secondary)):
        use_secondary = defaults.use_secondary
    return SpellWindowSettings(
        casting_window_buffer=_ranged(
            section, "casting_window_buffer", defaults.casting_window_buffer, 1, 4000
        ),
        delay_self_buffs_on_zone=_get(
            section, "delay_self_buffs_on_zone", bool, defaults.delay_self_buffs_on_zone
        ),
        level=_ranged(section, "level", defaults.level, 1, 65),
        sound_enabled=_get(section, "sound_enabled", bool, defaults.sound_enabled),
        sound_file=_get(section, "sound_file", str, defaults.sound_file),
        use_casting_window=_get(section, "use_casting_window", bool, defaults.use_casting_window),
        use_item_triggers=_get(section, "use_item_triggers", bool, defaults.use_item_triggers),
        use_custom_triggers=_get(
            section, "use_custom_triggers", bool, defaults.use_custom_triggers
        ),
        use_secondary=list(use_secondary),
        use_secondary_all=_get(section, "use_secondary_all", bool, defaults.use_secondary_all),
    )


def _migrate_discord(section: dict[str, Any]) -> DiscordSettings:
    return DiscordSettings(
        url=_get(section, "url", str, ""),
        channel=_get(section, "channel", str, ""),
    )


def _ranged(section: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = _get(section, key, int, default)
    return value if low <= value <= high else default


def _window_state(section: dict[str, Any]) -> WindowState:
    state = WindowState()
    geometry = section.get("geometry")
    if (
        isinstance(geometry, list)
        and len(geometry) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in geometry)
    ):
        state.geometry = (geometry[0], geometry[1], geometry[2], geometry[3])
    state.always_on_top = _get(section, "always_on_top", bool, True)
    state.clickthrough = _get(section, "clickthrough", bool, False)
    state.frameless = _get(section, "frameless", bool, True)
    state.shown = _get(section, "toggled", bool, False)
    state.auto_hide_menu = _get(section, "auto_hide_menu", bool, True)
    opacity = _get(section, "opacity", int, 100)
    state.opacity = min(max(opacity, 0), 100) / 100.0
    return state


def _custom_timer_to_trigger(entry: Any) -> TriggerModel | None:
    """Convert a legacy [name, matchtext, "hh:mm:ss"] custom timer to a trigger."""
    if not (
        isinstance(entry, list)
        and len(entry) >= 3
        and all(isinstance(part, str) for part in entry[:3])
    ):
        return None
    name, matchtext, duration_text = entry[0], entry[1], entry[2]
    if not name or not matchtext:
        return None
    duration = _parse_duration(duration_text)
    if duration is None:
        return None
    # Legacy match text is literal apart from '*' wildcards: escape it, then
    # turn each (escaped) '*' into the '.*' it stood for.
    search_text = re.escape(matchtext).replace(r"\*", ".*")
    return TriggerModel(
        name=name,
        folder=LEGACY_TRIGGER_FOLDER,
        use_regex=True,
        search_text=search_text,
        timer=TriggerTimer(name=name, duration_seconds=duration),
    )


def _parse_duration(text: str) -> int | None:
    """Parse 'hh:mm:ss' (also 'mm:ss' or bare seconds) into total seconds."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None
    if any(value < 0 for value in values):
        return None
    seconds = 0
    for value in values:
        seconds = seconds * 60 + value
    return seconds
