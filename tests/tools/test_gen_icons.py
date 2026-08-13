"""``tools/gen_icons.py`` and the committed artifacts it produces.

The generator has no ``--check`` mode on purpose (Qt's rasterizer is not
byte-stable across PySide6 versions, so a byte comparison would fail on a
version bump rather than on a real edit). These assert the artifacts' *shape*
instead — which is what actually breaks a shipped icon: a missing size, a
flattened alpha channel, an ICO whose directory disagrees with its payloads,
or a mark that has degenerated into a single flat colour at 16px.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import gen_icons  # noqa: E402

pytestmark = pytest.mark.qt

UI_DIR = REPO_ROOT / "data" / "ui"
DOCS_IMAGES = REPO_ROOT / "docs" / "assets" / "images"


def png_size(path: Path) -> tuple[int, int]:
    """Width/height straight out of the IHDR, no image library needed."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


def test_the_master_is_the_only_source_art():
    assert gen_icons.MASTER.is_file()
    # icon.xcf was 64x64 raster art inherited from the nParse fork; the whole
    # point of the SVG is that it is diffable and resamples losslessly.
    assert not (REPO_ROOT / "data" / "assets" / "icon.xcf").exists()


def test_the_master_starts_with_the_svg_tag():
    """The Flatpak build fails if this file does not open with ``<svg``.

    It is installed into ``hicolor/scalable/``, and ``appstreamcli compose``
    (run by flatpak-builder over the export) reads it through gdk-pixbuf,
    whose SVG loader sniffs the format from the first bytes and knows exactly
    two signatures: ``<svg`` and ``<!DOCTYPE svg``. Anything else — a leading
    comment, and an ``<?xml?>`` declaration too — is "Unrecognized image file
    format", which is a fatal compose error, not a warning. Qt renders the
    file either way, so nothing else here would notice: v2.9.0 was tagged and
    never shipped because of exactly this.

    The ``lstrip`` is deliberate and matches the loader rather than being
    laxer than it: librsvg registers its prefix as ``" <svg"``, whose leading
    space is gdk-pixbuf's "skip whitespace" marker. Leading blank lines were
    measured against appstreamcli 1.0.2 and compose still succeeds, so
    demanding ``<svg`` at byte zero would fail a file that builds fine. What
    may not precede the tag is *content* — a comment or a declaration.
    """
    head = gen_icons.MASTER.read_text().lstrip()
    assert head.startswith(("<svg", "<!DOCTYPE svg")), (
        "icon.svg must open with the <svg> tag — see the comment inside it"
    )


def test_committed_pngs_are_the_sizes_they_claim():
    assert png_size(UI_DIR / "icon.png") == (gen_icons.BASE_SIZE, gen_icons.BASE_SIZE)
    for size in gen_icons.ICON_SIZES:
        assert png_size(UI_DIR / f"icon-{size}.png") == (size, size)


def test_committed_ico_directory_matches_its_payloads():
    data = (UI_DIR / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    assert count == len(gen_icons.ICO_SIZES)

    for index, expected in enumerate(gen_icons.ICO_SIZES):
        offset = 6 + 16 * index
        width, _height, _colors, _reserved, _planes, _bpp, length, start = struct.unpack(
            "<BBBBHHII", data[offset : offset + 16]
        )
        # 256 is recorded as 0 — the format has one byte for the dimension.
        assert width == (0 if expected >= 256 else expected)
        payload = data[start : start + length]
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", payload[16:24]) == (expected, expected)


def test_docs_and_social_art_is_present_and_sized():
    assert png_size(DOCS_IMAGES / "favicon.png") == (64, 64)
    assert png_size(DOCS_IMAGES / "nparseplus-mark.png") == (
        gen_icons.BASE_SIZE,
        gen_icons.BASE_SIZE,
    )
    # GitHub's social preview canvas is exactly this; it is uploaded by hand
    # through the web UI, so nothing else checks it.
    assert png_size(DOCS_IMAGES / "social-preview.png") == gen_icons.SOCIAL_SIZE
    _, height = png_size(DOCS_IMAGES / "nparseplus-logo.png")
    assert height == gen_icons.LOCKUP_HEIGHT


def test_sixteen_pixel_render_still_reads(qapp):
    """The one requirement the mark cannot fail: a silhouette at 16px.

    Not a pixel comparison — a golden 16x16 would be exactly the brittle
    check the missing ``--check`` mode avoids. This asserts the qualities a
    mush render loses: transparent corners (the plate is notched, not a full
    square), a dark interior, and gold actually present.
    """
    image = gen_icons.render(16)
    assert (image.width(), image.height()) == (16, 16)

    # The notch cuts every corner, so all four stay transparent.
    for x, y in ((0, 0), (15, 0), (0, 15), (15, 15)):
        assert image.pixelColor(x, y).alpha() == 0, f"corner {x},{y} is not notched"

    colors = [
        image.pixelColor(x, y)
        for x in range(16)
        for y in range(16)
        if image.pixelColor(x, y).alpha() > 200
    ]
    # Thresholds are ~half the measured values (244 opaque, 62 warm, 122 dark,
    # peak lightness 175), so ordinary rasterizer drift cannot trip them but a
    # glyph that has thinned away or a plate that has gone flat will.
    assert len(colors) > 120, "the plate did not fill"

    # Gold: warm, red clearly above blue. The ring and the glyph are the only
    # things in the icon that qualify — everything else is neutral-dark.
    warm = [c for c in colors if c.red() > c.blue() + 40]
    assert len(warm) >= 30, "no engraving survived the 16px raster"
    assert max(c.lightness() for c in warm) >= 140, "the gold antialiased to mud"

    # And a dark ground behind it, or the glyph has nothing to read against.
    dark = [c for c in colors if c.lightness() < 40]
    assert len(dark) >= 60, "the glass field vanished"


def test_ico_assembly_is_ordered_and_offsets_line_up():
    """:func:`gen_icons.build_ico` on synthetic payloads — the struct packing
    is the part of this file with real off-by-one risk."""
    entries = [(16, b"a" * 10), (256, b"b" * 20)]
    blob = gen_icons.build_ico(entries)

    assert struct.unpack("<HHH", blob[:6]) == (0, 1, 2)
    first = struct.unpack("<BBBBHHII", blob[6:22])
    second = struct.unpack("<BBBBHHII", blob[22:38])
    assert first[0] == 16 and second[0] == 0  # 256 records as 0
    assert first[6] == 10 and second[6] == 20  # byte counts
    assert first[7] == 38 and second[7] == 48  # 6 + 2*16, then +10
    assert blob[38:48] == b"a" * 10
    assert blob[48:68] == b"b" * 20
