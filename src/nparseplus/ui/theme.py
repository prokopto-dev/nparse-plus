"""UI palette — the dark/light theme switch (eqtool #148).

The DARK values are the exact literals the windows used before theming
existed (zero visual regression); LIGHT is the alternative set. app.py calls
``set_theme`` once at startup from ``settings.general.theme`` and picks the
matching stylesheet file (data/ui/_.css vs light.css) for the legacy windows;
theme changes take effect on restart.

The full-screen event overlay is deliberately NOT themed: it renders over the
game, where translucent dark panels are right in both themes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    panel_bg: str  # translucent container background of overlay windows
    text: str  # primary label color
    heading: str  # bold group/header text
    bar_track: str  # spell-timer progress track
    warning_text: str  # buff-fade warning label
    dps_you: str  # your own DPS row highlight
    dps_dead_header: str
    dps_live_header: str
    map_input_bg: str  # maps search box / results chrome
    map_input_text: str
    map_input_border: str


DARK = Palette(
    name="dark",
    panel_bg="rgba(0, 0, 0, 180)",
    text="#dddddd",
    heading="#ffffff",
    bar_track="rgba(255, 255, 255, 35)",
    warning_text="#ff5044",
    dps_you="#e0c341",
    dps_dead_header="rgba(90, 30, 30, 190)",
    dps_live_header="rgba(0, 40, 80, 190)",
    map_input_bg="#050505",
    map_input_text="white",
    map_input_border="#333",
)

LIGHT = Palette(
    name="light",
    panel_bg="rgba(245, 245, 245, 215)",
    text="#222222",
    heading="#000000",
    bar_track="rgba(0, 0, 0, 40)",
    warning_text="#c62828",
    dps_you="#9a6d00",
    dps_dead_header="rgba(220, 150, 150, 190)",
    dps_live_header="rgba(150, 190, 230, 190)",
    map_input_bg="#f5f5f5",
    map_input_text="#222222",
    map_input_border="#bbb",
)

_PALETTES = {"dark": DARK, "light": LIGHT}
_current = DARK


def set_theme(name: str) -> None:
    global _current
    _current = _PALETTES.get(name, DARK)


def palette() -> Palette:
    return _current


def stylesheet_filename() -> str:
    """The legacy-window stylesheet for the active theme (under data/ui/)."""
    return "_.css" if _current is DARK else "light.css"
