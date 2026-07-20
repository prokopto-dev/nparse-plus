"""Smoke test for ``tools/capture_screenshots.py`` (offscreen Qt).

Runs one representative phase-A capture into a tmp dir and asserts the PNG is
present, correctly sized, and not a blank/uniform grab. The full 22-shot run is
a dev tool, not shipped code, so CI exercises a single window to stay fast; the
full-app phase B (maps/tray) is deliberately not exercised here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import capture_screenshots as cap  # noqa: E402

pytestmark = pytest.mark.qt


def test_spell_timers_capture(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage

    monkeypatch.setattr(cap, "OUT_DIR", tmp_path)
    backend, _settings = cap._build_backend()
    cap.cap_spell_timers(backend)

    out = tmp_path / "window--spell-timers.png"
    assert out.exists()
    image = QImage(str(out))
    assert not image.isNull()
    assert (image.width(), image.height()) == (250, 430)

    # Non-uniform: a blank offscreen grab (or a fully transparent overlay) would
    # be a single flat color. A populated window has many (icons, bars, text).
    sampled = {
        image.pixel(x, y) for x in range(0, image.width(), 20) for y in range(0, image.height(), 20)
    }
    assert len(sampled) > 5
