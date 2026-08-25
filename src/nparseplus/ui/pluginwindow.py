"""PluginWindow — the overlay window base plugins subclass.

Re-exported to plugin authors as ``nparseplus_sdk.ui.PluginWindow``. It is a
thin adapter from a :class:`~nparseplus_sdk.plugin.PluginWindowContext` onto
``OverlayWindowBase``, which supplies the full nParse+ overlay recipe:
frameless + drag-to-move, resize from any edge, opacity/on-top/click-through
flags, per-window geometry persisted in ``Settings.windows[window_key]``,
and quit safety (Cmd+Q never clobbers the shown flag).

Subclasses build their content in ``__init__`` and finish with
``self.restore_visibility()``. For live bus events, connect to
``self.window_context.bridge.event_received`` (GUI-thread delivery); for
state polling, use a QTimer gated on ``isVisible()``.

Since SDK 1.4 it also arrives **skinned**: the active skin's plate and glass
are painted behind it and its labels wear the overlay type treatment, so a
plugin that writes no styling at all still looks like the rest of the app
under all three skins. :meth:`PluginWindow.skin_stylesheet` is where a plugin
adds rules of its own; :meth:`PluginWindow.apply_skin` is the hook for
anything a stylesheet cannot express. See ``nparseplus_sdk.skin`` and
``docs/plugins/appearance.md``.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QPainter

from nparseplus.config.settings import WindowState
from nparseplus.ui import pluginskin, skins, skinwidgets
from nparseplus.ui.overlaybase import OverlayWindowBase
from nparseplus_sdk.plugin import PluginWindowContext

logger = logging.getLogger(__name__)


class PluginWindow(OverlayWindowBase):
    def __init__(
        self,
        wctx: PluginWindowContext,
        *,
        translucent: bool = True,
        default_state: WindowState | None = None,
        parent=None,
    ) -> None:
        super().__init__(
            settings=wctx.settings,
            window_key=wctx.window_key,
            title=wctx.title,
            default_geometry=wctx.default_geometry,
            on_save=wctx.on_save,
            default_state=default_state,
            translucent=translucent,
            parent=parent,
        )
        self.window_context = wctx
        #: The exact sheet this class last wrote, so a re-dress can tell its
        #: own half from the subclass's. See :meth:`_dress_from_skin`.
        self._skin_sheet = ""
        #: Rules a subclass set with ``setStyleSheet`` rather than through
        #: :meth:`skin_stylesheet`, kept across re-dresses.
        self._adopted_sheet = ""
        #: Whether :meth:`skin_stylesheet` has been consulted yet.
        self._skin_finalized = False
        # The DEFAULT dressing only — neither ``self.apply_skin()`` nor
        # ``self.skin_stylesheet()``. Both are virtual, and this runs inside
        # the subclass's ``super().__init__(...)`` call, before it has
        # assigned ``self._plugin`` or anything else its rules might read;
        # an override touching that state would raise AttributeError here.
        # The host wraps the window factory in try/except and SKIPS the
        # window on any exception, so that is not a cosmetic failure — it is
        # the add-on silently not appearing. The override is evaluated later,
        # by ``_finalize_skin``.
        self._dress_from_skin(with_hook=False)

    # -- appearance --------------------------------------------------------

    def skin_stylesheet(self) -> str:
        """Your own QSS, appended after the app's overlay dressing.

        **The** place for a plugin window's rules. Overriding this rather
        than calling ``setStyleSheet`` is what lets the window be re-dressed
        on every skin change without either discarding your rules or
        accumulating a stale copy of them per change — this class owns the
        whole sheet and re-assembles it from the two halves each time::

            def skin_stylesheet(self) -> str:
                app = skin.current()
                return f"#Total {{ {app.typography(skin.NUMERIC_TEXT, color=app.heading)} }}"

        Read ``nparseplus_sdk.skin.current()`` inside it: it is called afresh
        on every change, and never cached. It is **not** called during
        ``super().__init__()`` — the first call happens once your constructor
        has finished (at ``restore_visibility()``, or before the window is
        first shown), so anything you assign after ``super().__init__(...)``
        is available to it.
        """
        return ""

    def restore_visibility(self) -> None:
        """Honour the persisted ``shown`` flag — call it last in ``__init__``.

        Also the point at which the window is first dressed from the skin
        (:meth:`skin_stylesheet` and any :meth:`apply_skin` override): by
        convention this is the last thing a subclass does, so its own state
        exists by now. A window that never calls it is dressed before its
        first show instead.
        """
        self._finalize_skin()
        super().restore_visibility()

    def showEvent(self, event) -> None:
        """Finalize before the first paint, for a window that never called
        ``restore_visibility`` (one opened straight from the tray)."""
        self._finalize_skin()
        super().showEvent(event)

    def _finalize_skin(self) -> None:
        """Dress the window for the first time, once the subclass is built.

        Calls the full virtual :meth:`apply_skin`, not just
        ``_dress_from_skin`` — a startup plugin window never receives an
        appearance sweep (``app._apply_appearance`` only runs on a *change*),
        so an override doing the work a stylesheet cannot express (styling
        child widgets, painted colours, rebuilding coloured model items)
        would otherwise sit uninitialized until the user happened to switch
        skin. Safe here and not in ``__init__`` for the reason the
        constructor spells out: by now the subclass exists.

        Idempotent, and guarded for the same reason the stylesheet hook is —
        this runs from ``showEvent``, where an exception has nowhere useful
        to go, and a plugin's cosmetics must not cost it its window.
        """
        if self._skin_finalized:
            return
        self._skin_finalized = True
        try:
            self.apply_skin()
        except Exception:
            logger.exception(
                "plugin window %r apply_skin() failed on first dress", self._window_key
            )
            self._dress_from_skin(with_hook=False)

    def apply_skin(self) -> None:
        """Re-dress from the skin the user just picked.

        ``app._apply_appearance`` calls this on every skin, font-size or
        frame-opacity change — live, with no restart. Override it for the
        work a stylesheet cannot do (styling child widgets, painted colours,
        sizes), calling ``super().apply_skin()`` first::

            def apply_skin(self) -> None:
                super().apply_skin()
                self._row.apply_skin()

        For plain QSS, override :meth:`skin_stylesheet` instead — appending
        to ``self.styleSheet()`` here works, but this class then has to guess
        which half is yours.
        """
        self._dress_from_skin()

    def _dress_from_skin(self, *, with_hook: bool = True) -> None:
        current = self.styleSheet()
        if current != self._skin_sheet:
            # Not what we wrote: a subclass called ``setStyleSheet`` itself,
            # which is what EVERY PluginWindow written before SDK 1.4 does —
            # there was no hook, and no apply_skin to be called by. Adopt it
            # and keep re-applying it AFTER our own rules (so it still wins)
            # rather than discarding it on the first skin change: an additive
            # release must not silently unstyle a plugin that already works.
            #
            # Stripping the dressing we last wrote is what keeps a subclass
            # that appended to ``self.styleSheet()`` from contributing a
            # stale copy of our rules — and growing the sheet by one copy of
            # its own on every change.
            self._adopted_sheet = (
                current[len(self._skin_sheet) :]
                if self._skin_sheet and current.startswith(self._skin_sheet)
                else current
            )
        appearance = pluginskin.current()
        # Guarded even post-construction: a cosmetic hook must never be what
        # stops a plugin's window from showing, and ``showEvent`` is not a
        # place an exception can usefully go.
        extra = ""
        if with_hook:
            try:
                extra = self.skin_stylesheet()
            except Exception:
                logger.exception(
                    "plugin window %r skin_stylesheet() failed; using the default dressing",
                    self._window_key,
                )
        self._skin_sheet = appearance.overlay_stylesheet() + self._adopted_sheet + extra
        self.setStyleSheet(self._skin_sheet)
        # On the widget rather than on the layout: the plugin owns its layout
        # and its margins are its own, while clearing the painted frame is
        # ours. Qt adds the two.
        inset = appearance.frame_inset()
        self.setContentsMargins(inset, inset, inset, inset)
        self.update()

    def paintEvent(self, event) -> None:
        """Paint the active skin's plate and glass behind the window.

        The app's own overlays wrap their content in a
        ``skinwidgets.SkinPanel``; a plugin window cannot, because the plugin
        sets its own layout on ``self``. So it paints the identical frame
        directly — same function, same notch, same frame opacity (which fades
        the frame only, never the content on it).
        """
        painter = QPainter(self)
        skinwidgets.paint_skin_frame(
            painter,
            self.rect(),
            skins.skin(),
            pluginskin.current().frame_opacity,
        )
        painter.end()
        super().paintEvent(event)
