"""What nParse+ looks like, re-exported from the running host.

An add-on that wants to sit beside the Timers window without looking like a
bug report needs the app's colours, its type scale and its frame. Hardcoding
hex goes stale the moment the user switches skin — and the switch is **live**,
mid-fight, with no restart — while importing ``nparseplus.ui.skins`` reaches
into host internals that move with every design pass.

This module is the supported answer: a small, frozen read surface, forwarded
lazily to the host like :mod:`nparseplus_sdk.events` so importing it never
pulls Qt. The forwarded set is the explicit :data:`EXPORTS` allowlist, so the
SDK's additive-only 1.x promise covers names chosen here rather than
everything that happens to live in the host's skin layer.

The one rule to take away
-------------------------

**The palette owns VALUE; the skin owns HUE.**

:attr:`AppSkin.text`, :attr:`~AppSkin.surface`, :attr:`~AppSkin.field_bg` and
the rest of the value group are the readability floor. They are *identical*
under all three skins, and they are what you paint text and grounds with. The
skin contributes exactly one hue — :attr:`~AppSkin.accent` — for selection
bands, focus rings, group titles and hairlines.

So this is right::

    label.setStyleSheet(f"color: {app.text}; background: {app.surface};")
    rule.setStyleSheet(f"background: {app.accent};")     # a hairline

and this is the mistake the split exists to prevent::

    label.setStyleSheet(f"color: {app.text}; background: {app.accent};")

because an accent is a mark, not a ground: body text on it measures 1.2:1
under Velious (gold on gold — the app's own title caps are gold too), 1.7:1
under Duxa and 3.3:1 under Ledger. For a filled row, ``AppSkin.band`` is the
ground the app itself uses, with ``heading`` as its text.

Using it
--------

Read the snapshot at the moment you paint, never at ``activate``::

    from nparseplus_sdk import skin

    class MyWindow(PluginWindow):
        def skin_stylesheet(self) -> str:
            app = skin.current()
            return f"#Total {{ {app.typography(skin.NUMERIC_TEXT, color=app.heading)} }}"

        def apply_skin(self) -> None:          # only what QSS cannot express
            super().apply_skin()
            self._bar.setStyleSheet(skin.current().bar_stylesheet(skin.GOOD))

``PluginWindow`` re-assembles its sheet from its own dressing plus
``skin_stylesheet()`` on every skin, font-size and frame-opacity change, and
calls ``apply_skin()`` for anything else — so both run afresh each time and
neither may cache an ``AppSkin``. Sizes are multipliers of the user's
``general.font_size``: use :meth:`AppSkin.px` and :meth:`AppSkin.typography`,
never literal px.

See ``docs/plugins/appearance.md`` for the worked example.
"""

from __future__ import annotations

from typing import Any

#: The host names this module forwards. Anything else raises AttributeError.
EXPORTS = frozenset(
    {
        # the snapshot
        "AppSkin",
        "current",
        "SKIN_NAMES",
        # typography
        "TypographyRole",
        "SMALL_DISPLAY",
        "BODY_TEXT",
        "NUMERIC_TEXT",
        "px",
        "tracking",
        "typography_style",
        # the object names the ready-made stylesheets target
        "TITLE",
        "ROW_NAME",
        "ROW_VALUE",
        # semantic accents
        "GOOD",
        "BAD",
        "COOLDOWN",
        "TIMER",
        "ROLL",
        "POP_WINDOW",
        "LINK",
        # colour helpers
        "shade",
        "rgba",
        "gradient",
    }
)

_HOST_HINT = (
    "nparseplus_sdk.skin re-exports the host app's skin values and needs "
    "nparseplus importable. Inside nParse+ this always works; for standalone "
    "development install the app from source: "
    "pip install git+https://github.com/prokopto-dev/nparse-plus"
)


def _host_skin() -> Any:
    try:
        from nparseplus.ui import pluginskin
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(_HOST_HINT) from exc
    return pluginskin


def __getattr__(name: str) -> Any:
    if name not in EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(_host_skin(), name)


def __dir__() -> list[str]:
    return sorted(EXPORTS | {"EXPORTS"})
