"""Config-surface chrome — the look of every window that is *not* an overlay.

``ui/skins.py`` dresses the windows that sit on top of EverQuest. This module
dresses the ones the user configures the app with: Settings, the trigger and
macro editors, the plugin manager, the console, the dialogs. Same shape as
skins.py and theme.py — Qt-free, data plus pure functions returning stylesheet
strings, so the whole layer is unit-testable without a live window. The Qt half
(label factories, the mixin, the QPalette build) lives in ``ui/chromewidgets``.

The rule that makes this work, and the reason a config window can wear a skin
at all:

    **the palette owns value, the skin owns hue.**

Ground, field backgrounds and body text always come from the active
:class:`~nparseplus.ui.theme.Palette` — that is the readability floor, and a
skin cannot move it. The skin contributes exactly one hue, at the accent
positions: sidebar selection, focus ring, group-box titles, section captions,
hairlines, header bands, the primary button's edge. So picking Velious makes
Settings unmistakably Velious without ever putting gold text on a gold field.

Sizes follow skins.py's convention — multipliers of ``general.font_size``
through :func:`~nparseplus.ui.skins.px`, never raw px — with one deliberate
exception: the layout constants below are gutters and touch targets, not type,
and do not scale.
"""

from __future__ import annotations

from dataclasses import dataclass

from nparseplus.ui.skins import Skin
from nparseplus.ui.theme import Palette, palette

# -- the object-name contract ---------------------------------------------------
# A chrome stylesheet targets these names; ``ui/chromewidgets`` stamps them.
# Keeping them here (rather than as string literals at both ends) is what lets
# a test assert that every name the sheet styles is a name some widget wears.

ROOT = "ChromeRoot"  # a window or dialog's root widget
HINT = "ChromeHint"  # de-emphasised caption under a field
CAPTION = "ChromeCaption"  # SECTION CAPS above a group
TITLE = "ChromeTitle"  # a chrome window's own heading
BADGE = "ChromeBadge"  # a status pill (the version/update badge)
CARD = "ChromeCard"  # a pickable tile (the skin chooser)
SIDEBAR = "ChromeSidebar"  # the settings page list
PRIMARY = "ChromePrimary"  # the one emphasised button in a row

#: Dynamic properties the sheet keys off. Flipping one needs a ``repolish``.
PROP_TONE = "tone"  # ChromeBadge: "" | "ok" | "warn" | "danger" | "busy"
PROP_SELECTED = "selected"  # ChromeCard: bool

#: Every tone :func:`badge_rules` must define, in severity order.
BADGE_TONES = ("ok", "warn", "danger", "busy")

# -- shared layout ---------------------------------------------------------------
# Plain px on purpose: these are gutters and touch targets, not type, so they
# must not grow with the user's font size the way skins.px() values do.

PAGE_MARGINS = (10, 10, 10, 10)
ROW_SPACING = 6
SECTION_SPACING = 12

# -- semantic accents ------------------------------------------------------------
# Named for what they MEAN, not what they look like. Several of these share a
# hex today (a zone exit and a beneficial spell are both #2f9e6e) and that is
# a coincidence worth preserving the ability to break: giving each site the
# token it means is what lets the map palette change without repainting the
# spell window.

GOOD = "#2f9e6e"  # beneficial, up-to-date, a reachable zone exit
BAD = "#c0392b"  # detrimental, failed
COOLDOWN = "#3a7bd5"
TIMER = "#8e5bd1"
ROLL = "#d99b2b"  # a /random roll; also the "wants attention" badge
LINK = "#9ecfff"  # a clickable URL in rendered HTML


@dataclass(frozen=True)
class Chrome:
    """What a :class:`Skin` and a :class:`Palette` together imply for a
    config surface.

    Derived once by :func:`chrome_for` and handed to every builder, so the
    derivation is tested in one place instead of re-argued per stylesheet.
    """

    # -- hue, from the skin ----------------------------------------------
    accent: str  # selection, focus, group titles, hairlines
    accent_text: str  # text that sits ON the accent band
    band: tuple[str, ...]  # the selection band's fill
    rule: str  # hairline / border between sections

    # -- value, from the palette -----------------------------------------
    surface: str
    surface_alt: str
    text: str
    heading: str
    caption: str
    hint: str
    disabled: str
    field_bg: str
    field_text: str
    field_border: str

    # -- semantic, fixed --------------------------------------------------
    ok: str
    warn: str
    danger: str


def chrome_for(skin: Skin, colors: Palette | None = None) -> Chrome:
    """Resolve a skin + theme into the tokens a config surface needs.

    ``skin`` is expected to be theme-resolved already (``skins.skin()``), which
    is what darkens ``chrome_accent`` under the light theme; passing a raw skin
    is fine for previews.
    """
    colors = colors if colors is not None else palette()
    return Chrome(
        accent=skin.chrome_accent,
        # The band is dark under every skin, so its text is the skin's own
        # caps color rather than the palette's — this is the one place the
        # skin outranks the theme, because the theme is not what is behind it.
        accent_text=skin.title_color,
        band=skin.chrome_band,
        rule=skin.glass_border,
        surface=colors.surface,
        surface_alt=colors.surface_alt,
        text=colors.text,
        heading=colors.heading,
        caption=skin.chrome_accent,
        hint=colors.hint,
        disabled=colors.disabled,
        # These already mean "an input field in this theme"; see theme.py.
        field_bg=colors.map_input_bg,
        field_text=colors.map_input_text,
        field_border=colors.map_input_border,
        ok=GOOD,
        warn=ROLL,
        danger=BAD,
    )
