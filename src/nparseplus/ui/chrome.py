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

from nparseplus.ui.skins import (
    NOTO_SANS,
    SMALL_DISPLAY,
    Skin,
    TypographyRole,
    base_color,
    gradient,
    rgba,
    typography_style,
)
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
#: Body text on a config surface. Full size, unlike the overlays' BODY_TEXT —
#: an overlay row is glanced at over a raid and wants to be compact, a settings
#: form is read at a desk and wants to be legible.
CHROME_BODY = TypographyRole(scale=1.0)

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
# A respawn row inside its variable "pop" window (#125). Orange at hue ~25
# deg sits far enough from FADE_TARGET that the red fade still visibly runs
# across the window, and far enough from ROLL to stay a distinct meaning.
POP_WINDOW = "#e07b39"
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


def ground_rules(ch: Chrome, font_size: int) -> str:
    """The window's own ground and default type.

    MUST be emitted before every type-selector rule below. ``QWidget`` and
    ``QLineEdit`` match a QLineEdit with equal specificity, so within one
    sheet the later rule wins — put this last and every text field turns into
    the page background.
    """
    return (
        f"QWidget {{ background-color: {ch.surface}; color: {ch.text};"
        f" {typography_style(font_size, CHROME_BODY)} }}"
    )


def group_rules(ch: Chrome, font_size: int) -> str:
    """QSS for a titled section box."""
    # margin-top makes room for the title, which is drawn in the margin;
    # the paddings keep the box's own content clear of the border on every
    # side. Too little bottom padding and the last row in a group is clipped
    # by the next group's edge.
    return (
        f"QGroupBox {{ border: 1px solid {ch.rule}; border-radius: 3px;"
        f" margin-top: {SECTION_SPACING}px;"
        f" padding: {SECTION_SPACING}px {ROW_SPACING}px {ROW_SPACING}px {ROW_SPACING}px;"
        f" background: {ch.surface_alt}; }}"
        "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;"
        f" left: {ROW_SPACING}px; padding: 0 4px;"
        f" {typography_style(font_size, SMALL_DISPLAY, color=ch.accent)} }}"
    )


def field_rules(ch: Chrome, font_size: int) -> str:
    """QSS for every text/number/choice input.

    The focus ring is the skin's accent — the one place a config form shows
    which skin is active while you are actually using it.
    """
    inputs = (
        "QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QComboBox"
    )
    return (
        f"{inputs} {{ background-color: {ch.field_bg}; color: {ch.field_text};"
        f" border: 1px solid {ch.field_border}; border-radius: 3px; padding: 2px 4px;"
        f" selection-background-color: {ch.accent}; selection-color: {PILL_TEXT}; }}"
        f"{_focus(inputs)} {{ border: 1px solid {ch.accent}; }}"
        f"{_disabled(inputs)} {{ color: {ch.disabled}; }}"
        f"QComboBox QAbstractItemView {{ background-color: {ch.field_bg};"
        f" color: {ch.field_text}; border: 1px solid {ch.field_border};"
        f" selection-background-color: {ch.accent}; selection-color: {PILL_TEXT}; }}"
    )


def button_rules(ch: Chrome, font_size: int) -> str:
    """QSS for push buttons, including the one emphasised per row."""
    return (
        f"QPushButton {{ background-color: {ch.surface_alt}; color: {ch.text};"
        f" border: 1px solid {ch.field_border}; border-radius: 3px;"
        f" padding: 3px 10px; {typography_style(font_size, CHROME_BODY)} }}"
        f"QPushButton:hover {{ border: 1px solid {ch.accent}; }}"
        f"QPushButton:pressed {{ background-color: {ch.field_bg}; }}"
        f"QPushButton:disabled {{ color: {ch.disabled};"
        f" border: 1px solid {ch.field_border}; }}"
        f"QPushButton#{PRIMARY} {{ border: 1px solid {ch.accent}; color: {ch.heading}; }}"
        f"QPushButton#{PRIMARY}:hover {{ background-color: {gradient(ch.band)}; }}"
    )


def view_rules(ch: Chrome, font_size: int) -> str:
    """QSS for the list/tree/table views — plugin tables, trigger trees."""
    views = "QListWidget, QTreeWidget, QTableWidget, QTreeView, QTableView, QListView"
    return (
        f"{views} {{ background-color: {ch.field_bg}; color: {ch.text};"
        f" border: 1px solid {ch.field_border}; border-radius: 3px;"
        f" alternate-background-color: {ch.surface_alt}; }}"
        f"{_selected(views)} {{ background-color: {gradient(ch.band)};"
        f" color: {ch.accent_text}; }}"
        f"QHeaderView::section {{ background-color: {ch.surface_alt}; color: {ch.accent};"
        f" border: none; border-bottom: 1px solid {ch.rule}; padding: 3px 6px;"
        f" {typography_style(font_size, SMALL_DISPLAY)} }}"
        "QHeaderView { background: transparent; }"
    )


def tab_rules(ch: Chrome, font_size: int) -> str:
    """QSS for tab bars — the trigger editor and macro editor use them."""
    return (
        f"QTabWidget::pane {{ border: 1px solid {ch.rule}; border-radius: 3px;"
        f" background: {ch.surface}; }}"
        f"QTabBar::tab {{ background: transparent; color: {ch.hint};"
        f" padding: 4px 12px; border-bottom: 2px solid transparent;"
        f" {typography_style(font_size, SMALL_DISPLAY)} }}"
        f"QTabBar::tab:selected {{ color: {ch.accent};"
        f" border-bottom: 2px solid {ch.accent}; }}"
        f"QTabBar::tab:hover {{ color: {ch.text}; }}"
    )


def sidebar_rules(ch: Chrome, font_size: int) -> str:
    """QSS for the settings window's page list."""
    return (
        f"#{SIDEBAR} {{ background-color: {ch.surface_alt}; color: {ch.text};"
        f" border: none; border-right: 1px solid {ch.rule}; }}"
        f"#{SIDEBAR}::item {{ padding: 5px 8px; border: none;"
        f" {typography_style(font_size, CHROME_BODY)} }}"
        f"#{SIDEBAR}::item:selected {{ background: {gradient(ch.band)};"
        f" color: {ch.accent_text}; }}"
        f"#{SIDEBAR}::item:hover:!selected {{ color: {ch.accent}; }}"
    )


def slider_rules(ch: Chrome) -> str:
    """QSS for the opacity sliders."""
    return (
        f"QSlider::groove:horizontal {{ background: {ch.field_bg};"
        f" border: 1px solid {ch.field_border}; height: 4px; border-radius: 2px; }}"
        f"QSlider::handle:horizontal {{ background: {ch.accent};"
        " width: 12px; margin: -5px 0; border-radius: 3px; }"
    )


def misc_rules(ch: Chrome) -> str:
    """Checkboxes, radios, splitters and the scroll areas that hold pages.

    The indicators are drawn here rather than left to Fusion. Fusion renders
    them from the palette, and this palette's Base is near-black, so an
    UNCHECKED box came out invisible against the page — a settings row that
    reads as a label with nothing to click. Checked is a filled accent square
    rather than a glyph: no image asset, and it stays legible at any font size.
    """
    box = "13px"
    return (
        f"QCheckBox, QRadioButton {{ background: transparent; color: {ch.text};"
        " spacing: 6px; }"
        f"QCheckBox:disabled, QRadioButton:disabled {{ color: {ch.disabled}; }}"
        f"QCheckBox::indicator, QRadioButton::indicator {{ width: {box}; height: {box};"
        f" background-color: {ch.field_bg}; border: 1px solid {ch.field_border}; }}"
        "QCheckBox::indicator { border-radius: 2px; }"
        "QRadioButton::indicator { border-radius: 7px; }"
        "QCheckBox::indicator:hover, QRadioButton::indicator:hover"
        f" {{ border: 1px solid {ch.accent}; }}"
        "QCheckBox::indicator:checked, QRadioButton::indicator:checked"
        f" {{ background-color: {ch.accent}; border: 1px solid {ch.accent}; }}"
        "QCheckBox::indicator:disabled, QRadioButton::indicator:disabled"
        f" {{ border: 1px solid {ch.disabled}; }}"
        "QScrollArea { background: transparent; border: none; }"
        f"QSplitter::handle {{ background: {ch.rule}; }}"
    )


def scrollbar_rules(ch: Chrome, width: int = 10) -> str:
    """QSS for the chrome scrollbars.

    Wider than the overlays' 6px: these are dragged with a mouse in a settings
    form, not glanced at over a raid.
    """
    return (
        f"QScrollBar:vertical {{ background: transparent; width: {width}px; margin: 0; }}"
        f"QScrollBar:horizontal {{ background: transparent; height: {width}px; margin: 0; }}"
        f"QScrollBar::handle {{ background: {ch.field_border}; border-radius: {width // 2}px;"
        " min-height: 24px; min-width: 24px; }"
        f"QScrollBar::handle:hover {{ background: {ch.accent}; }}"
        "QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
    )


#: Selectors ``app_stylesheet`` is allowed to carry beyond ``#Id`` ones.
#: Menus and tooltips are top-level windows, so a window-scoped sheet never
#: reaches them; everything else must stay window-scoped or it lands on the
#: overlays. :func:`app_stylesheet` is tested against exactly this set.
APP_SCOPE_ALLOWLIST = (
    "QMenu",
    "QMenu::item",
    "QMenu::item:selected",
    "QMenu::separator",
    "QToolTip",
)


def menu_style(ch: Chrome, font_size: int) -> str:
    """QSS for menus — app scope.

    Worth its place in the app sheet for one rule block: the tray menu, the UI
    Skin submenu, the Window Layouts submenu and the spell window's own
    context menu all follow the skin from here.
    """
    return (
        f"QMenu {{ background-color: {ch.surface}; color: {ch.text};"
        f" border: 1px solid {ch.rule};"
        f" {typography_style(font_size, CHROME_BODY)} }}"
        "QMenu::item { padding: 4px 20px 4px 12px; }"
        f"QMenu::item:selected {{ background: {gradient(ch.band)}; color: {ch.accent_text}; }}"
        f"QMenu::separator {{ height: 1px; background: {ch.rule}; margin: 3px 8px; }}"
    )


def tooltip_style(ch: Chrome, font_size: int) -> str:
    """QSS for tooltips — app scope, because a tooltip is its own window and a
    window-scoped sheet never reaches one."""
    return (
        f"QToolTip {{ background-color: {ch.surface_alt}; color: {ch.text};"
        f" border: 1px solid {ch.accent}; padding: 3px;"
        f" {typography_style(font_size, CHROME_BODY)} }}"
    )


def legacy_parser_style(ch: Chrome, font_size: int) -> str:
    """The ``#ParserWindow*`` rules the legacy maps/discord chrome still reads.

    Replaces ``data/ui/_.css``, of which this was the only live part — every
    other selector in that file (``#Spell*``, ``#SettingsLabel``,
    ``#MapAreaLabel``) had zero ``setObjectName`` hits, and ``#MapCanvas`` is
    overridden on the widget itself.

    The window fill stays the literal black it has always been rather than
    ``ch.surface``: this is a legacy compatibility rule, not a design token,
    and the maps window paints transparent over it anyway.
    """
    return (
        "#ParserWindow { background-color: #000000;"
        f' font-family: "{NOTO_SANS}"; }}'
        f"#ParserWindowMoveButton {{ color: {ch.accent}; background: transparent;"
        f" border: 1px solid {ch.rule}; border-radius: 3px; padding: 1px;"
        " height: 20px; width: 20px; }"
        f"#ParserWindowMoveButton:hover {{ border: 1px solid {ch.accent}; }}"
        f"#ParserWindowTitle {{ color: {ch.accent};"
        f" {typography_style(font_size, SMALL_DISPLAY)} }}"
        f"#ParserWindowMenu QPushButton {{ color: {ch.hint}; background: transparent;"
        f" border: 1px solid {ch.rule}; border-radius: 3px; padding: 1px;"
        " height: 20px; width: 20px; }"
        f"#ParserWindowMenu QPushButton:hover {{ color: {ch.accent};"
        f" border: 1px solid {ch.accent}; }}"
        f"#ParserWindowMenu QPushButton:checked {{ color: {ch.accent_text};"
        f" background: {gradient(ch.band)}; }}"
        f"#ParserWindowMenu QLabel {{ color: {ch.text};"
        f" {typography_style(font_size, SMALL_DISPLAY)} }}"
        f"#ParserWindowMenu QSpinBox {{ color: {ch.field_text};"
        f" background-color: {ch.field_bg}; border: 1px solid {ch.field_border};"
        f" border-radius: 3px; padding: 3px;"
        f" {typography_style(font_size, CHROME_BODY)} }}"
    )


def discord_menu_style(ch: Chrome, red: int, green: int, blue: int, alpha: float) -> str:
    """QSS for the Discord overlay's hover menu strip.

    The GROUND stays the user's configured colour and opacity — that is the
    whole point of the Discord window's colour picker, and a skin has no
    business overriding a deliberate choice. Everything drawn ON it (the title,
    the move handle, the button hover and checked states) comes from the skin,
    so the strip stops being the one piece of chrome wearing 2019's darkgreen.
    """
    ground = f"rgba({red}, {green}, {blue}, {alpha})"
    return (
        f"#ParserWindowMenuReal {{ background-color: {ground}; }}"
        f"#ParserWindowMenuReal QPushButton {{"
        f" background-color: rgba({red}, {green}, {blue}, 0); }}"
        f"#ParserWindowMenu QPushButton {{ color: {rgba_of(ch.text, alpha)}; }}"
        f"#ParserWindowMenu QPushButton:hover {{ color: {ch.accent};"
        f" background: {rgba_of(ch.accent, min(alpha, 0.35))}; }}"
        f"#ParserWindowMenu QPushButton:checked {{ color: {ch.accent_text}; }}"
        f"#ParserWindowMoveButton {{ color: {rgba_of(ch.accent, alpha)}; }}"
        f"#ParserWindowTitle {{ color: {rgba_of(ch.accent, alpha)}; }}"
    )


def rgba_of(color: str, alpha: float) -> str:
    """``color`` at ``alpha`` (0..1), accepting hex or an existing rgba()."""
    return rgba(base_color((color,)), alpha)


def app_stylesheet(skin: Skin, colors: Palette, font_size: int) -> str:
    """The application-wide sheet.

    ``#Id`` selectors plus :data:`APP_SCOPE_ALLOWLIST` only. A bare type
    selector here would land on the overlays over EverQuest — see
    :func:`window_style` for why that is not merely untidy.
    """
    ch = chrome_for(skin, colors)
    return (
        legacy_parser_style(ch, font_size)
        + menu_style(ch, font_size)
        + tooltip_style(ch, font_size)
    )


def window_style(skin: Skin, colors: Palette, font_size: int) -> str:
    """The whole stylesheet for one config window.

    Scoped to a window rather than the application, deliberately: this sheet
    carries bare type selectors, and at app scope a ``QLabel`` rule here would
    land on the overlays sitting on top of EverQuest. ``skins.overlay_window_style``
    only overrides three properties on QLabel, and Qt resolves conflicts
    per-property — anything it does not name would leak through.

    Rule order is load-bearing; see :func:`ground_rules`.
    """
    ch = chrome_for(skin, colors)
    return (
        ground_rules(ch, font_size)
        + f"#{HINT} {{ {hint_style(ch, font_size)} }}"
        + f"#{CAPTION} {{ {caption_style(ch, font_size)} }}"
        + f"#{TITLE} {{ {title_style(ch, font_size)} }}"
        + badge_rules(ch, font_size)
        + card_rules(ch)
        + group_rules(ch, font_size)
        + field_rules(ch, font_size)
        + button_rules(ch, font_size)
        + view_rules(ch, font_size)
        + tab_rules(ch, font_size)
        + sidebar_rules(ch, font_size)
        + slider_rules(ch)
        + misc_rules(ch)
        + scrollbar_rules(ch)
    )


def qt_palette_spec(ch: Chrome) -> dict[str, str]:
    """QPalette role name -> color, for the Fusion style.

    Fusion honours QPalette fully and identically across platforms, so the
    parts of a control a stylesheet cannot reach without redrawing it wholesale
    (spin arrows, combo drop-downs, check indicators, tree branches) come out
    right from this instead of ~150 lines of sub-control QSS that would look
    subtly wrong on at least one platform.
    """
    return {
        "Window": ch.surface,
        "WindowText": ch.text,
        "Base": ch.field_bg,
        "AlternateBase": ch.surface_alt,
        "Text": ch.field_text,
        "Button": ch.surface_alt,
        "ButtonText": ch.text,
        "BrightText": ch.danger,
        "Highlight": ch.accent,
        "HighlightedText": PILL_TEXT,
        "ToolTipBase": ch.surface_alt,
        "ToolTipText": ch.text,
        "PlaceholderText": ch.hint,
        "Link": LINK,
    }


def _focus(selectors: str) -> str:
    return ", ".join(f"{part.strip()}:focus" for part in selectors.split(","))


def _disabled(selectors: str) -> str:
    return ", ".join(f"{part.strip()}:disabled" for part in selectors.split(","))


def _selected(selectors: str) -> str:
    return ", ".join(f"{part.strip()}::item:selected" for part in selectors.split(","))
