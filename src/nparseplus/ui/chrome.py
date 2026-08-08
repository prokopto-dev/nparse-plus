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

Type goes through skins.py's :class:`~nparseplus.ui.skins.TypographyRole` and
:func:`~nparseplus.ui.skins.typography_style`, the same mechanism the overlays
use, so a size is always a multiplier of ``general.font_size`` and the config
windows inherit the bundled Noto Sans family they never had. The one deliberate
exception is the layout constants below: those are gutters and touch targets,
not type, and must not grow with the user's font choice.
"""

from __future__ import annotations

from dataclasses import dataclass

from nparseplus.ui.skins import SMALL_DISPLAY, Skin, TypographyRole, typography_style
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

# -- type roles ------------------------------------------------------------------
# Expressed as skins.TypographyRole and rendered through skins.typography_style,
# the same mechanism the overlays use — so the config windows pick up the
# bundled Noto Sans family (which they never had) and one place still decides
# how a size multiplier becomes px. A SECTION CAPS label reuses the overlay's
# SMALL_DISPLAY outright: it is the same tracked, uppercase compact role, and
# giving it a private near-duplicate is how two surfaces drift apart.

#: Explanatory text under a field. 0.90 is the old hardcoded 11px at the
#: default font size of 12 — same look, but it now grows with the user's
#: choice instead of staying pinned.
HINT_TEXT = TypographyRole(scale=0.90)
#: A status pill's label.
BADGE_TEXT = TypographyRole(scale=0.90, weight="bold")
#: A config window's own heading.
CHROME_TITLE = TypographyRole(scale=1.15, weight="bold")

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

#: Text on a filled status pill. Every tone fill is a mid-to-dark saturated
#: color, so one value covers all of them in both themes.
PILL_TEXT = "#ffffff"


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


# -- stylesheet builders ---------------------------------------------------------
# Each returns QSS for one concern. They take an already-derived Chrome so the
# skin/palette split is resolved (and tested) once rather than per sheet.


def hint_style(ch: Chrome, font_size: int) -> str:
    """QSS body for a de-emphasised caption under a field.

    This replaced sixteen copies of a literal ``color: #888888``, which — being
    a literal — stayed dark grey under the light theme. Reading it from the
    palette is the whole fix.
    """
    return typography_style(font_size, HINT_TEXT, color=ch.hint) + " background: transparent;"


def caption_style(ch: Chrome, font_size: int) -> str:
    """QSS body for a SECTION CAPS label above a group."""
    return (
        typography_style(font_size, SMALL_DISPLAY, color=ch.caption) + " background: transparent;"
    )


def title_style(ch: Chrome, font_size: int) -> str:
    """QSS body for a chrome window's own heading."""
    return typography_style(font_size, CHROME_TITLE, color=ch.heading) + " background: transparent;"


def badge_rules(ch: Chrome, font_size: int) -> str:
    """QSS for the status pill, keyed by its ``tone`` property.

    Tones live in the sheet rather than being set inline at each call site so
    the badge re-themes on a skin change and reads correctly in the light
    theme without the caller doing anything.
    """
    # Untoned and "busy" are the same look — muted text, no pill. That is the
    # pre-check and "Checking…" states, which should not read as a result.
    quiet = (
        typography_style(font_size, HINT_TEXT, color=ch.hint)
        + " background: transparent; padding: 0;"
    )
    rules = f"#{BADGE} {{ {quiet} }}" + f'#{BADGE}[{PROP_TONE}="busy"] {{ {quiet} }}'
    pill = typography_style(font_size, BADGE_TEXT, color=PILL_TEXT)
    for tone, fill in (("ok", ch.ok), ("warn", ch.warn), ("danger", ch.danger)):
        rules += (
            f'#{BADGE}[{PROP_TONE}="{tone}"] {{ {pill}'
            f" background-color: {fill}; border-radius: 3px; padding: 1px 6px; }}"
        )
    return rules


def card_rules(ch: Chrome) -> str:
    """QSS for a pickable tile — the skin chooser's three cards."""
    return (
        f"#{CARD} {{ border: 1px solid {ch.field_border};"
        f" border-radius: 3px; background: {ch.surface_alt}; }}"
        f'#{CARD}[{PROP_SELECTED}="true"] {{ border: 2px solid {ch.accent}; }}'
    )
