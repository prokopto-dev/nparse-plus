"""The application mark, as Qt wants it.

``data/ui/`` carries one PNG per size rather than one large PNG the toolkit
downscales, and this assembles them into a single :class:`QIcon` so Qt picks
the representation that matches the surface asking — 16px in the tray, 32 in a
window list, 256 in a Cmd-Tab switcher. Letting Qt smooth-scale the 256px art
down to 16 is visibly softer, and the tray is where the mark is read most.

Both places that show the mark go through here (``app.create_app`` for the
window icon, ``helpers.application`` for the tray), so adding a size means
touching :data:`nparseplus.ui.appicon.ICON_SIZES` and nothing else.

The PNGs themselves are generated from ``data/assets/icon.svg`` by
``tools/gen_icons.py`` — do not hand-edit them.
"""

from __future__ import annotations

import os

from PySide6.QtGui import QIcon, QPixmap

from nparseplus.helpers import resource_path

#: The sized siblings of ``icon.png``, which is itself the 256px render.
ICON_SIZES = (16, 24, 32, 48, 64, 128)


def icon_path(size: int | None = None) -> str:
    """Absolute path to a shipped icon PNG (frozen-build aware).

    ``size=None`` is ``icon.png``, the 256px render.
    """
    name = "icon.png" if size is None else f"icon-{size}.png"
    return resource_path(os.path.join("data", "ui", name))


def app_icon() -> QIcon:
    """Every shipped size in one icon.

    Deliberately not cached: a QIcon holds QPixmaps, which are tied to the
    running QGuiApplication, and the test suite builds and tears one down
    more than once. Reading seven small PNGs costs nothing next to that risk.
    """
    icon = QIcon()
    for size in (*ICON_SIZES, None):
        pixmap = QPixmap(icon_path(size))
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    return icon


def app_pixmap(size: int) -> QPixmap:
    """The mark at ``size`` for use inside a window (the Settings header).

    Prefers an exact render and falls back to scaling the 256px one, so a size
    that is not in :data:`ICON_SIZES` still works.
    """
    if size in ICON_SIZES:
        pixmap = QPixmap(icon_path(size))
        if not pixmap.isNull():
            return pixmap
    return app_icon().pixmap(size, size)
