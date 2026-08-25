"""The host half of ``nparseplus_sdk.skin`` — what an add-on may read.

``ui/skins.py`` and ``ui/chrome.py`` are the app's own look, and they churn:
between them they are a thousand lines of tokens and stylesheet builders that
move with every design pass. Handing that to third-party plugins wholesale —
the way ``nparseplus_sdk.events`` hands over the event catalogue, where the
class *is* the contract — would freeze all of it under the SDK's
additive-only 1.x promise.

So this module is a **façade**, deliberately narrow: one frozen snapshot
(:class:`AppSkin`) of the values a plugin needs to sit beside the Timers
window without looking like a bug report, plus the pure helpers that turn
them into a stylesheet. It is the only nParse+ code the SDK's ``skin``
module forwards to, which means the internals above stay free to move as
long as this snapshot can still be built from them.

The rule it encodes, from ``theme.py`` and ``chrome.py``:

    **the palette owns VALUE, the skin owns HUE.**

Ground, field backgrounds and body text come from the
:class:`~nparseplus.ui.theme.Palette` — the readability floor no skin may
move — and are the same under all three skins. The skin contributes one
accent, for selection bands, focus rings, group titles and hairlines.

An accent is a mark, not a ground. Body text on :attr:`AppSkin.accent`
measures 1.2:1 under Velious (gold on gold — the app's own title caps are
gold too), 1.7:1 under Duxa and 3.3:1 under Ledger: unreadable on all three,
not just the loud one. :attr:`AppSkin.band` is the ground a selection
actually wants, and it takes :attr:`AppSkin.heading` as its text like every
other ground here.

Qt-free, like the three modules it composes, so a plugin can build a
stylesheet in a unit test with no QApplication — and so importing
``nparseplus_sdk.skin`` never drags PySide6 into a plugin's import graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nparseplus.ui import chrome, skins, theme

# -- semantic accents ----------------------------------------------------------
# Re-bound from ``ui/chrome.py`` so a plugin's "this is a debuff" is the same
# red the Timers window already draws, and so the SDK's allowlist names a
# stable spelling rather than the host's module layout.

#: Beneficial, healthy, up to date.
GOOD = chrome.GOOD
#: Detrimental, failed.
BAD = chrome.BAD
#: An ability reuse timer.
COOLDOWN = chrome.COOLDOWN
#: A plain countdown (a trigger timer, a mob respawn).
TIMER = chrome.TIMER
#: A ``/random`` roll; also "wants attention".
ROLL = chrome.ROLL
#: A respawn row inside its variable "pop" window.
POP_WINDOW = chrome.POP_WINDOW
#: A clickable URL in rendered rich text.
LINK = chrome.LINK

# -- typography ----------------------------------------------------------------
# Forwarded as-is: a role is three numbers, and the three shared ones are the
# whole vocabulary an overlay row needs.

TypographyRole = skins.TypographyRole
#: Tracked uppercase caps — titles, section headers, kickers.
SMALL_DISPLAY = skins.SMALL_DISPLAY
#: Ordinary label text on an overlay.
BODY_TEXT = skins.BODY_TEXT
#: A countdown, a total, anything the eye lands on first.
NUMERIC_TEXT = skins.NUMERIC_TEXT

#: Object names :meth:`AppSkin.overlay_stylesheet` styles. Stamp one on a
#: widget with ``setObjectName`` and it wears the skin's own treatment for
#: that role without the plugin writing a single rule.
TITLE = skins.OBJ_TITLE
ROW_NAME = skins.OBJ_ROW_NAME
ROW_VALUE = skins.OBJ_ROW_VALUE

#: The skins a user can pick, in the order the app offers them.
SKIN_NAMES = skins.SKIN_ORDER

# -- pure helpers --------------------------------------------------------------

shade = skins.shade
rgba = skins.rgba
gradient = skins.gradient
px = skins.px
tracking = skins.tracking
typography_style = skins.typography_style


@dataclass(frozen=True)
class AppSkin:
    """A snapshot of what nParse+ currently looks like.

    Read it with :func:`current` at the moment you paint — never at plugin
    activation. A skin change is **live** (the tray flips it mid-fight), and
    every window's ``apply_skin()`` is called when it happens, so a cached
    snapshot is a window wearing last week's colours.

    The fields are grouped by who owns them, and that grouping is the
    contract: :attr:`text` through :attr:`track` are identical under
    every skin and safe to pair with each other, while :attr:`accent`
    through :attr:`bar_track_border` change with the user's choice and are
    safe only as an accent *on* those values — including as a ground, which
    is why even :attr:`band` takes its foreground from the value group.
    """

    #: ``"duxa"``, ``"velious"`` or ``"ledger"``.
    name: str
    #: The skin's display name, as the tray and Settings spell it.
    label: str

    # -- VALUE: the palette's, and no skin may move it --------------------
    #: Body text. The readability floor: legible on every ground below.
    text: str
    #: Emphasised text — a heading, a row that matters.
    heading: str
    #: A de-emphasised caption under a field.
    hint: str
    #: A control the user cannot reach right now.
    disabled: str
    #: A config surface's ground (a settings page, a dialog).
    surface: str
    #: A raised strip on it — a group box, a header band.
    surface_alt: str
    #: An input field's fill, its text, and its border.
    field_bg: str
    field_text: str
    field_border: str
    #: The translucent container fill an overlay's content sits on.
    panel_bg: str
    #: A progress track, as the palette sets it.
    track: str

    # -- HUE: the skin's one contribution ---------------------------------
    #: Selection bands, focus rings, group titles, hairlines. **Not** a
    #: background for text — see the module docstring.
    accent: str
    #: The fill behind a selected row, as the app's own sidebar draws it —
    #: one stop is flat, two are a vertical gradient. Its own field rather
    #: than an ``rgba(accent, ...)`` guessed per plugin: Ledger's band is a
    #: 22% wash and Velious's is opaque stone, so no single alpha serves
    #: both. Deliberately **not** paired with an accent-coloured foreground:
    #: :attr:`heading` and :attr:`text` are what stay legible on it under
    #: every skin, while the app's own caps colour measures 3.4:1 on
    #: Ledger's band and 2.9:1 on a naive tint of the accent.
    band: tuple[str, ...]
    #: The hairline between sections.
    hairline: str
    #: The outer plate fill (one stop = flat, two = a vertical gradient)
    #: and its border.
    plate: tuple[str, ...]
    plate_border: str
    #: The inner glass the content sits on, and its border.
    glass: tuple[str, ...]
    glass_border: str
    #: A small opaque plate over the game — a chip, a region label — and the
    #: text on it. Tuned to sit on Norrath rather than on black glass.
    chip_fill: str
    chip_text: str
    #: A lane's own plate on the event overlay, and its border.
    lane_bg: str
    lane_border: str
    #: A skinned bar's track and its border (``""`` = no border).
    bar_track: str
    bar_track_border: str

    # -- sizing and geometry ----------------------------------------------
    #: Height of a countdown bar drawn directly on the game, in px. Already
    #: resolved (the event overlay's bars are a fixed geometry, not a font
    #: multiple), so use it as-is for a region's own bars.
    overlay_bar_height: int
    #: The user's ``general.font_size``. Every size in the system is a
    #: multiplier of it — never write px.
    base_font_size: int
    #: Alpha of the plate and glass, 0..1. It fades the **frame only**:
    #: text, bars and icons stay at full contrast, which is the whole point
    #: of splitting it off window opacity.
    frame_opacity: float
    #: ``"stacked"`` (a thin bar under the row) or ``"full"`` (the bar is
    #: the row's background). Ledger is the one that answers ``"full"``.
    row_style: str
    #: The gap between plate and glass, in px.
    plate_padding: int
    #: The corner notch, in px (``0`` = square corners). Painted, not CSS —
    #: it is here so a plugin drawing its own frame can match it.
    notch: int

    # -- sizes -------------------------------------------------------------

    def px(self, scale: float, minimum: int = 7) -> int:
        """``scale`` resolved against the user's font size, in px."""
        return skins.px(self.base_font_size, scale, minimum)

    def tracking(self, scale: float, em: float) -> str:
        """``letter-spacing`` in px for a size expressed in em."""
        return skins.tracking(self.base_font_size, scale, em)

    def typography(self, role: skins.TypographyRole, *, color: str | None = None) -> str:
        """QSS declarations for one typography role — family, size, weight,
        tracking and (optionally) colour.

        A declaration body rather than a rule, so it composes::

            f"#Total {{ {app.typography(skin.NUMERIC_TEXT, color=app.heading)} }}"
        """
        return skins.typography_style(self.base_font_size, role, color=color)

    def frame_inset(self) -> int:
        """Padding content needs to clear a frame painted from :attr:`plate`."""
        return self.plate_padding + 2

    # -- fills -------------------------------------------------------------

    def gradient(self, stops: tuple[str, ...], horizontal: bool = False) -> str:
        """A Qt fill for 1..n stops (one stop = a flat colour)."""
        return skins.gradient(stops, horizontal)

    def bar_fill(self, color: str) -> str:
        """The chunk fill a bar of ``color`` gets under this skin."""
        return skins.bar_fill(_host_skin(self.name), color)

    # -- ready-made stylesheets --------------------------------------------

    def bar_stylesheet(self, color: str) -> str:
        """QSS for a ``QProgressBar`` painted in ``color``, skinned like the
        Timers window's own bars."""
        return skins.row_bar_style(_host_skin(self.name), color)

    def overlay_bar_stylesheet(self, color: str) -> str:
        """QSS for a countdown bar sitting **on the game** — the wide kind the
        event overlay draws, not the thin rule under a Timers row.

        Pair it with :attr:`overlay_bar_height`. This is what a plugin
        contributing an event-overlay region wants, so its bars read as part
        of the same overlay.
        """
        return skins.overlay_bar_rules(_host_skin(self.name), color)

    def overlay_stylesheet(self) -> str:
        """The whole sheet for a window that sits **on the game** — the
        default a :class:`~nparseplus.ui.pluginwindow.PluginWindow` wears.

        Covers the family, ``QLabel`` body text, the scrollbars, and the
        three object names in :data:`TITLE` / :data:`ROW_NAME` /
        :data:`ROW_VALUE`. Set it on your window, then append your own rules.
        """
        host = _host_skin(self.name)
        colors = theme.palette()
        return skins.overlay_window_style(
            host, colors, self.base_font_size
        ) + skins.title_bar_style(host, self.base_font_size)

    def config_stylesheet(self) -> str:
        """The whole sheet for a window the user **configures** things in —
        what Settings, the editors and the plugin manager wear.

        Carries bare type selectors (``QLineEdit``, ``QPushButton``, …), so
        set it on the window, never on the QApplication: at app scope it
        would land on the overlays over EverQuest.
        """
        return chrome.window_style(_host_skin(self.name), theme.palette(), self.base_font_size)


def _host_skin(name: str) -> skins.Skin:
    """The internal :class:`~nparseplus.ui.skins.Skin` a snapshot came from.

    By name rather than by reference so :class:`AppSkin` stays a plain frozen
    record of strings and numbers — a plugin that iterates
    ``dataclasses.fields`` sees the contract and nothing behind it.
    """
    return skins.SKINS.get(name, skins.SKINS[skins.DEFAULT_SKIN])


# -- the live source -----------------------------------------------------------
# A skin has its own module global (``skins.set_skin``), but the two sizes a
# snapshot needs live in the user's settings. Rather than push them on every
# change — one missed call site and every plugin is a font size behind —
# ``create_app`` points this at the live settings once and ``current()`` reads
# through. The Settings root is loaded once per process and never replaced,
# and its ``general`` section is mutated in place, so the reference cannot go
# stale.

_source: Any = None


def use_settings(settings: Any) -> None:
    """Point :func:`current` at the app's live settings. Called once by
    ``create_app``; anything with a ``general.font_size`` and a
    ``general.frame_opacity`` will do (a bare ``Settings()`` in a test)."""
    global _source
    _source = settings


def _sizes() -> tuple[int, float]:
    """``(font size, frame opacity 0..1)``, defaulted when unbound.

    Unbound is the normal case outside the app — a plugin's unit tests, the
    ``nparseplus-plugin validate`` CLI — so it answers the shipped defaults
    rather than raising.
    """
    general = getattr(_source, "general", None)
    if general is None:
        return 12, 1.0
    return max(6, int(general.font_size)), max(0.0, min(1.0, general.frame_opacity / 100))


def current() -> AppSkin:
    """What nParse+ looks like **right now**.

    Cheap — a frozen record built from three module reads — so call it in
    ``apply_skin()`` and in any paint, rather than caching it.
    """
    host = skins.skin()
    colors = theme.palette()
    font_size, frame_opacity = _sizes()
    return AppSkin(
        name=host.name,
        label=host.label,
        text=colors.text,
        heading=colors.heading,
        hint=colors.hint,
        disabled=colors.disabled,
        surface=colors.surface,
        surface_alt=colors.surface_alt,
        # These palette names already mean "an input field"; ``chrome_for``
        # aliases them the same way rather than carrying a second set.
        field_bg=colors.map_input_bg,
        field_text=colors.map_input_text,
        field_border=colors.map_input_border,
        panel_bg=colors.panel_bg,
        track=colors.bar_track,
        accent=host.chrome_accent,
        band=host.chrome_band,
        hairline=host.glass_border,
        plate=host.plate,
        plate_border=host.plate_border,
        glass=host.glass,
        glass_border=host.glass_border,
        chip_fill=host.overlay_chip_fill,
        chip_text=host.overlay_chip_text,
        lane_bg=host.lane_bg,
        lane_border=host.lane_border,
        bar_track=host.bar_track,
        bar_track_border=host.bar_track_border,
        overlay_bar_height=host.overlay_bar_height,
        base_font_size=font_size,
        frame_opacity=frame_opacity,
        row_style=host.row_style,
        plate_padding=host.plate_padding,
        notch=host.notch,
    )
