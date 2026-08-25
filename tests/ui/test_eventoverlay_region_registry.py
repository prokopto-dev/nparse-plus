"""The event overlay's region registry (#154).

The four regions used to be four parallel hardcoded tables, two of which
raised ``KeyError`` on a key they had not been told about. They are one
record each now, and the registry is mutable while the overlay is live —
which is what a plugin contributing a region needs (#155).

Regions are display-only by design; nothing here (and nothing in
``RegionRecord``) is input-related. ``has_content`` is the one behaviour hook.
"""

import gc
import weakref

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from nparseplus.config.settings import MIN_REGION_HEIGHT, OverlayRegion, WindowState
from nparseplus.ui.eventoverlay import (
    REGION_MARGIN_TOP,
    EventOverlayWindow,
    qss_id,
)

pytestmark = pytest.mark.qt


def _region_state() -> WindowState:
    return WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top"),
            "utility": OverlayRegion(anchor="top", dy=96),
            "alert": OverlayRegion(anchor="center"),
            "bars": OverlayRegion(anchor="bottom"),
        },
    )


def _twist_host(parent: QWidget | None = None) -> QWidget:
    """A stand-in for the fifth region — #41's bard twist display."""
    host = QWidget(parent)
    layout = QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    host.setLayout(layout)
    return host


def _press(overlay: EventOverlayWindow, x: int, y: int) -> None:
    pt = QPointF(x, y)
    overlay.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pt,
            pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _move(overlay: EventOverlayWindow, x: int, y: int) -> None:
    pt = QPointF(x, y)
    overlay.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            pt,
            pt,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


# -- the registry itself -------------------------------------------------------


def test_the_four_builtins_are_records_in_stacked_order(qtbot) -> None:
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)

    assert list(overlay._regions) == ["lanes", "utility", "alert", "bars"]
    assert all(record.builtin for record in overlay._regions.values())
    assert overlay._region_hosts() == {
        "lanes": overlay._lanes_host,
        "utility": overlay._utility_host,
        "alert": overlay._alert_host,
        "bars": overlay._bars_host,
    }
    # The stacked layout is unchanged by being rebuilt from the registry:
    # [lanes][utility] <stretch> [alert] <stretch> [bars].
    order = [overlay._main_layout.itemAt(i).widget() for i in range(overlay._main_layout.count())]
    assert order == [
        overlay._lanes_host,
        overlay._utility_host,
        None,
        overlay._alert_host,
        None,
        overlay._bars_host,
    ]


def test_no_region_field_is_input_related(qtbot) -> None:
    """A region is a paint surface — permanently (owner decision on #155).

    Pinned here so a later "just one flag" cannot land quietly: an
    additive-only SDK would make a speculative input field forever.
    """
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    fields = set(vars(overlay._regions["alert"]))
    assert not {f for f in fields if any(w in f for w in ("input", "mouse", "click", "focus"))}


def test_default_region_hands_back_a_copy(qtbot) -> None:
    # Callers store what they get in overlay_regions and then drag it; sharing
    # the record's own default would walk it off its anchor.
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)

    first = overlay._default_region("bars")
    first.dx = 500
    assert overlay._default_region("bars").dx == 0


# -- no KeyError is reachable from an unknown key ------------------------------


def test_an_unknown_region_key_never_raises(qtbot) -> None:
    """Every per-region lookup answers something usable for a key it has never
    heard of — a region retired between a layout pass and the next read."""
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)

    assert overlay._default_region("nope") == OverlayRegion()
    assert overlay._region_for("nope") == OverlayRegion()
    assert overlay._min_region_height("nope") == MIN_REGION_HEIGHT
    width, height = overlay._region_size("nope", OverlayRegion())
    assert width > 0 and height > 0
    assert overlay._region_rect("nope").isValid()
    assert "nope" not in overlay._region_titles


def test_region_chrome_covers_every_registered_region(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.add_region(
        "twist",
        _twist_host(),
        title="Twist",
        has_content=lambda: False,
        default=OverlayRegion(anchor="top", dy=200),
    )

    overlay._set_region_chrome(True)
    assert set(overlay._region_titles) == {"lanes", "utility", "alert", "bars", "twist"}
    # isHidden, not isVisible: a child of a window that has not been shown is
    # never "visible" whatever its own flag says.
    assert not any(chip.isHidden() for chip in overlay._region_titles.values())
    overlay._position_region_chrome()

    overlay._set_region_chrome(False)
    assert all(chip.isHidden() for chip in overlay._region_titles.values())


# -- add_region / remove_region ------------------------------------------------


def test_added_region_lays_out_at_its_default_anchor(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)  # _region_at hit-tests only VISIBLE hosts
    host = _twist_host()
    host.setFixedSize(240, 40)

    assert overlay.add_region(
        "twist",
        host,
        title="Twist",
        has_content=lambda: False,
        default=OverlayRegion(anchor="top", dx=10, dy=180),
        default_width=lambda: 240,
    )

    assert host.parent() is overlay
    assert host.x() == 1000 // 2 + 10 - 240 // 2
    assert host.y() == REGION_MARGIN_TOP + 180
    assert overlay._region_at(QPoint(host.x() + 5, host.y() + 5)) == "twist"


def test_a_fifth_region_can_keep_the_overlay_on_screen_by_itself(qtbot) -> None:
    """The reason ``has_content`` is per-region: an OR over four literals
    could never let a contributed region show the overlay."""
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay._update_visibility()
    assert not overlay.isVisible()

    twisting = {"on": False}
    overlay.add_region(
        "twist",
        _twist_host(),
        title="Twist",
        has_content=lambda: twisting["on"],
        default=OverlayRegion(anchor="top", dy=200),
    )
    assert not overlay.isVisible()  # still nothing to show

    twisting["on"] = True
    overlay._update_visibility()
    assert overlay.isVisible()

    twisting["on"] = False
    overlay._update_visibility()
    assert not overlay.isVisible()


def test_removing_the_only_populated_region_hides_the_overlay(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.add_region(
        "twist", _twist_host(), title="Twist", has_content=lambda: True, default=OverlayRegion()
    )
    assert overlay.isVisible()

    assert overlay.remove_region("twist")
    assert not overlay.isVisible()


def test_remove_region_hands_the_host_back_and_takes_its_chip(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    host = _twist_host()
    overlay.add_region("twist", host, title="Twist", has_content=lambda: False)
    chip = overlay._region_titles["twist"]

    assert overlay.remove_region("twist")
    assert "twist" not in overlay._regions
    assert "twist" not in overlay._region_titles
    assert host.parent() is None  # the caller owns it; not deleted
    assert host.isHidden()
    assert chip.isHidden()
    # Idempotent.
    assert not overlay.remove_region("twist")


def test_add_region_replaces_and_never_duplicates(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    first, second = _twist_host(), _twist_host()

    overlay.add_region("twist", first, title="Twist", has_content=lambda: False)
    overlay.add_region("twist", second, title="Twist v2", has_content=lambda: False)

    assert list(overlay._regions).count("twist") == 1
    assert overlay._regions["twist"].host is second
    assert overlay._region_titles["twist"].text() == "Twist v2"
    assert first.parent() is None


def test_builtin_regions_are_neither_removable_nor_replaceable(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)

    assert not overlay.remove_region("alert")
    assert not overlay.add_region(
        "alert", _twist_host(), title="Hijacked", has_content=lambda: True
    )
    assert overlay._regions["alert"].host is overlay._alert_host
    assert overlay._region_titles["alert"].text() == "Alerts"


def test_a_host_with_no_object_name_gets_one_a_selector_can_use(qtbot) -> None:
    # An #id selector cannot carry a dot, and a malformed selector makes Qt
    # discard the WHOLE sheet with only a runtime warning.
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    host = _twist_host()
    overlay.add_region("plugin.demo.map", host, title="Map", has_content=lambda: False)

    assert host.objectName() == "OverlayRegion_plugin_demo_map"
    assert qss_id("plugin.demo.map") == "plugin_demo_map"
    overlay._set_region_chrome(True)
    assert f"#{host.objectName()}" in host.styleSheet()


def test_region_chrome_preserves_a_contributed_hosts_own_stylesheet(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    host = _twist_host()
    host.setObjectName("TwistHost")
    host.setStyleSheet("#TwistHost { color: red; }")
    overlay.add_region("twist", host, title="Twist", has_content=lambda: False)

    overlay._set_region_chrome(True)
    assert "color: red" in host.styleSheet()
    assert "dashed" in host.styleSheet()

    overlay._set_region_chrome(False)
    assert host.styleSheet() == "#TwistHost { color: red; }"


def test_a_seeded_layout_includes_regions_added_since(qtbot) -> None:
    # The first region drag seeds overlay_regions from the registry, so a
    # contributed region is seeded at its own default rather than (0, 0).
    overlay = EventOverlayWindow(state=WindowState(geometry=(0, 0, 1000, 800)))
    qtbot.addWidget(overlay)
    overlay.add_region(
        "twist",
        _twist_host(),
        title="Twist",
        has_content=lambda: False,
        default=OverlayRegion(anchor="bottom", dy=-40),
    )

    overlay._begin_region_edit("bars", QPoint(500, 700))

    regions = overlay._state.overlay_regions
    assert set(regions) == {"lanes", "utility", "alert", "bars", "twist"}
    assert regions["twist"] == OverlayRegion(anchor="bottom", dy=-40)


# -- bug 1: a region can disappear mid-drag ------------------------------------


def test_removing_the_dragged_region_drops_the_drag(qtbot) -> None:
    """Position mode drops ``WindowTransparentForInput``, so the overlay is
    clickable — a region really can be retired mid-drag, and the drag key left
    behind used to index ``overlay_regions`` unguarded."""
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    host = _twist_host()
    host.setFixedSize(240, 60)
    overlay.add_region(
        "twist",
        host,
        title="Twist",
        has_content=lambda: True,
        default=OverlayRegion(anchor="top", dy=200),
        default_width=lambda: 240,
    )
    overlay.set_edit_mode(True)
    overlay._layout_regions()

    _press(overlay, host.x() + host.width() // 2, host.y() + host.height() // 2)
    assert overlay._drag_region == "twist"

    overlay.remove_region("twist")
    assert overlay._drag_region is None

    # A move that arrives after the removal must not raise.
    _move(overlay, host.x() + 40, host.y() + 40)
    assert overlay._drag_region is None


def test_a_move_after_the_region_vanished_from_settings_is_survivable(qtbot) -> None:
    """Belt to ``remove_region``'s braces: both drag paths READ the placement
    rather than indexing it, so a key that outlives its entry cannot crash the
    Qt event loop of an always-on-top window over the game."""
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    overlay._layout_regions()

    lanes = overlay._lanes_host
    _press(overlay, lanes.x() + lanes.width() // 2, lanes.y() + lanes.height() // 2)
    assert overlay._drag_region == "lanes"

    del overlay._state.overlay_regions["lanes"]
    _move(overlay, lanes.x() + 30, lanes.y() + 30)
    assert overlay._drag_region is None

    # The resize path too.
    _press(overlay, lanes.x() + lanes.width() // 2, lanes.y() + lanes.height() // 2)
    overlay._drag_region = "gone"
    overlay._region_resize_edges = Qt.Edge.RightEdge
    overlay._apply_region_resize(QPoint(10, 10))  # must not raise


# -- bug 2: the legacy stacked layout is insertion-order-sensitive -------------


def test_a_region_added_in_stacked_mode_lands_in_its_own_band(qtbot) -> None:
    """With ``overlay_regions is None`` the hosts sit around two stretch items,
    so a plain ``addWidget`` would append below the bottom stretch — under the
    timer bars — whatever the region's anchor says."""
    overlay = EventOverlayWindow(state=WindowState(geometry=(0, 0, 1000, 800)))
    qtbot.addWidget(overlay)
    assert not overlay._region_mode()

    top, middle, bottom = _twist_host(), _twist_host(), _twist_host()
    overlay.add_region(
        "twist-top", top, title="T", has_content=lambda: False, default=OverlayRegion(anchor="top")
    )
    overlay.add_region(
        "twist-mid",
        middle,
        title="M",
        has_content=lambda: False,
        default=OverlayRegion(anchor="center"),
    )
    overlay.add_region(
        "twist-bot",
        bottom,
        title="B",
        has_content=lambda: False,
        default=OverlayRegion(anchor="bottom"),
    )

    layout = overlay._main_layout
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order == [
        overlay._lanes_host,
        overlay._utility_host,
        top,
        None,  # stretch(2)
        overlay._alert_host,
        middle,
        None,  # stretch(3)
        overlay._bars_host,
        bottom,
    ]
    assert not top.isHidden()


def test_retiring_a_region_restores_the_stacked_layout(qtbot) -> None:
    overlay = EventOverlayWindow(state=WindowState(geometry=(0, 0, 1000, 800)))
    qtbot.addWidget(overlay)
    before = [overlay._main_layout.itemAt(i).widget() for i in range(overlay._main_layout.count())]

    host = _twist_host()
    overlay.add_region(
        "twist", host, title="T", has_content=lambda: False, default=OverlayRegion(anchor="top")
    )
    assert overlay._main_layout.indexOf(host) >= 0

    overlay.remove_region("twist")
    after = [overlay._main_layout.itemAt(i).widget() for i in range(overlay._main_layout.count())]
    assert after == before
    assert overlay._main_layout.indexOf(host) < 0


def test_activating_region_layout_takes_a_contributed_host_out_too(qtbot) -> None:
    overlay = EventOverlayWindow(state=WindowState(geometry=(0, 0, 1000, 800)))
    qtbot.addWidget(overlay)
    host = _twist_host()
    overlay.add_region(
        "twist", host, title="T", has_content=lambda: False, default=OverlayRegion(anchor="top")
    )

    overlay._begin_region_edit("twist", QPoint(500, 300))

    assert overlay._region_mode()
    assert overlay._main_layout.indexOf(host) < 0


def test_a_region_removed_in_stacked_mode_can_be_added_back(qtbot) -> None:
    overlay = EventOverlayWindow(state=WindowState(geometry=(0, 0, 1000, 800)))
    qtbot.addWidget(overlay)
    host = _twist_host()
    spec = dict(title="T", has_content=lambda: False, default=OverlayRegion(anchor="center"))

    overlay.add_region("twist", host, **spec)
    overlay.remove_region("twist")
    overlay.add_region("twist", host, **spec)

    assert overlay._main_layout.indexOf(host) >= 0
    assert not host.isHidden()  # re-joining a layout must un-hide it


# -- position mode is live too -------------------------------------------------


def test_a_region_added_during_position_mode_gets_chrome_and_a_sample(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.set_edit_mode(True)
    baseline = len(overlay._preview_widgets)

    host = _twist_host()
    samples: list[QLabel] = []

    def sample() -> list[QLabel]:
        label = QLabel("TWIST", host)
        host.layout().addWidget(label)
        samples.append(label)
        return [label]

    overlay.add_region(
        "twist",
        host,
        title="Twist",
        has_content=lambda: False,
        default=OverlayRegion(anchor="top", dy=200),
        preview=sample,
    )

    assert len(overlay._preview_widgets) == baseline + 1
    assert samples[0] in overlay._preview_widgets
    assert overlay._region_titles["twist"].isVisible()
    assert "dashed" in host.styleSheet()


def test_retiring_a_region_takes_its_sample_content_with_it(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    host = _twist_host()

    def sample() -> list[QLabel]:
        label = QLabel("TWIST", host)
        host.layout().addWidget(label)
        return [label]

    overlay.add_region("twist", host, title="Twist", has_content=lambda: False, preview=sample)
    overlay.set_edit_mode(True)
    baseline = len(overlay._preview_widgets)
    assert baseline == 6  # the four built-ins' five samples + this one

    overlay.remove_region("twist")
    assert len(overlay._preview_widgets) == baseline - 1

    overlay.set_edit_mode(False)
    assert overlay._preview_widgets == []


def test_a_region_with_no_preview_factory_simply_shows_nothing(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.add_region("twist", _twist_host(), title="Twist", has_content=lambda: False)

    overlay.set_edit_mode(True)
    assert len(overlay._preview_widgets) == 5
    overlay.set_edit_mode(False)
    assert overlay._preview_widgets == []


def test_a_contributed_region_can_carry_its_own_drag_floor(qtbot) -> None:
    overlay = EventOverlayWindow(state=_region_state())
    qtbot.addWidget(overlay)
    overlay.add_region(
        "twist",
        _twist_host(),
        title="Twist",
        has_content=lambda: False,
        min_height=lambda: 90,
    )

    assert overlay._min_region_height("twist") == 90
    assert overlay._min_region_height("bars") == MIN_REGION_HEIGHT
    # Never below the structural floor, whatever the record says.
    overlay.add_region(
        "tiny", _twist_host(), title="Tiny", has_content=lambda: False, min_height=lambda: 2
    )
    assert overlay._min_region_height("tiny") == MIN_REGION_HEIGHT


def test_the_overlay_is_still_freed_by_refcounting(qapp) -> None:
    """The registry must not put the WINDOW in a Python reference cycle.

    A cycle hands the overlay's destruction to the cyclic collector, which
    runs whenever it likes — and a QWidget freed there rather than by Qt is a
    use-after-free the next repaint walks into. It crashed this suite from
    inside ``paintEvent`` (pytest-qt tracks widgets by weakref, so it is the
    refcount that is supposed to end them). ``weak_hook`` is why the built-in
    records do not reintroduce it; this is the guard, and it is why the
    assertion runs with the collector switched off.
    """
    gc.disable()
    try:
        overlay = EventOverlayWindow(state=_region_state())
        ref = weakref.ref(overlay)
        del overlay
        assert ref() is None
    finally:
        gc.enable()
