"""Map chrome geometry + the backdrop/window-opacity split.

The interesting parts of the chrome are pure: which edge a zone line projects
onto, what the recenter puck should say, how a ``to_*`` label reads. Those run
with no window at all.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QRegion
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsView, QWidget

from nparseplus.helpers import config
from nparseplus.parsers.maps import chrome
from nparseplus.parsers.maps.mapcanvas import BACKDROP_EDGE_PX, MapCanvas

pytestmark = pytest.mark.qt


@pytest.fixture
def canvas(qtbot, tmp_path):
    """A MapCanvas over a scratch legacy config (never the developer's)."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    widget = MapCanvas()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def canvas_no_antialias(qtbot, tmp_path):
    """A MapCanvas built with the maps.antialias escape hatch off."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["maps"]["antialias"] = False
    widget = MapCanvas()
    qtbot.addWidget(widget)
    return widget


# -- bearings -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dx", "dy", "expected"),
    [
        (0, -10, "N"),  # dy grows downward in Qt, so up is negative
        (10, -10, "NE"),
        (10, 0, "E"),
        (10, 10, "SE"),
        (0, 10, "S"),
        (-10, 10, "SW"),
        (-10, 0, "W"),
        (-10, -10, "NW"),
    ],
)
def test_compass_uses_screen_space_where_down_is_south(dx, dy, expected) -> None:
    assert chrome.compass_name(dx, dy) == expected
    assert chrome.bearing_arrow(dx, dy) == chrome.ARROWS[chrome.COMPASS.index(expected)]


def test_bearing_of_a_zero_vector_is_north_not_a_crash() -> None:
    assert chrome.bearing_index(0, 0) == 0


def test_bearing_sectors_round_to_the_nearest_of_eight() -> None:
    # Just past due north, both ways, still reads north.
    assert chrome.compass_name(1, -100) == "N"
    assert chrome.compass_name(-1, -100) == "N"
    # Past the 22.5° boundary it steps.
    assert chrome.compass_name(100, -100) == "NE"


# -- distances ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("units", "expected"),
    [(0, "0"), (840, "840"), (999.4, "999"), (1014, "1.0k"), (5500, "5.5k"), (14000, "14k")],
)
def test_format_distance_stays_short_enough_for_the_puck(units, expected) -> None:
    assert chrome.format_distance(units) == expected


def test_format_distance_ignores_sign() -> None:
    assert chrome.format_distance(-1200) == chrome.format_distance(1200)


# -- edge anchoring -------------------------------------------------------------


def test_edge_anchor_parks_on_the_border_you_would_leave_through() -> None:
    width, height = 500, 420
    # Straight up -> the top edge, horizontally centred.
    x, y = chrome.edge_anchor(0, -100, width, height)
    assert (x, y) == (250, 0)
    # Straight right -> the right edge, vertically centred.
    x, y = chrome.edge_anchor(100, 0, width, height)
    assert (x, y) == (500, 210)
    # Down-left -> the left or bottom border, never past either.
    x, y = chrome.edge_anchor(-100, 100, width, height)
    assert 0 <= x <= width and 0 <= y <= height
    assert x == 0 or y == height


def test_edge_anchor_respects_the_inset() -> None:
    _x, y = chrome.edge_anchor(0, -100, 500, 420, inset=6)
    assert y == 6


def test_edge_anchor_of_a_zero_vector_does_not_divide_by_zero() -> None:
    assert chrome.edge_anchor(0, 0, 500, 420) == (250, 0)


# -- zone-line labels -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("to_Northern_Ro", "Northern Ro"),
        ("✪ to_Southern_Ro", "Southern Ro"),
        ("to Northern Ro", "Northern Ro"),
        ("  to_The_Hole  ", "The Hole"),
    ],
)
def test_zone_line_label_reads_the_maps_own_convention(raw, expected) -> None:
    assert chrome.zone_line_label(raw) == expected
    assert chrome.is_zone_line(raw)


@pytest.mark.parametrize("raw", ["Tolan", "a sand giant", "Torch", "", "  "])
def test_ordinary_labels_are_not_zone_lines(raw) -> None:
    """``Tolan``/``Torch`` start with "to" — the underscore/space is the tell."""
    assert not chrome.is_zone_line(raw)


# -- respawn --------------------------------------------------------------------


def test_format_respawn_accepts_both_shapes_the_app_produces() -> None:
    # core.zones hands back seconds; MapData.get_default_spawn_timer a literal.
    assert chrome.format_respawn(400) == "6:40"
    assert chrome.format_respawn(3720) == "1:02:00"
    assert chrome.format_respawn("16:30") == "16:30"
    assert chrome.format_respawn(None) == "—"
    assert chrome.format_respawn(0) == "—"


# -- placement ------------------------------------------------------------------


def _panel(parent: QWidget, height: int) -> QWidget:
    widget = QWidget(parent)
    widget.setFixedHeight(height)
    return widget


def test_place_chrome_pins_the_strips_and_parks_the_puck(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(500, 420)
    header, toolbar = _panel(host, 39), _panel(host, 20)
    rail, puck = QWidget(host), _panel(host, 42)
    puck.setFixedWidth(42)

    chrome.place_chrome(QRect(0, 0, 500, 420), header, toolbar, rail, puck, rail_open=False)
    assert header.geometry() == QRect(0, 0, 500, 39)
    assert toolbar.geometry() == QRect(0, 400, 500, 20)
    # The puck sits above the toolbar, inside the right edge.
    assert puck.x() + puck.width() < 500
    assert puck.y() + puck.height() <= 400


def test_an_open_rail_owns_the_right_column(qtbot) -> None:
    """The strips stop at the rail instead of sliding under it."""
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(500, 420)
    header, toolbar = _panel(host, 39), _panel(host, 20)
    rail, puck = QWidget(host), _panel(host, 42)
    puck.setFixedWidth(42)

    chrome.place_chrome(QRect(0, 0, 500, 420), header, toolbar, rail, puck, rail_open=True)
    assert header.width() == 500 - chrome.MapRail.WIDTH
    assert toolbar.width() == 500 - chrome.MapRail.WIDTH
    assert rail.geometry() == QRect(500 - chrome.MapRail.WIDTH, 0, chrome.MapRail.WIDTH, 420)
    assert puck.x() + puck.width() <= header.width()


def test_the_rail_never_eats_a_narrow_window_whole(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    header, toolbar = _panel(host, 39), _panel(host, 20)
    rail, puck = QWidget(host), _panel(host, 42)
    puck.setFixedWidth(42)
    chrome.place_chrome(QRect(0, 0, 120, 200), header, toolbar, rail, puck, rail_open=True)
    assert rail.width() <= 120 - 40
    assert header.width() > 0


# -- the backdrop, split off window opacity -------------------------------------


def test_backdrop_opacity_moves_only_the_scene_fill(canvas) -> None:
    canvas.apply_backdrop_opacity(0)
    assert canvas.backgroundBrush().color().alpha() == 0
    canvas.apply_backdrop_opacity(62)
    assert canvas.backgroundBrush().color().alpha() == 158
    canvas.apply_backdrop_opacity(100)
    assert canvas.backgroundBrush().color().getRgb() == (0, 0, 0, 255)
    # The window's own opacity is a separate control and stays untouched.
    assert canvas.windowOpacity() == 1.0


def test_backdrop_opacity_clamps_out_of_range_values(canvas) -> None:
    canvas.apply_backdrop_opacity(-40)
    assert canvas.backdrop_opacity() == 0
    canvas.apply_backdrop_opacity(400)
    assert canvas.backdrop_opacity() == 100


def test_applying_the_backdrop_does_not_adopt_it_as_the_setting(canvas) -> None:
    """The idle fade drives apply_ directly; if that wrote the setting, an
    idle map would forget the value the user picked."""
    canvas.set_backdrop_opacity(70)
    assert config.data["maps"]["backdrop_opacity"] == 70
    canvas.apply_backdrop_opacity(0)
    assert config.data["maps"]["backdrop_opacity"] == 70
    assert canvas.backdrop_opacity() == 0


def test_the_wheel_band_hugs_every_edge(canvas) -> None:
    canvas.resize(400, 300)
    inside = BACKDROP_EDGE_PX - 1
    for point in (
        QPointF(inside, 150),  # left
        QPointF(400 - inside, 150),  # right
        QPointF(200, inside),  # top
        QPointF(200, 300 - inside),  # bottom
    ):
        assert canvas._in_backdrop_band(point)
    assert not canvas._in_backdrop_band(QPointF(200, 150))


# -- the backdrop erases, it does not tint --------------------------------------
#
# A QImage here stands in for the viewport's backing store: Qt repaints INTO
# the pixels the last frame left, so anything the backdrop merely composites
# onto survives as a ghost. Rendering twice into one image is that, exactly.


def _repaint(canvas, image) -> None:
    """One repaint of the whole viewport into ``image``, keeping its content."""
    canvas.viewport().render(image, QPoint(), QRegion(image.rect()))


def _frame(canvas, fill=None) -> QImage:
    image = QImage(canvas.width(), canvas.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(fill if fill is not None else QColor(0, 0, 0, 0))
    return image


def test_the_backdrop_replaces_what_it_covers(canvas) -> None:
    """The reported Windows ghosting: below 100% the fill used to composite
    over the previous frame, so the map darkened it instead of erasing it."""
    canvas.resize(200, 200)
    canvas.apply_backdrop_opacity(50)
    image = _frame(canvas, QColor(255, 0, 0, 255))  # last frame's leftovers

    _repaint(canvas, image)

    assert image.pixelColor(100, 100) == QColor(0, 0, 0, 128)
    _repaint(canvas, image)  # and it is stable, not converging frame by frame
    assert image.pixelColor(100, 100) == QColor(0, 0, 0, 128)


def test_a_transparent_backdrop_is_glass_not_whatever_was_there(canvas) -> None:
    canvas.resize(200, 200)
    canvas.apply_backdrop_opacity(0)
    image = _frame(canvas, QColor(255, 0, 0, 255))

    _repaint(canvas, image)

    assert image.pixelColor(100, 100).alpha() == 0


def test_a_dot_that_moves_leaves_no_ghost_of_itself(canvas) -> None:
    """'Multiple locs get drawn': your marker at every place it has been."""
    canvas.resize(200, 200)
    canvas.apply_backdrop_opacity(50)
    canvas.setSceneRect(-100, -100, 200, 200)
    dot = QGraphicsEllipseItem(-8, -8, 16, 16)
    dot.setBrush(QBrush(QColor(61, 235, 52)))  # YOU_COLOR
    dot.setPen(QPen(QColor(61, 235, 52), 2))
    canvas._scene.addItem(dot)
    dot.setPos(0, 0)
    canvas.centerOn(0, 0)
    was_at = canvas.mapFromScene(QPointF(0, 0))

    image = _frame(canvas)
    _repaint(canvas, image)
    assert image.pixelColor(was_at).green() > 100, "the dot should be drawn to begin with"

    dot.setPos(60, 0)
    _repaint(canvas, image)

    assert image.pixelColor(was_at) == QColor(0, 0, 0, 128)


def test_the_backdrop_leaves_the_painter_as_it_found_it(canvas) -> None:
    """DontSavePainterState means Qt does not save/restore around
    drawBackground, so a leaked Source mode would make every item paint holes
    in the backdrop instead of over it."""
    image = _frame(canvas, QColor(0, 0, 0, 0))
    painter = QPainter(image)
    try:
        canvas.drawBackground(painter, QRectF(0, 0, 10, 10))
        assert painter.compositionMode() == QPainter.CompositionMode.CompositionMode_SourceOver
    finally:
        painter.end()


def test_a_see_through_backdrop_gives_up_the_partial_repaint_fast_path(canvas) -> None:
    """Minimal updates rely on the fill covering the region it repaints — and
    on Qt's scroll blit, which smears translucent ink. Opaque keeps them."""
    canvas.apply_backdrop_opacity(100)
    assert canvas.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
    canvas.apply_backdrop_opacity(60)
    assert canvas.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    canvas.apply_backdrop_opacity(0)  # the idle fade lands here too
    assert canvas.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate


def test_antialiased_items_keep_their_dirty_rect_padding(canvas) -> None:
    """The other half of the trail: an antialiased pen inks outside its own
    boundingRect, and DontAdjustForAntialiasing drops the padding that would
    have repainted the fringe — Qt documents that as leaving painting traces."""
    assert canvas.renderHints() & QPainter.RenderHint.Antialiasing
    flags = canvas.optimizationFlags()
    assert not (flags & QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing)
    assert flags & QGraphicsView.OptimizationFlag.DontSavePainterState


def test_turning_antialiasing_off_restores_the_fast_path(canvas_no_antialias) -> None:
    flags = canvas_no_antialias.optimizationFlags()
    assert flags & QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing


# -- the map chrome follows the skin --------------------------------------------


def test_map_colors_differ_by_skin() -> None:
    """The map used to hardcode Duxa's palette, so it was the one surface the
    skin picker never reached."""
    from nparseplus.ui import skins

    seen = {name: chrome.map_colors(skin) for name, skin in skins.SKINS.items()}
    assert len({c.gold for c in seen.values()}) == len(seen)
    assert len({c.ink_solid for c in seen.values()}) > 1


def test_velious_gives_the_map_a_warm_stone_ground() -> None:
    """The payoff of deriving ink from the skin's plate rather than a literal."""
    from nparseplus.ui import skins

    velious = chrome.map_colors(skins.VELIOUS)
    duxa = chrome.map_colors(skins.DUXA)
    assert velious.ink_solid != duxa.ink_solid
    assert skins.base_color((velious.ink_solid,)) == skins.base_color(skins.VELIOUS.plate)


def test_map_colors_never_returns_an_empty_string() -> None:
    """Ledger's mark_color is "" — the derivation must go through
    chrome_accent, or the map loses its gold entirely under that skin."""
    from dataclasses import fields

    from nparseplus.ui import skins

    for name, skin in skins.SKINS.items():
        colors = chrome.map_colors(skin)
        for field in fields(colors):
            assert getattr(colors, field.name), (name, field.name)


def test_the_status_accents_stay_semantic() -> None:
    """A reachable exit and a live recording mean a thing, not "the chrome's
    gold" — they must not move when the skin's frame colours do."""
    from nparseplus.ui import chrome as ui_chrome

    assert chrome.GREEN == ui_chrome.GOOD
    assert chrome.AMBER == ui_chrome.ROLL
