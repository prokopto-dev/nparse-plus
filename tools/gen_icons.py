#!/usr/bin/env python
"""Generate every shipped icon and brand image from the SVG master.

``data/assets/icon.svg`` is the source of truth; everything this writes is a
committed build artifact of it, the same arrangement as ``convert_zones.py``
and ``gen_registry_schema.py``. Nothing here is imported by the app — it is
tooling, so it may use Qt freely.

Usage::

    uv run python tools/gen_icons.py              # rewrite the committed art
    uv run python tools/gen_icons.py --iconset D  # stage a macOS .iconset in D

**No new dependencies.** PySide6 is already a runtime dependency and
``QSvgRenderer`` rasterizes SVG, so there is no cairosvg and no Pillow here
(Pillow is in the ``build`` group for the Windows splash screen only, and must
stay there). The multi-size ``.ico`` is assembled from those PNGs with stdlib
``struct``: a PNG-embedded ICO is valid and is what Windows has read since
Vista. The ``.icns`` still goes through ``iconutil``, which is macOS-only —
``packaging/make_icns.sh`` calls this script with ``--iconset`` to stage it.

There is deliberately **no ``--check`` mode**. Qt's rasterizer is not
byte-stable across PySide6 versions, so a staleness check would fail on a
version bump rather than on a real edit. ``tests/tools/test_gen_icons.py``
asserts the committed artifacts' shape instead — sizes, alpha, and that the
ICO directory parses and lists what it claims.

Why one PNG per size rather than one large PNG the toolkit downscales: the
mark is tuned so its strokes land on whole pixels at 16px (see the geometry
notes in the SVG). A smooth downscale of the 256px art is visibly softer at
tray size, which is exactly where the icon is read most.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtSvg import QSvgRenderer

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER = REPO_ROOT / "data" / "assets" / "icon.svg"
UI_DIR = REPO_ROOT / "data" / "ui"
DOCS_IMAGES = REPO_ROOT / "docs" / "assets" / "images"
FONT_DIR = REPO_ROOT / "data" / "fonts"

#: Sized PNG siblings of ``icon.png``. 16/32/48 are the desktop sizes that
#: matter (tray, taskbar, window list); 24 is Windows' small-icon step; 128 is
#: what a Linux app grid asks for. ``icon.png`` itself is the 256 render, and
#: is the name the Flatpak manifest and every legacy caller already use.
ICON_SIZES = (16, 24, 32, 48, 64, 128)
BASE_SIZE = 256

#: What goes in the Windows .ico. 256 has to be PNG-embedded (a 256px BMP
#: entry is not representable), which is why the whole file is PNG-embedded.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: Apple's iconset ladder. Every entry is a native render off the vector, not
#: an upscale — which is the thing the old make_icns.sh admitted it could not
#: do from 64x64 source art.
ICNS_SIZES = (16, 32, 128, 256, 512)

SOCIAL_SIZE = (1280, 640)  # GitHub's social-preview canvas
LOCKUP_HEIGHT = 300  # the README banner; its width is measured from the type

# Palette, mirroring data/assets/icon.svg (which mirrors ui/skins.py).
GOLD_BRIGHT = "#e2c882"
GOLD_MID = "#c8a951"
GOLD_DIM = "#8a7549"
FRAME = "#6b5a3a"
GLASS_TOP = "#0d0f14"
GLASS_BOTTOM = "#06070a"
INK = "#e6dfd0"
INK_DIM = "#968c7b"

TAGLINE = "EverQuest Project 1999 companion overlay"
PLATFORMS = "macOS  ·  Windows  ·  Linux"


@dataclass(frozen=True)
class Written:
    path: Path
    note: str


# -- rasterizing ---------------------------------------------------------------


def render(size: int) -> QImage:
    """The master, drawn square at ``size`` with an alpha background."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    QSvgRenderer(str(MASTER)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    # The QByteArray must outlive the QBuffer, so hold it in a local rather
    # than passing a temporary — PySide6 segfaults on the latter.
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(store)


def build_ico(entries: list[tuple[int, bytes]]) -> bytes:
    """A PNG-embedded multi-size ICO.

    ICONDIR (6 bytes) + one 16-byte ICONDIRENTRY per image + the PNG payloads.
    A 256px entry records its dimension as 0, which is the format's way of
    saying "256" in a byte.
    """
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(header) + 16 * len(entries)
    directory = b""
    payload = b""
    for size, data in entries:
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,  # palette entries: 0 for a truecolour image
            0,  # reserved
            1,  # colour planes
            32,  # bits per pixel
            len(data),
            offset,
        )
        offset += len(data)
        payload += data
    return header + directory + payload


# -- brand images ----------------------------------------------------------------


def load_font_family() -> str:
    """The bundled Noto Sans, the same faces ``app.create_app`` registers.

    Registering it here rather than naming a system font keeps the wordmark
    identical on every machine that regenerates these images.
    """
    family = "Noto Sans"
    for face in ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(FONT_DIR / face))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                family = families[0]
    return family


def _font(family: str, pixels: int, *, bold: bool = False, tracking: int = 100) -> QFont:
    font = QFont(family)
    font.setPixelSize(pixels)
    font.setBold(bold)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
    return font


def _glass(width: int, height: int) -> QLinearGradient:
    gradient = QLinearGradient(0, 0, 0, height)
    gradient.setColorAt(0, QColor(GLASS_TOP))
    gradient.setColorAt(1, QColor(GLASS_BOTTOM))
    return gradient


def _notched_rect(x: float, y: float, w: float, h: float, notch: float) -> QPainterPath:
    """The plate's octagon, at an arbitrary aspect — the icon's corner motif
    reused as a frame so the banner and the mark share one geometry."""
    path = QPainterPath()
    path.moveTo(x + notch, y)
    path.lineTo(x + w - notch, y)
    path.lineTo(x + w, y + notch)
    path.lineTo(x + w, y + h - notch)
    path.lineTo(x + w - notch, y + h)
    path.lineTo(x + notch, y + h)
    path.lineTo(x, y + h - notch)
    path.lineTo(x, y + notch)
    path.closeSubpath()
    return path


#: The gap between "nParse" and "+", as a fraction of the type size.
_PLUS_GAP = 0.08


def _wordmark_font(family: str, pixels: int) -> QFont:
    return _font(family, pixels, bold=True, tracking=99)


def _wordmark_width(family: str, pixels: int) -> float:
    metrics = QFontMetrics(_wordmark_font(family, pixels))
    return metrics.horizontalAdvance("nParse") + pixels * _PLUS_GAP + metrics.horizontalAdvance("+")


def _draw_wordmark(
    painter: QPainter, family: str, pixels: int, left: float, baseline: float
) -> None:
    """``nParse+`` with the plus in Velious gold, set from ``left``.

    The plus is the one part of the name the icon deliberately does not carry
    (a "+" set into the ring is a two-pixel smudge at 16px), so the wordmark
    is where it has to read.
    """
    font = _wordmark_font(family, pixels)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    painter.setPen(QColor(INK))
    painter.drawText(int(left), int(baseline), "nParse")
    painter.setPen(QColor(GOLD_BRIGHT))
    plus_x = left + metrics.horizontalAdvance("nParse") + pixels * _PLUS_GAP
    painter.drawText(int(plus_x), int(baseline), "+")


def _draw_text(
    painter: QPainter, text: str, font: QFont, color: str, left: float, baseline: float
) -> None:
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(int(left), int(baseline), text)


def _draw_centered(
    painter: QPainter, text: str, font: QFont, color: str, center_x: float, baseline: float
) -> None:
    width = QFontMetrics(font).horizontalAdvance(text)
    _draw_text(painter, text, font, color, center_x - width / 2, baseline)


def _new_canvas(width: int, height: int) -> tuple[QImage, QPainter]:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    return image, painter


def draw_lockup(family: str) -> QImage:
    """The horizontal banner: mark, wordmark, tagline.

    It carries its own dark plate rather than sitting on transparency because
    GitHub renders README images on both a light and a dark ground, and a
    light-on-transparent wordmark disappears on one of them.

    The canvas is measured, not fixed: the type is set left-aligned against
    the mark, so the banner's width falls out of how wide the wordmark and
    tagline actually are. A fixed canvas left a third of the plate empty.
    """
    height = LOCKUP_HEIGHT
    mark = 188
    pad, gap = 84, 48

    tag_font = _font(family, 27, tracking=102)
    text_width = max(
        _wordmark_width(family, 104), QFontMetrics(tag_font).horizontalAdvance(TAGLINE)
    )
    width = int(pad + mark + gap + text_width + pad)

    image, painter = _new_canvas(width, height)
    painter.fillPath(_notched_rect(0, 0, width, height, 26), _glass(width, height))
    painter.setPen(QPen(QColor(FRAME), 3))
    painter.drawPath(_notched_rect(1.5, 1.5, width - 3, height - 3, 25))

    QSvgRenderer(str(MASTER)).render(painter, QRectF(pad, (height - mark) / 2, mark, mark))

    text_left = pad + mark + gap
    _draw_wordmark(painter, family, 104, text_left, height / 2 + 6)
    _draw_text(painter, TAGLINE, tag_font, INK_DIM, text_left + 3, height / 2 + 58)

    painter.end()
    return image


def draw_social(family: str) -> QImage:
    """The 1280x640 GitHub social-preview card.

    Content is stacked and centred: GitHub crops this to several aspect ratios
    across its surfaces, and anything pinned to an edge is what gets cut.
    """
    width, height = SOCIAL_SIZE
    image, painter = _new_canvas(width, height)
    painter.fillRect(0, 0, width, height, _glass(width, height))

    painter.setPen(QPen(QColor(FRAME), 3))
    painter.drawPath(_notched_rect(38, 38, width - 76, height - 76, 34))
    painter.setPen(QPen(QColor(GOLD_DIM), 1))
    painter.drawPath(_notched_rect(50, 50, width - 100, height - 100, 28))

    mark = 208
    QSvgRenderer(str(MASTER)).render(painter, QRectF((width - mark) / 2, 108, mark, mark))

    center = width / 2
    _draw_wordmark(painter, family, 106, center - _wordmark_width(family, 106) / 2, 420)
    _draw_centered(painter, TAGLINE, _font(family, 32, tracking=102), INK_DIM, center, 478)
    _draw_centered(painter, PLATFORMS, _font(family, 23, tracking=126), GOLD_DIM, center, 536)

    painter.end()
    return image


# -- outputs ------------------------------------------------------------------------


def _display(path: Path) -> str:
    """Repo-relative when it can be — ``--iconset`` may name anywhere."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def write_iconset(directory: Path) -> list[Written]:
    """Stage an Apple ``.iconset`` for ``iconutil``."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for size in ICNS_SIZES:
        for suffix, pixels in ((f"{size}x{size}", size), (f"{size}x{size}@2x", size * 2)):
            path = directory / f"icon_{suffix}.png"
            path.write_bytes(png_bytes(render(pixels)))
            written.append(Written(path, f"{pixels}px"))
    return written


def write_all() -> list[Written]:
    family = load_font_family()
    written: list[Written] = []

    UI_DIR.mkdir(parents=True, exist_ok=True)
    base = UI_DIR / "icon.png"
    base.write_bytes(png_bytes(render(BASE_SIZE)))
    written.append(Written(base, f"{BASE_SIZE}px — Qt window icon, Flatpak hicolor"))

    for size in ICON_SIZES:
        path = UI_DIR / f"icon-{size}.png"
        path.write_bytes(png_bytes(render(size)))
        written.append(Written(path, f"{size}px"))

    ico = UI_DIR / "icon.ico"
    ico.write_bytes(build_ico([(size, png_bytes(render(size))) for size in ICO_SIZES]))
    written.append(Written(ico, ", ".join(f"{s}" for s in ICO_SIZES)))

    DOCS_IMAGES.mkdir(parents=True, exist_ok=True)
    mark = DOCS_IMAGES / "nparseplus-mark.png"
    mark.write_bytes(png_bytes(render(BASE_SIZE)))
    written.append(Written(mark, "mkdocs theme logo"))

    favicon = DOCS_IMAGES / "favicon.png"
    favicon.write_bytes(png_bytes(render(64)))
    written.append(Written(favicon, "mkdocs favicon (overrides the theme's own)"))

    logo = DOCS_IMAGES / "nparseplus-logo.png"
    logo.write_bytes(png_bytes(draw_lockup(family)))
    written.append(Written(logo, "README header banner"))

    social = DOCS_IMAGES / "social-preview.png"
    social.write_bytes(png_bytes(draw_social(family)))
    written.append(Written(social, "1280x640 — upload by hand, see docs/dev-notes"))

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--iconset",
        type=Path,
        help="stage an Apple .iconset in this directory instead of writing the "
        "committed artifacts (used by packaging/make_icns.sh)",
    )
    args = parser.parse_args()

    if not MASTER.is_file():
        sys.exit(f"missing the SVG master: {MASTER}")

    # QGuiApplication, not QApplication: this needs the font database and a
    # paint device, not widgets. Held in a local so it outlives every render.
    app = QGuiApplication.instance() or QGuiApplication([])
    assert app is not None

    written = write_iconset(args.iconset) if args.iconset else write_all()
    for item in written:
        print(f"wrote {_display(item.path)}  ({item.note})")
    print(f"{len(written)} files from {MASTER.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
