"""Overlay skins — the frame, type hierarchy and bar geometry of every
in-fight overlay, driven by one ``general.skin`` setting.

A skin is a bigger :class:`~nparseplus.ui.theme.Palette`. Where the palette
answers "what color is body text" — the readability floor, one set of values
for the whole app — a skin answers "what does the window's edge look like,
how loud is the title, is the progress bar a thin rule under the row or the
row's own background". Three ship:

``duxa``
    Thin double-line frame over flat black glass with tan caps — what a
    P99 raider running DuxaUI already has on screen, so nParse+ stops
    being the odd window out. The default.
``velious``
    The full classic frame: a beveled stone plate with notched corners,
    gems in recessed sockets, engraved gold caps. Loudest, biggest.
``ledger``
    The Duxa frame, but the bar *is* the row — a draining block behind the
    name. Least to scan mid-pull; reads from the corner of the eye.

This module is deliberately Qt-free (like ``theme.py``): everything here is
either data or a pure function producing a stylesheet string, so the whole
skin layer is unit-testable without a live window. The painted parts a
stylesheet cannot express — the Velious corner notches, the Ledger full-row
bar — live in ``ui/skinwidgets.py``.

Sizes are stored as multipliers of ``settings.general.font_size`` rather
than px, so the user's font-size choice keeps working under every skin.
"""

from __future__ import annotations

from dataclasses import dataclass

from nparseplus.ui.theme import Palette

# Row-kind accents, shared with ``ui.spellwindow``'s bar colors. A skin tints
# its group headers from these so a header and the bars under it agree.
KIND_YOU = "you"
KIND_PLAYER = "player"
KIND_DETRIMENTAL = "detrimental"
KIND_TIMER = "timer"
KIND_COOLDOWN = "cooldown"
KIND_ROLL = "roll"

#: Every group-header kind a skin must define a tint for.
HEADER_KINDS = (KIND_YOU, KIND_PLAYER, KIND_DETRIMENTAL, KIND_TIMER, KIND_COOLDOWN, KIND_ROLL)

# The app registers the bundled regular and bold faces before constructing
# any windows (``app.create_app``). Naming the family in every role keeps an
# overlay deterministic when the desktop's default font differs by platform.
NOTO_SANS = "Noto Sans"


@dataclass(frozen=True)
class TypographyRole:
    """A Qt-free type token resolved from ``general.font_size``.

    ``tracking_em`` is stored proportionally and converted to px by
    :func:`typography_style`, because Qt stylesheets do not understand em.
    Capitalization deliberately is not part of the token: QSS has no
    ``text-transform``, so display widgets use ``skinwidgets.set_caps`` while
    keeping their model text untouched.
    """

    scale: float
    weight: str = "normal"
    tracking_em: float = 0.0


# Shared roles for values that do not need a skin-specific type scale. Titles,
# group headers and alert kickers construct a role from their Skin tokens.
SMALL_DISPLAY = TypographyRole(scale=0.78, weight="bold", tracking_em=0.18)
BODY_TEXT = TypographyRole(scale=0.84)
NUMERIC_TEXT = TypographyRole(scale=1.05, weight="bold")


@dataclass(frozen=True)
class HeaderTint:
    """One group header's coloring: text plus (optionally) a tinted band."""

    text: str
    band: str = ""  # "" = no fill (Ledger draws headers as bare caps)
    rule: str = ""  # top hairline over the band


@dataclass(frozen=True)
class Skin:
    """A complete overlay chrome. All colors are literal."""

    name: str
    label: str
    blurb: str

    # -- frame -----------------------------------------------------------
    #: Outer plate fill. Two colors = a vertical gradient (Velious stone).
    plate: tuple[str, ...]
    plate_border: str
    #: Gap between the outer plate and the inner glass, in px.
    plate_padding: int
    #: Corner notch size in px (0 = square corners). Painted, not CSS.
    notch: int
    #: Inner glass fill; two colors = a vertical gradient.
    glass: tuple[str, ...]
    glass_border: str
    #: Ambient drop shadow under the plate (0 = none).
    shadow_blur: int

    # -- title bar -------------------------------------------------------
    title_fill: tuple[str, ...]
    title_rule: str  # hairline under the title bar
    title_color: str
    title_scale: float
    title_tracking: float  # letter-spacing in em
    title_highlight: str  # inset top highlight ("" = none)
    #: The little rotated-square gem beside the title ("" = no mark).
    mark_color: str
    mark_size: int
    mark_glow: str

    # -- config surfaces (Settings, the editors) -------------------------
    #: The one hue a skin lends the chrome: selection bands, focus rings,
    #: group titles, hairlines. Its own field rather than ``mark_color``
    #: because Ledger has no gem, and ``title_color`` because Duxa's gem and
    #: caps deliberately differ. ``ui/chrome.py`` owns everything downstream.
    chrome_accent: str
    #: Fill behind a selected sidebar row. Not derived from ``title_fill``:
    #: Ledger's is transparent, which would make the selection invisible.
    chrome_band: tuple[str, ...]

    # -- group headers ---------------------------------------------------
    header_scale: float
    header_tracking: float
    header_pad: tuple[int, int, int, int]  # top right bottom left
    header_inset: int  # horizontal margin of the band inside the glass
    header_tints: dict[str, HeaderTint]

    # -- rows ------------------------------------------------------------
    #: ``"stacked"`` = name/value over a thin bar (Duxa, Velious);
    #: ``"full"`` = the bar is the row's background (Ledger).
    row_style: str
    row_height: int  # only meaningful for "full"
    row_pad: tuple[int, int, int, int]
    name_scale: float
    name_color: str
    value_scale: float
    value_color: str
    value_shadow: bool
    icon_size: int
    bar_height: int
    bar_track: str
    bar_track_border: str
    #: Bar fill stops as fractions of the way from a lightened tint of the
    #: row color to a darkened one — see :func:`bar_fill_stops`.
    bar_lighten: float
    bar_darken: float
    #: Left rule width for "full" rows (0 for stacked skins).
    row_rule: int

    # -- the event overlay (sits directly on the game) -------------------
    #: Kicker caps above the big alert word ("GORENAIRE" over "ENRAGED").
    alert_kicker_color: str
    alert_kicker_scale: float
    alert_kicker_tracking: float
    #: The hairline under the alert, fading out at both ends.
    alert_rule_color: str
    alert_rule_width: int
    #: Marks flanking the kicker ("" = none).
    alert_mark: str
    #: Timer-bar chrome on the event overlay.
    overlay_bar_height: int
    overlay_bar_bg: str
    overlay_bar_border: str
    overlay_bar_style: str  # "boxed" (Duxa/Velious) or "full" (Ledger)
    #: The CH chain lane's own plate. Separate from ``overlay_bar_bg``, which
    #: is transparent on Ledger — a lane that vanishes is not a lane.
    lane_bg: str
    lane_border: str
    #: Small opaque plates over the game: the Utility header, region chips,
    #: the edit hint. The header tints are alpha-0.14 bands tuned to sit on
    #: black glass, not on Norrath, so these get their own pair.
    overlay_chip_fill: str
    overlay_chip_text: str


def _tints(you: str, player: str, detrimental: str, timer: str, band: bool) -> dict:
    """Header tints for one skin. ``band`` off = bare caps (Ledger)."""

    def tint(text: str, rgb: str) -> HeaderTint:
        if not band:
            return HeaderTint(text=text)
        return HeaderTint(text=text, band=f"rgba({rgb}, 0.14)", rule=f"rgba({rgb}, 0.34)")

    return {
        KIND_YOU: tint(you, "107, 90, 58"),
        KIND_PLAYER: tint(player, "107, 90, 58"),
        KIND_DETRIMENTAL: tint(detrimental, "192, 57, 43"),
        KIND_TIMER: tint(timer, "142, 91, 209"),
        KIND_COOLDOWN: tint(player, "58, 123, 213"),
        KIND_ROLL: tint(you, "217, 155, 43"),
    }


DUXA = Skin(
    name="duxa",
    label="Duxa",
    blurb="Thin double-line frame, black glass, tan caps. Matches a Duxa-skinned client.",
    plate=("rgba(6, 7, 10, 219)",),
    plate_border="#6b5a3a",
    plate_padding=2,
    notch=0,
    glass=("transparent",),
    glass_border="#2b2519",
    shadow_blur=0,
    title_fill=("rgba(107, 90, 58, 77)", "rgba(107, 90, 58, 15)"),
    title_rule="#3a3122",
    title_color="#d4b675",
    title_scale=0.80,
    title_tracking=0.20,
    title_highlight="",
    mark_color="#c8a951",
    mark_size=5,
    mark_glow="",
    chrome_accent="#c8a951",
    chrome_band=("rgba(107, 90, 58, 77)", "rgba(107, 90, 58, 15)"),
    header_scale=0.80,
    header_tracking=0.16,
    header_pad=(3, 7, 2, 7),
    header_inset=0,
    header_tints=_tints("#d4b675", "#d4b675", "#efb8ae", "#c6b3ea", band=True),
    row_style="stacked",
    row_height=0,
    row_pad=(3, 7, 4, 7),
    name_scale=0.96,
    name_color="#cfd3e5",
    value_scale=1.04,
    value_color="#f3f5fe",
    value_shadow=False,
    icon_size=15,
    bar_height=4,
    bar_track="#0b0c0f",
    bar_track_border="#2b2519",
    bar_lighten=0.22,
    bar_darken=0.0,
    row_rule=0,
    alert_kicker_color="#c8a951",
    alert_kicker_scale=0.78,
    alert_kicker_tracking=0.28,
    alert_rule_color="rgba(200, 169, 81, 128)",
    alert_rule_width=170,
    alert_mark="",
    overlay_bar_height=19,
    overlay_bar_bg="rgba(6, 7, 10, 199)",
    overlay_bar_border="#6b5a3a",
    overlay_bar_style="boxed",
    lane_bg="rgba(6, 7, 10, 199)",
    lane_border="#6b5a3a",
    overlay_chip_fill="rgba(58, 123, 213, 0.780)",
    overlay_chip_text="#e4e7f5",
)

VELIOUS = Skin(
    name="velious",
    label="Velious plate",
    blurb="Beveled stone frame, notched corners, gems in sockets, engraved gold.",
    plate=("#3a3122", "#241e14"),
    plate_border="#241e14",
    plate_padding=3,
    notch=9,
    glass=("rgba(10, 11, 15, 240)", "rgba(6, 7, 10, 229)"),
    glass_border="#6b5a3a",
    shadow_blur=30,
    title_fill=("#5c4d31", "#332a1c"),
    title_rule="#14110b",
    title_color="#f0dcae",
    title_scale=0.84,
    title_tracking=0.26,
    title_highlight="rgba(212, 182, 117, 89)",
    mark_color="#e2c882",
    mark_size=7,
    mark_glow="rgba(226, 200, 130, 178)",
    chrome_accent="#e2c882",
    chrome_band=("#5c4d31", "#332a1c"),
    header_scale=0.84,
    header_tracking=0.20,
    header_pad=(3, 7, 3, 7),
    header_inset=5,
    header_tints={
        KIND_YOU: HeaderTint("#f0dcae", "rgba(92, 77, 49, 140)", "rgba(226, 200, 130, 82)"),
        KIND_PLAYER: HeaderTint("#f0dcae", "rgba(92, 77, 49, 140)", "rgba(226, 200, 130, 82)"),
        KIND_DETRIMENTAL: HeaderTint(
            "#f6d3cc", "rgba(192, 57, 43, 115)", "rgba(240, 140, 124, 102)"
        ),
        KIND_TIMER: HeaderTint("#e0d3f7", "rgba(142, 91, 209, 107)", "rgba(178, 140, 232, 102)"),
        KIND_COOLDOWN: HeaderTint("#d5e5fb", "rgba(58, 123, 213, 107)", "rgba(140, 185, 245, 89)"),
        KIND_ROLL: HeaderTint("#f0dcae", "rgba(217, 155, 43, 115)", "rgba(240, 200, 130, 89)"),
    },
    row_style="stacked",
    row_height=0,
    row_pad=(5, 9, 6, 9),
    name_scale=1.04,
    name_color="#e4e7f5",
    value_scale=1.25,
    value_color="#f3f5fe",
    value_shadow=True,
    icon_size=20,
    bar_height=6,
    bar_track="#07080b",
    bar_track_border="#3a3122",
    bar_lighten=0.34,
    bar_darken=0.30,
    row_rule=0,
    alert_kicker_color="#c8a951",
    alert_kicker_scale=0.80,
    alert_kicker_tracking=0.32,
    alert_rule_color="rgba(226, 200, 130, 153)",
    alert_rule_width=230,
    alert_mark="#e2c882",
    overlay_bar_height=22,
    overlay_bar_bg="rgba(6, 7, 10, 184)",
    overlay_bar_border="#6b5a3a",
    overlay_bar_style="boxed",
    lane_bg="rgba(10, 11, 15, 220)",
    lane_border="#6b5a3a",
    overlay_chip_fill="rgba(92, 77, 49, 0.860)",
    overlay_chip_text="#f0dcae",
)

LEDGER = Skin(
    name="ledger",
    label="Ledger",
    blurb="Duxa frame, full-row draining bars. Fastest peripheral read.",
    plate=("rgba(6, 7, 10, 224)",),
    plate_border="#6b5a3a",
    plate_padding=2,
    notch=0,
    glass=("transparent",),
    glass_border="#2b2519",
    shadow_blur=0,
    title_fill=("transparent",),
    title_rule="#2b2519",
    title_color="#8a7549",
    title_scale=0.80,
    title_tracking=0.22,
    title_highlight="",
    mark_color="",
    mark_size=0,
    mark_glow="",
    # No gem and a transparent title fill — exactly why these are fields.
    chrome_accent="#8a7549",
    chrome_band=("rgba(107, 90, 58, 56)",),
    header_scale=0.76,
    header_tracking=0.18,
    header_pad=(7, 8, 2, 8),
    header_inset=0,
    header_tints=_tints("#d4b675", "#5cc79a", "#ef9184", "#b28ce8", band=False),
    row_style="full",
    row_height=32,
    row_pad=(0, 8, 0, 8),
    name_scale=1.0,
    name_color="#e4e7f5",
    value_scale=1.33,
    value_color="#f3f5fe",
    value_shadow=True,
    icon_size=16,
    bar_height=0,
    bar_track="transparent",
    bar_track_border="",
    bar_lighten=0.0,
    bar_darken=0.0,
    row_rule=3,
    alert_kicker_color="#8a7549",
    alert_kicker_scale=0.76,
    alert_kicker_tracking=0.26,
    alert_rule_color="",
    alert_rule_width=0,
    alert_mark="",
    overlay_bar_height=26,
    overlay_bar_bg="transparent",
    overlay_bar_border="",
    overlay_bar_style="full",
    lane_bg="rgba(6, 7, 10, 179)",
    lane_border="#3a3122",
    overlay_chip_fill="rgba(6, 7, 10, 0.800)",
    overlay_chip_text="#d4b675",
)

SKINS: dict[str, Skin] = {skin.name: skin for skin in (DUXA, VELIOUS, LEDGER)}
#: Menu/settings order — the doc's own order, plainest first.
SKIN_ORDER = ("duxa", "velious", "ledger")
DEFAULT_SKIN = "duxa"

_current = DUXA


def set_skin(name: str) -> None:
    """Select the active skin; unknown names fall back to the default."""
    global _current
    _current = SKINS.get(name, SKINS[DEFAULT_SKIN])


def skin() -> Skin:
    """The active skin.

    There is one palette (see ``theme.py``), so a skin needs no per-theme
    adaptation: what is declared above is what every surface gets.
    """
    return _current


# -- pure color/geometry helpers ------------------------------------------------


def _hex_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def shade(color: str, amount: float) -> str:
    """Lighten (``amount`` > 0) or darken (< 0) a ``#rrggbb`` color.

    A plain channel-wise mix toward white/black. Used for the bar fill's
    top-lit / bottom-shaded gradient, where a hue-preserving blend is the
    point (unlike ``spellwindow.fade_color``, which deliberately travels the
    hue arc toward red).
    """

    def mix(channel: int) -> int:
        moved = channel + (255 - channel) * amount if amount >= 0 else channel * (1 + amount)
        return max(0, min(255, round(moved)))

    red, green, blue = (mix(channel) for channel in _hex_rgb(color))
    return f"#{red:02x}{green:02x}{blue:02x}"


def rgba(color: str, alpha: float) -> str:
    """``#rrggbb`` -> ``rgba(r, g, b, a)`` with ``alpha`` in 0..1."""
    red, green, blue = _hex_rgb(color)
    return f"rgba({red}, {green}, {blue}, {max(0.0, min(1.0, alpha)):.3f})"


def base_color(stops: tuple[str, ...]) -> str:
    """The first stop of a fill as ``#rrggbb``, whatever notation it uses.

    A skin's ``plate``/``glass`` may be hex, ``rgba(...)`` or ``transparent``;
    callers that need a *color* rather than a fill (the map chrome derives its
    ink from ``plate``) want one answer, not three cases. Alpha is dropped —
    that is the point, since the caller is about to pick its own.
    """
    if not stops:
        return "#000000"
    value = stops[0].strip()
    if value.startswith("#"):
        red, green, blue = _hex_rgb(value)
        return f"#{red:02x}{green:02x}{blue:02x}"
    if value.startswith("rgba(") or value.startswith("rgb("):
        parts = value[value.index("(") + 1 : value.rindex(")")].split(",")
        red, green, blue = (max(0, min(255, int(float(part)))) for part in parts[:3])
        return f"#{red:02x}{green:02x}{blue:02x}"
    return "#000000"  # "transparent" and anything unparseable


def gradient(stops: tuple[str, ...], horizontal: bool = False) -> str:
    """A Qt stylesheet fill for 1..n color stops (1 stop = a flat color)."""
    if not stops:
        return "transparent"
    if len(stops) == 1:
        return stops[0]
    axis = "x1: 0, y1: 0, x2: 1, y2: 0" if horizontal else "x1: 0, y1: 0, x2: 0, y2: 1"
    steps = ", ".join(
        f"stop: {index / (len(stops) - 1):.3f} {color}" for index, color in enumerate(stops)
    )
    return f"qlineargradient({axis}, {steps})"


def bar_fill(skin_: Skin, color: str) -> str:
    """The chunk fill for a bar of ``color`` under ``skin_``.

    Duxa lifts the top edge only; Velious is a three-stop lit-to-shaded
    gem; Ledger's bars are painted, not styled, so it returns the flat color.
    """
    if skin_.row_style == "full":
        return color
    stops = [shade(color, skin_.bar_lighten), color]
    if skin_.bar_darken:
        stops.append(shade(color, -skin_.bar_darken))
    return gradient(tuple(stops))


def px(base_font_size: int, scale: float, minimum: int = 7) -> int:
    """A skin size multiplier resolved against the user's font size."""
    return max(minimum, round(base_font_size * scale))


def tracking(base_font_size: int, scale: float, em: float) -> str:
    """``letter-spacing`` in px for a size expressed in em (Qt has no em)."""
    return f"{px(base_font_size, scale) * em:.2f}px"


def typography_style(base_font_size: int, role: TypographyRole, *, color: str | None = None) -> str:
    """QSS declarations for one typography role.

    This is intentionally a declaration body rather than a selector so every
    overlay can compose it with its own colors/background/borders without
    duplicating the family, scaling, weight, or tracking rules.
    """
    style = (
        f'font-family: "{NOTO_SANS}";'
        f" font-size: {px(base_font_size, role.scale)}px;"
        f" font-weight: {role.weight};"
    )
    if role.tracking_em:
        style += f" letter-spacing: {tracking(base_font_size, role.scale, role.tracking_em)};"
    if color is not None:
        style += f" color: {color};"
    return style


def full_row_height(skin_: Skin, base_font_size: int) -> int:
    """Height for a painted full row without clipping user-scaled type.

    ``Skin.row_height`` remains the default-geometry floor. Above that, the
    largest row role gets a conservative line box plus the skin's vertical
    padding. Stacked skins hug their layouts and therefore do not use this.
    """
    top, _right, bottom, _left = skin_.row_pad
    text_height = round(px(base_font_size, max(skin_.name_scale, skin_.value_scale)) * 1.25)
    content_height = max(text_height, skin_.icon_size) + top + bottom + 4
    return max(skin_.row_height, content_height)


# -- stylesheet builders --------------------------------------------------------


def title_style(skin_: Skin, font_size: int) -> str:
    """QSS body for a window's title caps."""
    return (
        typography_style(
            font_size,
            TypographyRole(skin_.title_scale, "bold", skin_.title_tracking),
            color=skin_.title_color,
        )
        + " background: transparent;"
    )


def title_bar_style(skin_: Skin, font_size: int) -> str:
    """QSS for the skinned title strip and the muted count at its right edge.

    Qt stylesheets have no inset box-shadow, so Velious's lit top bevel is
    drawn as a 1px top border in the highlight color — same read, one rule.
    """
    rules = (
        f"#SkinTitleBar {{ background: {gradient(skin_.title_fill)};"
        f" border-bottom: 1px solid {skin_.title_rule};"
    )
    if skin_.title_highlight:
        rules += f" border-top: 1px solid {skin_.title_highlight};"
    rules += " }"
    rules += (
        f"#SkinTitleCount {{ color: {skin_.plate_border};"
        f" {typography_style(font_size, TypographyRole(skin_.header_scale))}"
        " background: transparent; }"
    )
    return rules


def header_style(skin_: Skin, font_size: int, kind: str) -> str:
    """QSS body for one group header, tinted by its row kind."""
    tint = skin_.header_tints.get(kind, skin_.header_tints[KIND_PLAYER])
    top, right, bottom, left = skin_.header_pad
    style = (
        typography_style(
            font_size,
            TypographyRole(skin_.header_scale, "bold", skin_.header_tracking),
            color=tint.text,
        )
        + f" padding: {top}px {right}px {bottom}px {left}px;"
    )
    if tint.band:
        style += (
            f" background-color: {tint.band};"
            f" border-top: 1px solid {tint.rule};"
            " border-bottom: 1px solid rgba(0, 0, 0, 0.5);"
        )
    else:
        style += " background: transparent;"
    return style


def row_bar_style(skin_: Skin, color: str) -> str:
    """QSS for a stacked-row ``QProgressBar`` painted in ``color``."""
    border = f"1px solid {skin_.bar_track_border}" if skin_.bar_track_border else "none"
    return (
        f"QProgressBar {{ background-color: {skin_.bar_track}; border: {border};"
        " border-radius: 0px; }"
        f"QProgressBar::chunk {{ background: {bar_fill(skin_, color)}; }}"
    )


def scrollbar_style() -> str:
    """The thin, chrome-free scrollbar every skinned overlay shares."""
    return (
        "QScrollArea { background: transparent; border: none; }"
        "QScrollArea > QWidget > QWidget { background: transparent; }"
        "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
        "QScrollBar::handle:vertical {"
        " background: rgba(136, 136, 136, 120); border-radius: 3px; min-height: 20px; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        "QSizeGrip { background: transparent; width: 12px; height: 12px; }"
    )


def overlay_window_style(skin_: Skin, colors: Palette, font_size: int) -> str:
    """The whole stylesheet for a skinned overlay window.

    One string so a window's ``apply_skin`` is a single ``setStyleSheet``;
    per-widget accents (headers, bars) are set on the widgets themselves.
    """
    row_name = typography_style(font_size, TypographyRole(skin_.name_scale), color=skin_.name_color)
    row_value = typography_style(
        font_size, TypographyRole(skin_.value_scale, "bold"), color=skin_.value_color
    )
    return (
        f'QWidget {{ font-family: "{NOTO_SANS}"; }}'
        f"QLabel {{ {typography_style(font_size, BODY_TEXT, color=colors.text)}"
        " background: transparent; }"
        f"#SkinTitle {{ {title_style(skin_, font_size)} }}"
        f"#SkinRowName {{ {row_name}"
        " background: transparent; }"
        f"#SkinRowValue {{ {row_value}"
        " background: transparent; }" + scrollbar_style()
    )
