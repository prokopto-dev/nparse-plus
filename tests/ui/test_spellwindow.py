"""SpellTimerWindow rendering tests (offscreen Qt via pytest-qt)."""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.handlers.boat import BOATS_GROUP
from nparseplus.core.handlers.spawn_timer import CUSTOM_TIMER_GROUP
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import (
    YOU_GROUP,
    CounterRow,
    RollRow,
    SpellRow,
    TimerRow,
    TimersService,
)
from nparseplus.ui.spellwindow import SpellTimerWindow, bar_color, format_remaining

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
    timers.add_timer(
        TimerRow(
            name="Custom Timer",
            group="Timers",
            updated_at=NOW,
            ends_at=NOW + timedelta(seconds=30),
            total_duration_s=30.0,
        )
    )
    from nparseplus.core.player import ActivePlayer

    return types.SimpleNamespace(timers=timers, settings=Settings(), player=ActivePlayer())


def test_rows_render_and_you_group_first(qtbot):
    backend = make_backend()
    # A group sorting alphabetically before "Timers" — YOU_GROUP must still win.
    backend.timers.add_spell(
        SpellRow(
            name="Tainted Breath",
            group=" a rat ",
            updated_at=NOW,
            is_target_player=False,
            spell=Spell(id=2, name="Tainted Breath"),
            ends_at=NOW + timedelta(seconds=42),
            total_duration_s=42.0,
            detrimental=True,
        )
    )
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()

    groups = window.current_groups()
    assert groups[0] == YOU_GROUP
    assert groups == [YOU_GROUP, " a rat ", "Timers"]
    names = window.current_row_names()
    assert names == ["Clarity", "Tainted Breath", "Custom Timer"]


def _add_category_rows(backend) -> None:
    """One row per built-in category (plus another player's buff)."""
    backend.timers.add_spell(
        SpellRow(
            name="Aegolism",
            group="Joe",
            updated_at=NOW,
            is_target_player=True,
            spell=Spell(id=3, name="Aegolism"),
            ends_at=NOW + timedelta(minutes=90),
            total_duration_s=90 * 60.0,
        )
    )
    backend.timers.add_timer(
        TimerRow(
            name="Butcherblock to Freeport",
            group=BOATS_GROUP,
            updated_at=NOW,
            ends_at=NOW + timedelta(minutes=5),
            total_duration_s=300.0,
        )
    )
    backend.timers.add_timer(
        TimerRow(
            name="a decaying skeleton",
            group=CUSTOM_TIMER_GROUP,
            updated_at=NOW,
            ends_at=NOW + timedelta(minutes=6),
            total_duration_s=360.0,
        )
    )
    backend.timers.add_roll(
        RollRow(
            name="Joe",
            group=" Random -- 333",
            updated_at=NOW,
            roll=42,
            max_roll=333,
            ends_at=NOW + timedelta(seconds=30),
            total_duration_s=30.0,
        )
    )


def test_you_only_filter_hides_only_other_player_spell_rows(qtbot):
    # Regression: the toggle used to hide EVERY non-YOU row (boats, custom
    # respawn timers, trigger timers, rolls) instead of just other players'
    # spell rows.
    backend = make_backend()
    _add_category_rows(backend)
    backend.settings.spellwindow.you_only_spells = True
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()

    names = window.current_row_names()
    assert "Aegolism" not in names  # the other player's buff hides
    assert "Clarity" in names  # yours stays
    assert "Custom Timer" in names  # trigger timer stays
    assert "Butcherblock to Freeport" in names  # boats stay
    assert "a decaying skeleton" in names  # respawn timers stay
    assert "Joe" in names  # the roll stays


@pytest.mark.parametrize(
    ("setting", "gone", "kept"),
    [
        ("show_boats", "Butcherblock to Freeport", "a decaying skeleton"),
        ("show_custom_timers", "a decaying skeleton", "Butcherblock to Freeport"),
        ("show_trigger_timers", "Custom Timer", "Butcherblock to Freeport"),
        ("show_random_rolls", "Joe", "Custom Timer"),
    ],
)
def test_category_toggle_hides_only_its_section(qtbot, setting, gone, kept):
    backend = make_backend()
    _add_category_rows(backend)
    setattr(backend.settings.spellwindow, setting, False)
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()

    names = window.current_row_names()
    assert gone not in names
    assert kept in names
    assert "Clarity" in names and "Aegolism" in names  # spells untouched


def test_all_categories_visible_by_default(qtbot):
    backend = make_backend()
    _add_category_rows(backend)
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    names = window.current_row_names()
    for name in (
        "Clarity",
        "Aegolism",
        "Custom Timer",
        "Butcherblock to Freeport",
        "a decaying skeleton",
        "Joe",
    ):
        assert name in names


def _add_player_spell(backend, name: str, class_levels: dict) -> None:
    backend.timers.add_spell(
        SpellRow(
            name=name,
            group="Joe",
            updated_at=NOW,
            is_target_player=True,
            spell=Spell(id=hash(name) % 9999, name=name, class_levels=class_levels),
            ends_at=NOW + timedelta(minutes=5),
            total_duration_s=300.0,
        )
    )


def _with_profile(backend, show_classes):
    from nparseplus.config.settings import PlayerInfo
    from nparseplus.core.enums import Server

    backend.player.reset_for("Xantik", Server.GREEN)
    backend.settings.players.append(
        PlayerInfo(name="Xantik", server="green", show_spells_for_classes=show_classes)
    )


def test_class_filter_hides_unselected_classes(qtbot):
    from nparseplus.core.enums import PlayerClass

    backend = make_backend()
    _with_profile(backend, [int(PlayerClass.CLERIC)])
    _add_player_spell(backend, "Skin like Wood", {PlayerClass.DRUID: 14})
    _add_player_spell(backend, "Courage", {PlayerClass.CLERIC: 1, PlayerClass.PALADIN: 9})
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    names = window.current_row_names()
    assert "Courage" in names  # a selected class can cast it
    assert "Skin like Wood" not in names  # druid-only, filtered
    assert "Clarity" in names  # YOU group always visible


def test_class_filter_none_shows_all_and_npc_rows_survive(qtbot):
    from nparseplus.core.enums import PlayerClass

    backend = make_backend()
    _with_profile(backend, None)  # None = show all (EQTool null default)
    _add_player_spell(backend, "Skin like Wood", {PlayerClass.DRUID: 14})
    backend.timers.add_spell(
        SpellRow(
            name="Tainted Breath",
            group=" a rat ",
            updated_at=NOW,
            is_target_player=False,  # NPC target: never filtered
            spell=Spell(id=2, name="Tainted Breath", class_levels={PlayerClass.SHAMAN: 4}),
            ends_at=NOW + timedelta(seconds=42),
            total_duration_s=42.0,
        )
    )
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    names = window.current_row_names()
    assert "Skin like Wood" in names
    assert "Tainted Breath" in names

    # Even with a restrictive filter, the NPC-target row survives.
    backend.settings.players[0].show_spells_for_classes = [int(PlayerClass.CLERIC)]
    window.refresh()
    names = window.current_row_names()
    assert "Tainted Breath" in names
    assert "Skin like Wood" not in names


def test_refresh_drops_removed_rows(qtbot):
    backend = make_backend()
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    assert "Custom Timer" in window.current_row_names()

    row = backend.timers.find("Custom Timer", "Timers")
    backend.timers.remove_row(row)
    window.refresh()

    assert window.current_row_names() == ["Clarity"]
    assert window.current_groups() == [YOU_GROUP]


def test_counter_and_roll_rows_render(qtbot):
    backend = make_backend()
    backend.timers.add_counter(
        CounterRow(name="Tashan", group=" a rat ", updated_at=NOW, is_target_player=False)
    )
    backend.timers.add_roll(
        RollRow(
            name="Rollo",
            group="0 to 100",
            updated_at=NOW,
            roll=87,
            max_roll=100,
            ends_at=NOW + timedelta(seconds=15),
            total_duration_s=15.0,
        )
    )
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()

    names = window.current_row_names()
    assert "Tashan" in names and "Rollo" in names


def test_refresh_does_not_mutate_rows(qtbot):
    backend = make_backend()
    before = [row.model_copy(deep=True) for row in backend.timers.snapshot()]
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    assert backend.timers.snapshot() == before


def test_persist_state_writes_window_settings(qtbot):
    backend = make_backend()
    saves: list[bool] = []
    window = SpellTimerWindow(backend, on_save=lambda: saves.append(True))
    qtbot.addWidget(window)
    window.setGeometry(10, 20, 200, 300)
    window.persist_state(shown=True)

    state = backend.settings.windows["spells"]
    assert state.geometry == (10, 20, 200, 300)
    assert state.shown is True
    assert saves == [True]


def test_bar_colors_and_time_format():
    spell = Spell(id=1, name="Clarity")
    beneficial = SpellRow(
        name="Clarity",
        group=YOU_GROUP,
        updated_at=NOW,
        spell=spell,
        ends_at=NOW,
        total_duration_s=1.0,
    )
    detrimental = beneficial.model_copy(update={"detrimental": True})
    cooldown = beneficial.model_copy(update={"is_cooldown": True})
    timer = TimerRow(name="t", group="Timers", updated_at=NOW, ends_at=NOW, total_duration_s=1.0)
    roll = RollRow(
        name="r",
        group="g",
        updated_at=NOW,
        roll=1,
        max_roll=2,
        ends_at=NOW,
        total_duration_s=1.0,
    )
    colors = {bar_color(row) for row in (beneficial, detrimental, cooldown, timer, roll)}
    assert len(colors) == 5  # each kind is visually distinct

    assert format_remaining(-5) == "00:00"
    assert format_remaining(65) == "01:05"
    assert format_remaining(3723) == "1:02:03"


# -- window sizing (user-controlled, scroll on overflow) -----------------------


def test_window_keeps_user_size_and_scrolls_on_overflow(qtbot):
    # Regression: the window grew as rows arrived (layout minimum enforced on
    # the window) and stayed huge after they left. Rows now live in a scroll
    # area, so the user's size wins and overflow scrolls.
    from PySide6.QtWidgets import QApplication

    backend = make_backend()
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.show()
    window.resize(220, 300)
    QApplication.processEvents()

    for i in range(40):
        backend.timers.add_timer(
            TimerRow(
                name=f"filler {i}",
                group="Timers",
                updated_at=NOW,
                ends_at=NOW + timedelta(minutes=5),
                total_duration_s=300.0,
            ),
            allow_duplicates=True,
        )
    window.refresh()
    QApplication.processEvents()
    assert window.height() == 300  # did not inflate
    assert window._scroll.verticalScrollBar().maximum() > 0  # overflow scrolls

    backend.timers.clear_all()
    window.refresh()
    QApplication.processEvents()
    assert window.height() == 300  # and did not stay huge either way
    assert window._scroll.verticalScrollBar().maximum() == 0


def test_user_resize_persists_geometry(qtbot):
    backend = make_backend()
    saves: list[bool] = []
    window = SpellTimerWindow(backend, on_save=lambda: saves.append(True))
    qtbot.addWidget(window)
    window.show()

    window.resize(250, 333)  # what the size grip does
    qtbot.waitUntil(lambda: bool(saves), timeout=3000)  # debounced persist
    assert backend.settings.windows["spells"].geometry[2:] == (250, 333)


# -- context menu (manual timer clearing) --------------------------------------


def _shown_window(qtbot, backend) -> SpellTimerWindow:
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()
    window.show()  # childAt/mapTo need real layout geometry
    window._rows_layout.activate()
    return window


def test_context_target_resolves_row_header_and_empty(qtbot):
    backend = make_backend()
    window = _shown_window(qtbot, backend)

    clarity_widget = next(w for w in window._row_widgets.values() if w.row_name == "Clarity")
    pos = clarity_widget.mapTo(window, clarity_widget.rect().center())
    row, group = window._context_target(pos)
    assert row is not None and row.name == "Clarity"
    assert group == YOU_GROUP

    header = window._headers["Timers"]
    pos = header.mapTo(window, header.rect().center())
    row, group = window._context_target(pos)
    assert row is None
    assert group == "Timers"

    row, group = window._context_target(window.rect().bottomRight())
    assert row is None and group is None


def test_clear_row_group_and_all(qtbot):
    backend = make_backend()
    _add_category_rows(backend)
    window = _shown_window(qtbot, backend)

    row = backend.timers.find("Custom Timer", "Timers")
    window._clear_row(row)
    assert "Custom Timer" not in window.current_row_names()
    assert backend.timers.find("Custom Timer", "Timers") is None

    window._clear_group(BOATS_GROUP)
    assert "Butcherblock to Freeport" not in window.current_row_names()

    window._clear_all()
    assert backend.timers.snapshot() == []
    assert window.current_row_names() == []
    assert window.current_groups() == []


def test_buff_fade_warning_turns_time_label_red(qtbot):
    backend = make_backend()
    backend.settings.spellwindow.buff_fade_warning_seconds = 30
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)

    window.refresh(now=NOW)
    clarity = next(w for w in window._row_widgets.values() if w.row_name == "Clarity")
    assert clarity._value.styleSheet() == ""

    window.refresh(now=NOW + timedelta(minutes=35) - timedelta(seconds=10))
    assert "bold" in clarity._value.styleSheet()

    # Recast clears the warning again.
    window.refresh(now=NOW)
    assert clarity._value.styleSheet() == ""
