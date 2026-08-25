"""Kill Ticker — a plugin's own region INSIDE the event overlay.

Shows the last few things that died near you, as a small list sitting on the
game beside the CH lanes and the timer bars rather than in a window of its
own. This file is the reference for the region seam: an ``OverlayRegionSpec``,
a ``PluginOverlayRegion`` subclass, and the two calls that keep the overlay
in step — ``notify_content_changed`` and ``has_content``.

**A region is display-only, permanently.** The event overlay is
transparent for input, and Qt has no per-child exemption, so nothing in here
will ever receive a click, a hover or a key — not even a right-click menu.
The base class seals itself so that stays true in position mode too, where
the overlay IS clickable and every click belongs to the user dragging their
chrome around. If you want something people press, ship a window
(``ctx.add_window``) instead. See ``docs/plugins/overlay-regions.md``.

Where it appears: the overlay only shows itself when something has content,
so this region is invisible until the first kill and fades out again once the
list is cleared on a zone. Drag it where you like from tray > Position Event
Overlay; the placement is persisted under ``plugin.kill-ticker.kills`` and
kept even if you disable the add-on.

Install: add-ons are off by default — tick Settings > Advanced > Enable
plugins (add-ons) and restart nParse+ first. Then copy this file into your
plugins folder (tray > Open Plugins Folder), or use Settings > Plugins >
Install from file, and restart again to be asked for consent.
Check it from a dev environment with: ``nparseplus-plugin validate kill_ticker.py``
"""

from __future__ import annotations

from typing import Any

from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginContext, PluginMeta

KEEP = 5  # how many kills the ticker shows


def _build_region(rctx: Any) -> Any:
    """The region factory — imported lazily, like every host Qt import.

    Defined at module level rather than inline so the class is built once the
    first time a region is made, and so a bare dev environment (the validate
    CLI, a plugin's own Qt-free tests) can import this module without PySide6.
    """
    from PySide6.QtWidgets import QLabel

    from nparseplus_sdk import skin
    from nparseplus_sdk.ui import PluginOverlayRegion

    class KillTickerRegion(PluginOverlayRegion):
        def __init__(self, ctx: Any) -> None:
            super().__init__(ctx)
            self._rows: list[QLabel] = []
            # The supported push path onto the GUI thread. A region is
            # display-only, so this (or a QTimer) is how anything in here ever
            # changes.
            if ctx.bridge is not None:
                ctx.bridge.event_received.connect(self._on_event)

        # -- content ---------------------------------------------------------

        @property
        def kills(self) -> list[str]:
            return [row.text() for row in self._rows]

        def _on_event(self, event: Any) -> None:
            from nparseplus_sdk.events import SlainEvent, YouZonedEvent

            if isinstance(event, YouZonedEvent):
                self.clear()
            elif isinstance(event, SlainEvent):
                self.add(event.victim)

        def add(self, victim: str) -> None:
            row = QLabel(victim, self)
            row.setObjectName(skin.ROW_NAME)  # wears the skin with no rules
            self.layout().insertWidget(0, row)
            self._rows.insert(0, row)
            while len(self._rows) > KEEP:
                self._rows.pop().deleteLater()
            # THE call: the overlay cannot see inside a region, so it does not
            # know this one grew — or that it went from empty to worth showing.
            self.notify_content_changed()

        def clear(self) -> None:
            for row in self._rows:
                row.deleteLater()
            self._rows.clear()
            self.notify_content_changed()

        def has_content(self) -> bool:
            return bool(self._rows)

        # -- appearance ------------------------------------------------------

        def sample(self) -> list[Any]:
            """What position mode shows, so the region can be placed before
            anything has died."""
            made = [QLabel(name, self) for name in ("Sample Mob", "Another Mob")]
            for label in made:
                label.setObjectName(skin.ROW_NAME)
                self.layout().addWidget(label)
                label.show()
            return made

        def skin_stylesheet(self) -> str:
            """Read the snapshot at the moment you paint, never at activate:
            the user can switch skin mid-fight and this is called afresh."""
            app = skin.current()
            return f"#{skin.ROW_NAME} {{ {app.typography(skin.BODY_TEXT, color=app.text)} }}"

    return KillTickerRegion(rctx)


class KillTickerPlugin(NParsePlugin):
    meta = PluginMeta(
        id="kill-ticker",
        name="Kill Ticker",
        version="1.0.0",
        description="A small list of recent kills, drawn inside the event overlay.",
        author="nParse+ examples",
        requires_sdk=">=1.5,<2",
    )

    def __init__(self) -> None:
        self.region: Any = None

    def activate(self, ctx: PluginContext) -> None:
        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="kills",
                title="Kills",
                factory=self._make,
                # Required: the overlay hides itself when every region is
                # empty, so a region with no opinion could never keep it on
                # screen by itself. Asked often — keep it a flag read.
                has_content=self._has_content,
                default_anchor="bottom",
                default_dy=-140,
                default_width=200,
            )
        )
        ctx.logger.info("kill-ticker ready — drag it from tray > Position Event Overlay")

    def _make(self, rctx: Any) -> Any:
        self.region = _build_region(rctx)
        return self.region

    def _has_content(self) -> bool:
        return self.region is not None and self.region.has_content()


def create_plugin() -> KillTickerPlugin:
    return KillTickerPlugin()
