"""PluginOverlayRegion — the base an add-on's Event Overlay region subclasses.

Re-exported to plugin authors as ``nparseplus_sdk.ui.PluginOverlayRegion``
(SDK 1.5). It is the region counterpart of
:class:`~nparseplus.ui.pluginwindow.PluginWindow`: an adapter from a
:class:`~nparseplus_sdk.plugin.OverlayRegionContext` onto a widget the event
overlay lays out, drags, resizes and persists exactly like its own four
built-in regions.

**A region is display-only, permanently.** ``_apply_locked_flags`` sets
``Qt.WindowType.WindowTransparentForInput`` on the whole overlay window and Qt
has no per-child exemption, so outside position mode nothing inside the
overlay sees a mouse, a key, a hover or a wheel. Inside position mode that
flag is dropped — the overlay has to be clickable for the user to drag their
chrome around — and *that* is the trap this class exists to close: without it
a plugin widget would suddenly start receiving real clicks it was never
written for, but only while the user is repositioning. So the base seals
itself and everything under it with ``WA_TransparentForMouseEvents`` and
``NoFocus``. Sealing (rather than accepting-and-ignoring) is also what keeps
the overlay's own region drag working: the press falls through to the overlay,
which hit-tests the region rectangles itself.

If your add-on needs clicks, it wants a **window** — ``ctx.add_window`` — not
a region. See ``docs/plugins/overlay-regions.md``.

What you get for free: the overlay type treatment and the active skin (kept
current through :meth:`apply_skin`, live, with no restart), a zero-margin
``QVBoxLayout`` to fill, sample content so the region is visible and draggable
in position mode, and the non-interactive posture above.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from nparseplus.ui import pluginskin, skins
from nparseplus_sdk.plugin import OverlayRegionContext

logger = logging.getLogger(__name__)


def qss_region_id(region_key: str) -> str:
    """A ``#selector``-safe object name for a namespaced region key.

    Region keys are ``plugin.<id>.<key>``, and a stylesheet whose selector is
    malformed is discarded WHOLE by Qt with only a runtime warning — so the
    dots have to go. Deliberately the same transformation the overlay's own
    ``qss_id`` applies, so the name this class styles and the name the
    position-mode dashed chrome targets are one string.
    """
    return "OverlayRegion_" + (
        "".join(c if c.isalnum() or c == "_" else "_" for c in region_key) or "region"
    )


class PluginOverlayRegion(QWidget):
    """Base class for a plugin's Event Overlay region. Display-only."""

    def __init__(self, rctx: OverlayRegionContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.region_context = rctx
        self.setObjectName(qss_region_id(rctx.region_key))
        #: The exact sheet this class last wrote — this class owns the whole
        #: stylesheet of the widget (unlike ``PluginWindow``, which adopts a
        #: sheet a pre-SDK-1.4 subclass set by hand; nothing predates this
        #: class, so there is no such sheet to preserve). Override
        #: :meth:`skin_stylesheet` to add rules.
        self._skin_sheet = ""
        self._skin_finalized = False
        #: Whether the "you set your own sheet" line has been said. Once.
        self._sheet_warned = False
        # A layout up front, unlike PluginWindow: the overlay hands the
        # region's layout to its preview machinery, and a region with none
        # would have nowhere to put sample content. Fill it, or nest your own
        # layout inside it.
        base = QVBoxLayout()
        base.setContentsMargins(0, 0, 0, 0)
        base.setSpacing(2)
        self.setLayout(base)
        self._seal_tree(self)
        # The DEFAULT dressing only, for the reason PluginWindow's constructor
        # spells out: :meth:`skin_stylesheet` and :meth:`apply_skin` are both
        # virtual and this runs inside the subclass's ``super().__init__(...)``
        # call, before it has assigned anything its rules might read. The host
        # wraps the region factory in try/except and SKIPS the region on any
        # exception, so an AttributeError there is not a cosmetic failure — it
        # is the add-on silently not appearing.
        self._dress_from_skin(with_hook=False)

    # -- identity ----------------------------------------------------------

    @property
    def region_key(self) -> str:
        """The namespaced ``plugin.<id>.<key>`` this region is persisted under."""
        return self.region_context.region_key

    # -- content -----------------------------------------------------------

    def notify_content_changed(self) -> None:
        """Tell the overlay this region's content changed. Call it liberally.

        The overlay cannot see inside a region: it anchors each one from its
        size and asks ``has_content`` whether the overlay is worth showing at
        all. So a region that grew a row, lost one, or went from empty to
        occupied has to say so, or it sits at its old size — or leaves the
        overlay hidden while holding something the user wanted to see.

        Also the moment this class re-seals the widget tree, which is what
        makes content built after ``__init__`` non-interactive too.
        """
        self._seal_tree(self)
        try:
            self.region_context.on_content_changed()
        except Exception:
            logger.exception("overlay region %r content notification failed", self.region_key)

    def has_content(self) -> bool:
        """Whether this region currently has anything to show.

        A convenience for the common ``has_content=region.has_content`` wiring
        — the spec's predicate is what the overlay actually asks, and it is
        declared before the widget exists, so pass a bound method of your own
        state or a lambda closing over the region you built. The default here
        says "yes whenever any of my children is visible", which is right for
        a region that shows and hides its own rows.

        Asked often, on the GUI thread. Keep whatever you substitute cheap.
        """
        return any(child.isVisibleTo(self) for child in self.findChildren(QWidget))

    def sample(self) -> list[QWidget]:
        """Sample content for position mode; return what you added.

        The overlay populates every region with sample content while the user
        is placing their chrome, so an empty region is still something they
        can see and drag. Add your widgets to ``self.layout()`` and hand them
        back — the overlay takes them out again when position mode ends.

        The default is a single chip carrying the region's title, which is
        enough to make any region placeable. Override it to show the shape of
        your real content.
        """
        chip = QLabel(self.region_context.title or self.region_key, self)
        chip.setObjectName(skins.OBJ_ROW_NAME)
        # A plugin's own title string, and Qt's AutoText renders anything
        # tag-shaped as markup — same reasoning as every label on the overlay
        # that shows text the app did not write.
        chip.setTextFormat(Qt.TextFormat.PlainText)
        layout = self.layout()
        if layout is not None:
            layout.addWidget(chip)
        chip.show()
        return [chip]

    # -- appearance --------------------------------------------------------

    def skin_stylesheet(self) -> str:
        """Your own QSS, appended after the app's overlay dressing.

        **The** place for a region's rules. This class owns the widget's whole
        stylesheet and re-assembles it from the two halves on every skin, font
        size and frame-opacity change, so calling ``setStyleSheet`` yourself
        is discarded on the next change; override this instead::

            def skin_stylesheet(self) -> str:
                app = skin.current()
                return f"#Total {{ {app.typography(skin.NUMERIC_TEXT, color=app.heading)} }}"

        Read ``nparseplus_sdk.skin.current()`` inside it — it is called afresh
        each time and must never be cached. It is **not** called during
        ``super().__init__()``: the first call happens once your constructor
        has finished, before the region is first shown.
        """
        return ""

    def apply_skin(self) -> None:
        """Re-dress from the skin the user just picked.

        ``app._apply_appearance`` calls this on every skin, font-size or
        frame-opacity change — live, with no restart. Override it for the work
        a stylesheet cannot do (child widgets, painted colours, sizes),
        calling ``super().apply_skin()`` first. For plain QSS override
        :meth:`skin_stylesheet` instead.
        """
        self._dress_from_skin()

    def showEvent(self, event) -> None:
        """Finalize the skin before the first paint.

        A region has no ``restore_visibility`` to hang the first dress on the
        way a plugin window does, so this is the one hook — and it has to be a
        deferred first dress rather than one in ``__init__`` for the reason the
        constructor gives.
        """
        self._seal_tree(self)
        self._finalize_skin()
        super().showEvent(event)

    def _finalize_skin(self) -> None:
        """Dress for the first time, once the subclass is built.

        Calls the full virtual :meth:`apply_skin`, not just
        ``_dress_from_skin``: ``app._apply_appearance`` only runs on a
        *change*, so a region built at launch never receives a sweep and an
        override doing what a stylesheet cannot express would sit
        uninitialized until the user happened to switch skin.

        Idempotent, and guarded — this runs from ``showEvent``, where an
        exception has nowhere useful to go, and a region's cosmetics must not
        cost it its place on the overlay.
        """
        if self._skin_finalized:
            return
        self._skin_finalized = True
        try:
            self.apply_skin()
        except Exception:
            logger.exception(
                "overlay region %r apply_skin() failed on first dress", self.region_key
            )
            self._dress_from_skin(with_hook=False)

    def _dress_from_skin(self, *, with_hook: bool = True) -> None:
        self._warn_if_overwritten()
        appearance = pluginskin.current()
        extra = ""
        if with_hook:
            try:
                extra = self.skin_stylesheet()
            except Exception:
                logger.exception(
                    "overlay region %r skin_stylesheet() failed; using the default dressing",
                    self.region_key,
                )
        # Transparent by construction: the overlay window is translucent, so
        # any opaque brush here paints a solid rectangle over EverQuest.
        self._skin_sheet = (
            appearance.overlay_stylesheet()
            + f"#{self.objectName()} {{ background: transparent; }}"
            + extra
        )
        self.setStyleSheet(self._skin_sheet)
        # The type scale just moved, so the region's height probably did too —
        # and the overlay re-asserts its position-mode chrome from here, since
        # writing the sheet is what would otherwise have dropped the dashed
        # border a user was dragging by.
        self.notify_content_changed()

    def _warn_if_overwritten(self) -> None:
        """Say once that a sheet set by hand is about to be replaced.

        ``PluginWindow`` ADOPTS such a sheet, because windows written before
        SDK 1.4 had no hook to be called by. A region cannot: the overlay
        appends its position-mode chrome to this widget's sheet and strips it
        off again by suffix, so re-writing an adopted sheet after that
        appendix would leave the dashed border on when position mode ends.

        So the sheet is replaced — which without this line is a plugin's
        styling silently vanishing at the first skin change, with nothing
        anywhere to explain it. A sheet that merely STARTS with ours is the
        overlay's chrome, not a plugin's rules, and is not what this is about.
        """
        current = self.styleSheet()
        if self._sheet_warned or not self._skin_sheet or current.startswith(self._skin_sheet):
            return
        self._sheet_warned = True
        logger.warning(
            "overlay region %r had its stylesheet set directly; this class owns the whole "
            "sheet and is replacing it. Put your rules in skin_stylesheet() instead — see "
            "docs/plugins/overlay-regions.md",
            self.region_key,
        )

    # -- the non-interactive posture ---------------------------------------

    def childEvent(self, event) -> None:
        """Seal every widget that joins this region, whenever it joins.

        ``WA_TransparentForMouseEvents`` is per-widget and not inherited, so a
        child added after ``__init__`` would be interactive in position mode
        even though its parent is not. A subtree reparented in one go brings
        descendants that never raise ``ChildAdded`` here, hence the recursive
        seal; :meth:`notify_content_changed` and :meth:`showEvent` re-sweep for
        anything built later still.

        ``ChildPolished`` as well as ``ChildAdded`` because a widget class
        that sets its OWN focus policy does so after its parent is assigned —
        ``QPushButton`` is the obvious one — so the mouse attribute holds from
        the moment of parenting but the focus policy needs the second pass Qt
        sends before the child is first shown.
        """
        super().childEvent(event)
        if event.type() in (QEvent.Type.ChildAdded, QEvent.Type.ChildPolished):
            child = event.child()
            if isinstance(child, QWidget):
                self._seal_tree(child)

    def _seal_tree(self, root: QWidget) -> None:
        seal_tree(root)


# -- sealing a region that is NOT this base ------------------------------------
#
# ``OverlayRegionSpec.factory`` promises a QWidget, not a subclass of this
# class, and the docs and tests support returning a plain one — the base is a
# convenience. But the display-only guarantee is a promise about EVERY region,
# not about the ones that happened to use the convenience: an unsealed plain
# widget (or a child control inside it) receives the click in position mode,
# where the overlay drops ``WindowTransparentForInput``, so it can run handlers
# it was never written for AND makes its own rectangle impossible to drag,
# because the press never falls through to the overlay's hit-test. So the host
# seals whatever the factory returned, and these are the functions it uses —
# the same ones the base uses, so the two cannot drift.


def seal_widget(widget: QWidget) -> None:
    """One widget, made transparent to the mouse and unable to take focus."""
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)


def seal_tree(root: QWidget) -> None:
    """``root`` and every widget under it.

    ``WA_TransparentForMouseEvents`` is per-widget and not inherited, so the
    whole subtree has to be walked rather than just the root.
    """
    seal_widget(root)
    for child in root.findChildren(QWidget):
        seal_widget(child)


class _RegionSealer(QObject):
    """Keeps a non-base region sealed as it builds children later.

    The base class does this by overriding ``childEvent``, which is not
    available for a widget the host did not write, so this is the same job
    done through an event filter. It follows new descendants as they appear:
    a filter on the root alone would never see a grandchild added to an
    existing child, and a region that builds its content lazily — the common
    shape, since ``sample()`` and the first real update both run after
    construction — would silently go unsealed exactly there.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # ChildPolished as well as ChildAdded: a widget class that sets its
        # OWN focus policy does so after its parent is assigned (QPushButton
        # is the obvious one), so the mouse attribute holds from the moment of
        # parenting but the focus policy needs Qt's second pass.
        if event.type() in (QEvent.Type.ChildAdded, QEvent.Type.ChildPolished):
            child = event.child()
            if isinstance(child, QWidget):
                self.watch(child)
        return False

    def watch(self, root: QWidget) -> None:
        seal_tree(root)
        for widget in (root, *root.findChildren(QWidget)):
            # Remove before install so a widget reached twice — a subtree
            # reparented in one go raises ChildAdded for the root and is also
            # walked by findChildren here — ends up with exactly one entry
            # rather than a second callback doing the same work.
            widget.removeEventFilter(self)
            widget.installEventFilter(self)


def enforce_non_interactive(widget: QWidget) -> None:
    """Seal a region widget and KEEP it sealed, whatever class it is.

    Called by the host on every widget a region factory returns. A
    :class:`PluginOverlayRegion` already seals itself and keeps itself sealed
    through ``childEvent``, so it needs the sweep but not the filter; anything
    else gets both.
    """
    if isinstance(widget, PluginOverlayRegion):
        seal_tree(widget)
        return
    # Parented to the widget, which is the whole reason it is not assigned
    # anywhere: Qt owns it, so it lives exactly as long as the region does and
    # goes when the region is retired and deleted.
    _RegionSealer(widget).watch(widget)
