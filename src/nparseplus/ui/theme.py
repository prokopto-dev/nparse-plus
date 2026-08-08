"""UI palette — the app's colour values, as opposed to its skin.

One palette, deliberately. nParse+ renders on top of EverQuest, where a pale
panel is a flashbang, so every surface is dark; a light alternative existed
briefly (eqtool #148) and bought nothing but a second set of values to keep in
sync and a restart to switch between them.

What survives is the split that does earn its keep, with ``ui/skins.py``:

    the palette owns VALUE, the skin owns HUE.

A palette answers "what colour is body text, a field background, a page
ground" — the readability floor, which no skin may move. A :class:`Skin`
answers "what does the window's edge look like, how loud is the title" and
contributes one accent on top. ``ui/chrome.py`` composes the two for the
config windows; the overlays read both directly.

Qt-free, like skins.py and chrome.py.
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
    # -- config surfaces (Settings, the editors, the plugin manager) -------
    # ``map_input_*`` above already means "an input field", so ``chrome_for``
    # aliases the field tokens to them rather than duplicating.
    surface: str  # a config window's ground
    surface_alt: str  # a raised strip on it (group box, header band)
    hint: str  # de-emphasised caption under a field
    disabled: str  # a control the user cannot reach right now


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
    surface="#16171b",
    surface_alt="#1d1f24",
    hint="#8b8f9a",
    disabled="#5a5e69",
)


def palette() -> Palette:
    """The app's colour values.

    A function rather than a bare constant so the call sites read the same as
    ``skins.skin()`` and so a second palette, if one is ever justified again,
    lands here instead of in every caller.
    """
    return DARK
