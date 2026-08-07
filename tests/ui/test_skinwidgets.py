"""The painted half of the skin layer — color parsing and the plate path."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel

from nparseplus.ui import skins
from nparseplus.ui.skinwidgets import (
    GemMark,
    SkinPanel,
    SkinPreview,
    notched_path,
    qcolor,
    set_caps,
)

pytestmark = pytest.mark.qt


# -- color parsing --------------------------------------------------------------


def test_qcolor_reads_every_form_a_skin_uses() -> None:
    assert qcolor("#2f9e6e").getRgb() == (47, 158, 110, 255)
    assert qcolor("rgba(6, 7, 10, 219)").getRgb() == (6, 7, 10, 219)  # Qt-style alpha
    assert qcolor("rgba(6, 7, 10, 0.5)").getRgb() == (6, 7, 10, 128)  # CSS-style alpha
    assert qcolor("rgb(1, 2, 3)").getRgb() == (1, 2, 3, 255)
    assert qcolor("transparent").alpha() == 0
    assert qcolor("").alpha() == 0


def test_qcolor_returns_transparent_rather_than_raising_on_garbage() -> None:
    """A skin typo must not take an overlay repaint down with it."""
    assert qcolor("not-a-color").alpha() == 0


def test_qcolor_scales_alpha_by_the_frame_opacity() -> None:
    assert qcolor("#2f9e6e", 0.5).alpha() == 128
    assert qcolor("rgba(0, 0, 0, 200)", 0.5).alpha() == 100
    assert qcolor("#2f9e6e", 0.0).alpha() == 0
    assert qcolor("#2f9e6e", 5).alpha() == 255  # clamped


# -- the plate path -------------------------------------------------------------


def test_notched_path_is_a_plain_rect_without_a_notch() -> None:
    rect = QRectF(0, 0, 100, 60)
    assert notched_path(rect, 0).boundingRect() == rect
    assert notched_path(rect, -4).boundingRect() == rect


def test_notched_path_cuts_eight_corners_and_keeps_the_bounds() -> None:
    rect = QRectF(0, 0, 100, 60)
    path = notched_path(rect, 9)
    assert path.boundingRect() == rect
    # An octagon: the start point, seven corners, and the closing edge back.
    assert path.elementCount() == 9
    # A notched corner is outside the shape; the middle of an edge is inside.
    assert not path.contains(rect.topLeft() + QRectF(0, 0, 1, 1).center())
    assert path.contains(QRectF(rect).center())


def test_notched_path_clamps_a_notch_bigger_than_the_rect() -> None:
    """A tiny window must not invert its own frame."""
    path = notched_path(QRectF(0, 0, 10, 6), 40)
    assert path.boundingRect() == QRectF(0, 0, 10, 6)


# -- caps -----------------------------------------------------------------------


def test_set_caps_changes_rendering_but_not_the_text(qtbot) -> None:
    """The model stays the model: group keys, clear-group menu entries and the
    window's own test hooks all read label.text()."""
    label = QLabel("a sand giant")
    qtbot.addWidget(label)
    set_caps(label)
    assert label.text() == "a sand giant"
    assert label.font().capitalization() == QFont.Capitalization.AllUppercase
    set_caps(label, False)
    assert label.font().capitalization() == QFont.Capitalization.MixedCase


# -- widgets --------------------------------------------------------------------


def test_skin_panel_reserves_room_for_the_frame_it_paints(qtbot) -> None:
    panel = SkinPanel(skins.VELIOUS)
    qtbot.addWidget(panel)
    assert panel.frame_inset() == skins.VELIOUS.plate_padding + 2
    panel.apply_skin(skins.DUXA, 0.5)
    assert panel.frame_inset() == skins.DUXA.plate_padding + 2


def test_gem_mark_collapses_for_a_skin_with_no_mark(qtbot) -> None:
    mark = GemMark(skins.VELIOUS)
    qtbot.addWidget(mark)
    assert mark.width() > 0
    mark.apply_skin(skins.LEDGER)  # Ledger's title carries no gem
    assert (mark.width(), mark.height()) == (0, 0)
    mark.apply_skin(skins.DUXA)
    assert mark.width() > 0


def test_every_skin_renders_a_preview_without_raising(qtbot) -> None:
    """The picker paints real skins at ~1/5 scale; a divide-by-zero in the row
    maths there would break Settings, not just a thumbnail."""
    preview = SkinPreview(skins.DUXA)
    qtbot.addWidget(preview)
    for name in skins.SKIN_ORDER:
        preview.set_skin(skins.SKINS[name])
        for size in ((70, 46), (200, 120), (40, 20)):
            preview.resize(*size)
            preview.grab()
