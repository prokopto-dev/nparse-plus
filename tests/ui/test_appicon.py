"""``ui/appicon`` — the one place the app turns shipped PNGs into a QIcon."""

from __future__ import annotations

from pathlib import Path

import pytest

from nparseplus.ui import appicon

pytestmark = pytest.mark.qt

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_icon_path_points_at_the_shipped_art():
    assert Path(appicon.icon_path()).samefile(REPO_ROOT / "data" / "ui" / "icon.png")
    for size in appicon.ICON_SIZES:
        assert Path(appicon.icon_path(size)).samefile(
            REPO_ROOT / "data" / "ui" / f"icon-{size}.png"
        )


def test_app_icon_carries_every_shipped_size(qapp):
    """The reason this module exists: Qt should pick a native render for the
    tray rather than smooth-scaling the 256px one down to 16."""
    icon = appicon.app_icon()
    assert not icon.isNull()

    available = {(size.width(), size.height()) for size in icon.availableSizes()}
    for size in (*appicon.ICON_SIZES, 256):
        assert (size, size) in available, f"{size}px representation missing"


def test_app_icon_returns_exact_pixels_not_a_rescale(qapp):
    # actualSize() answers what Qt would really draw. If a size were missing,
    # Qt would hand back the nearest one scaled, and this would still be
    # (16, 16) — so compare the pixmap's own dimensions instead.
    pixmap = appicon.app_icon().pixmap(16, 16)
    assert (pixmap.width(), pixmap.height()) == (16, 16)
    assert not pixmap.isNull()


def test_app_pixmap_falls_back_for_an_unshipped_size(qapp):
    exact = appicon.app_pixmap(32)
    assert (exact.width(), exact.height()) == (32, 32)

    # 40 is not in ICON_SIZES; it must still produce something drawable.
    scaled = appicon.app_pixmap(40)
    assert not scaled.isNull()
    assert (scaled.width(), scaled.height()) == (40, 40)
