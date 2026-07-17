"""SpellTimerWindow rendering tests (offscreen Qt via pytest-qt)."""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings
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


def test_you_only_filter_hides_other_groups(qtbot):
    backend = make_backend()
    backend.settings.spellwindow.you_only_spells = True
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh()

    assert window.current_groups() == [YOU_GROUP]
    assert window.current_row_names() == ["Clarity"]


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
