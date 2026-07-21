"""
General global settings setup to provide settings.data
"""

import json
import os
import sys

data = {}
_filename = ""
APP_EXIT = False


def _resolve_config_path(filename):
    """Relative config paths resolve against the CWD from a source checkout,
    but a frozen app's CWD is inside the .app bundle (read-only, and writing
    there breaks the code signature) — use the platform config dir there."""
    if os.path.isabs(filename) or not getattr(sys, "frozen", False):
        return filename
    import platformdirs

    config_dir = platformdirs.user_config_dir("nparseplus")
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, filename)


def load(filename):
    """
    Load json from file.

    If resulting json has 'location' declared, 'data' dict will be wiped and
    populated with the yaml at file location 'location'.
    """
    global data
    global _filename
    _filename = _resolve_config_path(filename)

    try:
        with open(_filename, "r+") as f:
            data = json.loads(f.read())
    except:
        # nparse.config.json does not exist, create blank data
        data = {}


def save():
    """
    Saves json to previously opened location.
    """
    with open(_filename, mode="w") as f:
        f.write(json.dumps(data, indent=4, sort_keys=True))


def verify_settings():
    # verify nparse.config.json contains what it should and
    # set defaults if appropriate

    # general
    data["general"] = data.get("general", {})
    data["general"]["eq_log_dir"] = get_setting(data["general"].get("eq_log_dir", ""), "")
    data["general"]["window_flush"] = get_setting(data["general"].get("window_flush", True), True)
    data["general"]["update_check"] = get_setting(data["general"].get("update_check", True), True)

    # sharing
    data["sharing"] = data.get("sharing", {})
    data["sharing"]["player_name"] = get_setting(
        data["sharing"].get("player_name", "ConfigureMe"), "ConfigureMe"
    )
    data["sharing"]["player_name_override"] = get_setting(
        data["sharing"].get("player_name_override", False), False
    )
    data["sharing"]["url"] = get_setting(
        data["sharing"].get("url", "ws://sheeplauncher.net:8424"),
        "ws://sheeplauncher.net:8424",
        lambda x: x.startswith("ws://"),
    )
    data["sharing"]["reconnect_delay"] = get_setting(
        data["sharing"].get("reconnect_delay", 5), 5, lambda x: isinstance(x, int) and x >= 1
    )
    data["sharing"]["enabled"] = get_setting(data["sharing"].get("enabled", False), False)
    data["sharing"]["group_key"] = get_setting(data["sharing"].get("group_key", "public"), "public")
    data["sharing"]["discord_channel"] = get_setting(
        data["sharing"].get("discord_channel", False), False
    )

    # maps
    data["maps"] = data.get("maps", {})
    data["maps"]["antialias"] = get_setting(data["maps"].get("antialias", True), True)
    data["maps"]["auto_follow"] = get_setting(data["maps"].get("auto_follow", True), True)
    # Opt-in: cache each z-band's static line group as a device-space pixmap
    # so auto-follow panning blits instead of re-stroking every segment.
    # Needs on-hardware validation (memory vs. paint trade-off) before it can
    # default on — hence False.
    data["maps"]["band_cache"] = get_setting(data["maps"].get("band_cache", False), False)
    data["maps"]["closest_z_alpha"] = get_setting(
        data["maps"].get("closest_z_alpha", 20), 20, lambda x: 1 <= x <= 100
    )
    data["maps"]["current_z_alpha"] = get_setting(
        data["maps"].get("current_z_alpha", 100), 100, lambda x: 1 <= x <= 100
    )
    data["maps"]["geometry"] = get_setting(
        data["maps"].get("geometry", [0, 0, 400, 400]),
        [0, 0, 400, 400],
        lambda x: (
            len(x) == 4
            and isinstance(x[0], int)
            and isinstance(x[1], int)
            and isinstance(x[2], int)
            and isinstance(x[3], int)
        ),
    )
    data["maps"]["grid_line_width"] = get_setting(
        data["maps"].get("grid_line_width", 1), 1, lambda x: 1 <= x <= 10
    )
    data["maps"]["last_zone"] = get_setting(data["maps"].get("last_zone", ""), "")
    data["maps"]["line_width"] = get_setting(
        data["maps"].get("line_width", 1), 1, lambda x: 1 <= x <= 10
    )
    data["maps"]["other_z_alpha"] = get_setting(
        data["maps"].get("other_z_alpha", 10), 10, lambda x: 1 <= x <= 100
    )
    data["maps"]["scale"] = get_setting(data["maps"].get("scale", 0.07), 0.07)
    data["maps"]["show_grid"] = get_setting(data["maps"].get("show_grid", True), True)
    data["maps"]["show_mouse_location"] = get_setting(
        data["maps"].get("show_mouse_location", True), True
    )
    data["maps"]["show_poi"] = get_setting(data["maps"].get("show_poi", True), True)
    # eqtool #211: hide other players' shared dots while still sending yours.
    data["maps"]["show_other_players"] = get_setting(
        data["maps"].get("show_other_players", True), True
    )
    data["maps"]["toggled"] = get_setting(data["maps"].get("toggled", True), True)
    data["maps"]["use_z_layers"] = get_setting(data["maps"].get("use_z_layers", False), False)
    data["maps"]["map_font_scale"] = get_setting(
        data["maps"].get("map_font_scale", 100), 100, lambda x: 50 <= x <= 200
    )
    data["maps"]["z_fade_enabled"] = get_setting(data["maps"].get("z_fade_enabled", True), True)
    data["maps"]["z_fade_min_opacity"] = get_setting(
        data["maps"].get("z_fade_min_opacity", 10), 10, lambda x: 1 <= x <= 100
    )
    data["maps"]["z_fade_strength"] = get_setting(
        data["maps"].get("z_fade_strength", 100), 100, lambda x: 25 <= x <= 400
    )
    data["maps"]["z_fade_fallback_height"] = get_setting(
        data["maps"].get("z_fade_fallback_height", 0), 0, lambda x: 0 <= x <= 1000
    )
    data["maps"]["opacity"] = get_setting(
        data["maps"].get("opacity", 80), 80, lambda x: 0 <= x <= 100
    )
    data["maps"]["color"] = data["maps"].get("color", "#000000")
    data["maps"]["clickthrough"] = get_setting(data["maps"].get("clickthrough", False), False)
    data["maps"]["auto_hide_menu"] = get_setting(data["maps"].get("auto_hide_menu", True), True)
    data["maps"]["always_on_top"] = get_setting(data["maps"].get("always_on_top", True), True)
    data["maps"]["frameless"] = get_setting(data["maps"].get("frameless", True), True)

    # spells
    data["spells"] = data.get("spells", {})
    data["spells"]["casting_window_buffer"] = get_setting(
        data["spells"].get("casting_window_buffer", 1000), 1000, lambda x: 1 <= x <= 4000
    )
    data["spells"]["custom_timers"] = get_setting(
        data["spells"].get("custom_timers", [[]]),
        [["Journeyman Boots", "Your feet feel quick.", "00:18:00"]],
        lambda x: (
            isinstance(x[0], list)
            and isinstance(x[0][0], str)
            and isinstance(x[0][1], str)
            and isinstance(x[0][2], str)
        ),
    )
    data["spells"]["delay_self_buffs_on_zone"] = get_setting(
        data["spells"].get("delay_self_buffs_on_zone", True), True
    )
    data["spells"]["geometry"] = get_setting(
        data["spells"].get("geometry", [400, 0, 200, 400]),
        [400, 0, 200, 400],
        lambda x: (
            len(x) == 4
            and isinstance(x[0], int)
            and isinstance(x[1], int)
            and isinstance(x[2], int)
            and isinstance(x[3], int)
        ),
    )
    data["spells"]["level"] = get_setting(data["spells"].get("level", 1), 1, lambda x: 1 <= x <= 65)
    data["spells"]["toggled"] = get_setting(data["spells"].get("toggled", True), True)
    data["spells"]["use_casting_window"] = get_setting(
        data["spells"].get("use_casting_window", True), True
    )
    data["spells"]["use_item_triggers"] = get_setting(
        data["spells"].get("use_item_triggers", False), False
    )
    data["spells"]["use_custom_triggers"] = get_setting(
        data["spells"].get("use_custom_triggers", True), True
    )
    data["spells"]["use_secondary"] = get_setting(
        data["spells"].get("use_secondary", ["levitate"]),
        ["levitate"],
        lambda x: isinstance(x, list),
    )
    data["spells"]["use_secondary_all"] = get_setting(
        data["spells"].get("use_secondary_all", False), False
    )
    data["spells"]["opacity"] = get_setting(
        data["spells"].get("opacity", 80), 80, lambda x: 0 <= x <= 100
    )
    data["spells"]["color"] = data["spells"].get("color", "#000000")
    data["spells"]["clickthrough"] = get_setting(data["spells"].get("clickthrough", False), False)
    data["spells"]["auto_hide_menu"] = get_setting(data["spells"].get("auto_hide_menu", True), True)
    data["spells"]["always_on_top"] = get_setting(data["spells"].get("always_on_top", True), True)
    data["spells"]["frameless"] = get_setting(data["spells"].get("frameless", True), True)

    # discord
    data["discord"] = data.get("discord", {})
    data["discord"]["toggled"] = get_setting(data["discord"].get("toggled", True), True)
    data["discord"]["geometry"] = get_setting(
        data["discord"].get("geometry", [0, 400, 200, 400]),
        [0, 400, 200, 400],
        lambda x: (
            len(x) == 4
            and isinstance(x[0], int)
            and isinstance(x[1], int)
            and isinstance(x[2], int)
            and isinstance(x[3], int)
        ),
    )
    data["discord"]["url"] = get_setting(data["discord"].get("url", ""), "")
    data["discord"]["opacity"] = get_setting(
        data["discord"].get("opacity", 80), 80, lambda x: 0 <= x <= 100
    )
    data["discord"]["bg_opacity"] = get_setting(
        data["discord"].get("bg_opacity", 25), 25, lambda x: 0 <= x <= 100
    )
    data["discord"]["color"] = data["discord"].get("color", "#000000")
    data["discord"]["clickthrough"] = get_setting(data["discord"].get("clickthrough", False), False)
    data["discord"]["auto_hide_menu"] = get_setting(
        data["discord"].get("auto_hide_menu", True), True
    )
    data["discord"]["always_on_top"] = get_setting(data["discord"].get("always_on_top", True), True)
    data["discord"]["frameless"] = get_setting(data["discord"].get("frameless", True), True)


def get_setting(setting, default, func=None):
    try:
        assert type(setting) == type(default)
        if func:
            if not func(setting):
                return default
        return setting
    except:
        return default
