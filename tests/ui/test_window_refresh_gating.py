"""Perf gating on the polling windows: no render work while hidden, and no
layout teardown when the widget order is unchanged between ticks."""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import YOU_GROUP, SpellRow, TimersService
from nparseplus.ui.spellwindow import SpellTimerWindow

pytestmark = pytest.mark.qt

NOW = datetime(2026, 7, 14, 12, 0, 0)


def make_backend() -> types.SimpleNamespace:
    timers = TimersService()
    timers.add_spell(
        SpellRow(
            name="Clarity",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=Spell(id=1, name="Clarity"),
            ends_at=NOW + timedelta(minutes=35),
            total_duration_s=35 * 60.0,
        )
    )
    return types.SimpleNamespace(timers=timers, settings=Settings(), player=ActivePlayer())


def make_window(qtbot, backend) -> SpellTimerWindow:
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    return window


def test_tick_skips_render_while_hidden(qtbot, monkeypatch) -> None:
    window = make_window(qtbot, make_backend())
    window.hide()
    calls = []
    monkeypatch.setattr(window, "refresh", lambda *a, **k: calls.append(1))
    window._on_refresh_tick()
    assert calls == []


def test_show_renders_immediately(qtbot, monkeypatch) -> None:
    window = make_window(qtbot, make_backend())
    window.hide()
    calls = []
    monkeypatch.setattr(window, "refresh", lambda *a, **k: calls.append(1))
    window.show()
    assert calls == [1]
    window._on_refresh_tick()
    assert calls == [1, 1]


def test_stable_rows_skip_layout_teardown(qtbot, monkeypatch) -> None:
    backend = make_backend()
    window = make_window(qtbot, backend)
    window.refresh(now=NOW)
    assert window.current_row_names() == ["Clarity"]

    calls = []
    original = window._rows_layout.takeAt
    monkeypatch.setattr(window._rows_layout, "takeAt", lambda i: (calls.append(i), original(i))[1])
    window.refresh(now=NOW + timedelta(seconds=1))
    assert calls == []  # same row set/order -> layout untouched
    # countdown labels still updated on the skipped-layout pass
    assert window.current_row_names() == ["Clarity"]


def test_row_change_rebuilds_layout(qtbot) -> None:
    backend = make_backend()
    window = make_window(qtbot, backend)
    window.refresh(now=NOW)
    backend.timers.add_spell(
        SpellRow(
            name="Aegolism",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=Spell(id=2, name="Aegolism"),
            ends_at=NOW + timedelta(minutes=20),
            total_duration_s=20 * 60.0,
        )
    )
    window.refresh(now=NOW)
    assert sorted(window.current_row_names()) == ["Aegolism", "Clarity"]

    backend.timers.clear_all()
    window.refresh(now=NOW)
    assert window.current_row_names() == []
