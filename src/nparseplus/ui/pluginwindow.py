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
under all three skins. :meth:`PluginWindow.apply_skin` is the hook for one
that wants more — see ``nparseplus_sdk.skin`` and
``docs/plugins/appearance.md``.
"""

from __future__ import annotations

from PySide6.QtGui import QPainter

from nparseplus.config.settings import WindowState
from nparseplus.ui import pluginskin, skins, skinwidgets
from nparseplus.ui.overlaybase import OverlayWindowBase
from nparseplus_sdk.plugin import PluginWindowContext


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
        # The default dressing, not ``self.apply_skin()``: this runs inside
        # the subclass's ``super().__init__(...)`` call, before a single one
        # of its own widgets exists, and an override that touched them would
        # raise. A skin change later calls the override, as it should.
        self._dress_from_skin()

    # -- appearance --------------------------------------------------------

    def apply_skin(self) -> None:
        """Re-dress from the skin the user just picked.

        ``app._apply_appearance`` calls this on every skin, font-size or
        frame-opacity change — live, with no restart — so read
        ``nparseplus_sdk.skin.current()`` here rather than caching a snapshot
        at construction. The default sets the overlay stylesheet and the
        frame clearance; override it to add your own rules, calling
        ``super().apply_skin()`` first::

            def apply_skin(self) -> None:
                super().apply_skin()
                app = skin.current()
                self.setStyleSheet(
                    self.styleSheet()
                    + f"#Total {{ {app.typography(skin.NUMERIC_TEXT, color=app.heading)} }}"
                )
        """
        self._dress_from_skin()

    def _dress_from_skin(self) -> None:
        appearance = pluginskin.current()
        self.setStyleSheet(appearance.overlay_stylesheet())
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
