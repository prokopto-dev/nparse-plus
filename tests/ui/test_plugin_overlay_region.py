"""``PluginOverlayRegion`` — the base an add-on's overlay region subclasses (#155).

The defining property is the one the owner settled on the issue: a region is
**display-only, permanently**. Position mode is the trap — it drops
``WindowTransparentForInput`` so the user can drag their chrome, and a plugin
widget would suddenly start receiving real clicks it was never written for —
so the base seals itself and everything under it. Sealing rather than
accepting-and-ignoring is load-bearing in the other direction too: the press
has to fall THROUGH to the overlay, or the region could not be dragged.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from nparseplus.config.settings import OverlayRegion, Settings, WindowState
from nparseplus.ui import pluginskin, skins
from nparseplus.ui.eventoverlay import EventOverlayWindow
from nparseplus.ui.pluginregion import PluginOverlayRegion, qss_region_id
from nparseplus_sdk.plugin import OverlayRegionContext

pytestmark = pytest.mark.qt

REGION_KEY = "plugin.ticker.main"


def make_context(**overrides) -> OverlayRegionContext:
    kwargs = {
        "settings": Settings(),
        "region_key": REGION_KEY,
        "title": "Ticker",
        "on_save": lambda: None,
    }
    kwargs.update(overrides)
    return OverlayRegionContext(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def region(qtbot) -> PluginOverlayRegion:
    widget = PluginOverlayRegion(make_context())
    qtbot.addWidget(widget)
    return widget


# -- the non-interactive posture -----------------------------------------------


def test_the_region_itself_is_transparent_for_the_mouse(region) -> None:
    assert region.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert region.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_a_child_added_later_is_sealed_too(region) -> None:
    """``WA_TransparentForMouseEvents`` is per-widget and not inherited, so a
    button built after ``__init__`` would otherwise be clickable — but only
    while the user is repositioning, which is the worst possible time to
    discover it."""
    button = QPushButton("press me", region)

    assert button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    # A widget class that sets its own focus policy does so AFTER its parent
    # is assigned, so that half lands on Qt's second pass (ChildPolished),
    # which it sends before the child can ever be shown or focused.
    region.show()
    assert button.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_a_reparented_subtree_is_sealed_all_the_way_down(region) -> None:
    """A subtree built elsewhere and reparented in one go raises ChildAdded
    only for its root, so the seal has to recurse."""
    panel = QWidget()
    deep = QLabel("nested", panel)

    panel.setParent(region)

    assert panel.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert deep.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_notifying_reseals_anything_built_since(region) -> None:
    """The documented sweep point: a descendant grown under an existing child
    never raises ChildAdded on the region."""
    panel = QWidget(region)
    late = QPushButton("late", panel)
    late.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
    late.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    region.notify_content_changed()

    assert late.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert late.focusPolicy() == Qt.FocusPolicy.NoFocus


# -- content -------------------------------------------------------------------


def test_notify_calls_the_context_hook(qtbot) -> None:
    calls: list[int] = []
    widget = PluginOverlayRegion(make_context(on_content_changed=lambda: calls.append(1)))
    qtbot.addWidget(widget)
    calls.clear()

    widget.notify_content_changed()

    assert calls == [1]


def test_a_raising_content_hook_does_not_reach_the_plugin(qtbot, caplog) -> None:
    """The hook runs from a plugin's own timers, where an exception has
    nowhere to go but the Qt event loop of an always-on-top window."""

    def boom() -> None:
        raise RuntimeError("nope")

    widget = PluginOverlayRegion(make_context(on_content_changed=boom))
    qtbot.addWidget(widget)

    with caplog.at_level("ERROR"):
        widget.notify_content_changed()

    assert any("content notification failed" in record.message for record in caplog.records)


def test_the_default_sample_puts_a_titled_chip_in_the_layout(region) -> None:
    made = region.sample()

    assert [widget.text() for widget in made] == ["Ticker"]
    assert made[0].objectName() == skins.OBJ_ROW_NAME
    assert region.layout().indexOf(made[0]) >= 0
    # A plugin's own title string is never markup.
    assert made[0].textFormat() == Qt.TextFormat.PlainText


def test_the_default_has_content_follows_the_children(region) -> None:
    assert region.has_content() is False

    label = QLabel("something", region)

    assert region.has_content() is True

    label.hide()

    assert region.has_content() is False


# -- appearance ----------------------------------------------------------------


def test_the_object_name_is_selector_safe(region) -> None:
    """Region keys are ``plugin.<id>.<key>``, and Qt discards a WHOLE sheet
    whose selector is malformed with only a runtime warning."""
    assert region.objectName() == "OverlayRegion_plugin_ticker_main"
    assert "." not in region.objectName()
    assert qss_region_id("") == "OverlayRegion_region"


def test_it_is_dressed_transparent_from_the_start(region) -> None:
    """The overlay window is translucent, so any opaque brush paints a solid
    rectangle over EverQuest."""
    assert f"#{region.objectName()} {{ background: transparent; }}" in region.styleSheet()
    assert pluginskin.current().overlay_stylesheet() in region.styleSheet()


def test_skin_stylesheet_is_appended_and_re_read_on_every_change(qtbot) -> None:
    class Custom(PluginOverlayRegion):
        def __init__(self, rctx):
            super().__init__(rctx)
            self.marker = "#Total { color: red; }"

        def skin_stylesheet(self) -> str:
            return self.marker

    widget = Custom(make_context())
    qtbot.addWidget(widget)
    # NOT during super().__init__(): the subclass had not assigned .marker yet,
    # and the host skips a region whose factory raises.
    assert widget.marker not in widget.styleSheet()

    widget.apply_skin()

    assert widget.styleSheet().endswith(widget.marker)

    widget.marker = "#Total { color: blue; }"
    widget.apply_skin()

    assert widget.styleSheet().endswith("blue; }")
    assert "red" not in widget.styleSheet()


def test_a_raising_skin_stylesheet_still_leaves_a_dressed_region(qtbot, caplog) -> None:
    class Broken(PluginOverlayRegion):
        def skin_stylesheet(self) -> str:
            raise RuntimeError("nope")

    widget = Broken(make_context())
    qtbot.addWidget(widget)

    with caplog.at_level("ERROR"):
        widget.apply_skin()

    assert pluginskin.current().overlay_stylesheet() in widget.styleSheet()
    assert any("skin_stylesheet() failed" in record.message for record in caplog.records)


def test_showing_finalizes_the_skin_once(qtbot) -> None:
    """A region has no ``restore_visibility`` to hang the first dress on, and
    ``app._apply_appearance`` only runs on a CHANGE — so a region built at
    launch would never see its own ``apply_skin`` override without this."""
    calls: list[int] = []

    class Counting(PluginOverlayRegion):
        def apply_skin(self) -> None:
            super().apply_skin()
            calls.append(1)

    widget = Counting(make_context())
    qtbot.addWidget(widget)
    assert calls == []

    widget.show()
    widget.hide()
    widget.show()

    assert calls == [1]


def test_a_raising_apply_skin_does_not_cost_the_region_its_show(qtbot, caplog) -> None:
    class Broken(PluginOverlayRegion):
        def apply_skin(self) -> None:
            raise RuntimeError("nope")

    widget = Broken(make_context())
    qtbot.addWidget(widget)

    with caplog.at_level("ERROR"):
        widget.show()

    assert widget.isVisible()
    assert pluginskin.current().overlay_stylesheet() in widget.styleSheet()


# -- inside the real overlay ---------------------------------------------------


def _region_state() -> WindowState:
    return WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top"),
            "utility": OverlayRegion(anchor="top", dy=96),
            "alert": OverlayRegion(anchor="center"),
            "bars": OverlayRegion(anchor="bottom"),
            REGION_KEY: OverlayRegion(anchor="top", dy=300, height=60),
        },
    )


@pytest.fixture
def overlay(qtbot) -> EventOverlayWindow:
    window = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(window)
    return window


@pytest.fixture
def placed(qtbot, overlay) -> PluginOverlayRegion:
    # The content hook the host supplies for real (``_region_content_hook``).
    # Without it a region cannot tell the overlay anything, which is what the
    # re-anchor and chrome tests below are about.
    widget = PluginOverlayRegion(
        make_context(on_content_changed=lambda: overlay.region_content_changed(REGION_KEY))
    )
    qtbot.addWidget(widget)
    overlay.add_region(
        REGION_KEY,
        widget,
        title="Ticker",
        has_content=lambda: False,
        default=OverlayRegion(anchor="top", dy=300, height=60),
        preview=widget.sample,
    )
    return widget


def _press(overlay: EventOverlayWindow, x: int, y: int) -> None:
    point = QPointF(x, y)
    overlay.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            point,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_a_sealed_region_can_still_be_dragged_in_position_mode(overlay, placed) -> None:
    """The press falls through to the overlay, which hit-tests the region
    RECTANGLES itself — which is exactly why the seal is
    ``WA_TransparentForMouseEvents`` rather than an accept-and-ignore."""
    overlay.set_edit_mode(True)
    rect = overlay._region_rect(REGION_KEY)

    _press(overlay, rect.center().x(), rect.center().y())

    assert overlay._drag_region == REGION_KEY


def test_content_changed_re_anchors_the_region(overlay, placed) -> None:
    overlay._state.overlay_regions[REGION_KEY].dy = 420

    placed.notify_content_changed()

    assert placed.pos().y() == overlay._region_rect(REGION_KEY).y()


def test_a_notification_for_a_retired_region_is_ignored(overlay, placed) -> None:
    """A plugin can notify after its region was taken away — the same race
    ``remove_region`` closes for a drag in flight."""
    overlay.remove_region(REGION_KEY)

    overlay.region_content_changed(REGION_KEY)  # must not raise


def test_a_region_with_content_keeps_the_overlay_on_screen(qtbot, overlay) -> None:
    """``_update_visibility`` ORs the per-region predicates, which is the
    whole reason ``has_content`` is required on the spec."""
    occupied = True
    widget = PluginOverlayRegion(make_context())
    qtbot.addWidget(widget)  # no content hook: the test drives the overlay directly
    overlay.add_region(
        REGION_KEY, widget, title="Ticker", has_content=lambda: occupied, preview=widget.sample
    )

    assert overlay.isVisible()

    occupied = False
    overlay.region_content_changed(REGION_KEY)

    assert not overlay.isVisible()


def test_the_dashed_chrome_survives_the_region_re_dressing_itself(overlay, placed) -> None:
    """A skin change reaches a region as a ``setStyleSheet``, and it can land
    while position mode is up — which used to drop the dashed border the user
    was dragging by, and then restore a sheet from before the skin change."""
    overlay.set_edit_mode(True)
    assert "dashed" in placed.styleSheet()

    placed.apply_skin()

    assert "dashed" in placed.styleSheet()

    overlay.set_edit_mode(False)

    assert "dashed" not in placed.styleSheet()
    # And what is left is the region's OWN current dressing, not a snapshot
    # taken before it re-dressed.
    assert pluginskin.current().overlay_stylesheet() in placed.styleSheet()


def test_position_mode_shows_the_regions_sample_content(overlay, placed) -> None:
    overlay.set_edit_mode(True)

    assert [widget.text() for widget in placed.findChildren(QLabel)] == ["Ticker"]

    overlay.set_edit_mode(False)

    assert not placed.findChildren(QLabel)
