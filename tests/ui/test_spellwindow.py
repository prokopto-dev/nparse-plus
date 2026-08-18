"""SpellTimerWindow rendering tests (offscreen Qt via pytest-qt)."""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.handlers.boat import BOATS_GROUP
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    ROLL_TIMER_GROUP,
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    CounterRow,
    RollRow,
    SpellRow,
    TimerRow,
    TimersService,
)
from nparseplus.ui.overlaybase import format_mmss
from nparseplus.ui.spellwindow import (
    COLOR_BENEFICIAL,
    COLOR_DETRIMENTAL,
    COLOR_POP_WINDOW,
    COLOR_ROLL,
    COLOR_TIMER,
    FADE_STEPS,
    SpellTimerWindow,
    bar_color,
    fade_color,
    row_bar_color,
    row_sort_key,
)

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
            group=TRIGGER_TIMER_GROUP,
            updated_at=NOW,
            ends_at=NOW + timedelta(seconds=30),
            total_duration_s=30.0,
        )
    )
    from nparseplus.core.player import ActivePlayer

    return types.SimpleNamespace(timers=timers, settings=Settings(), player=ActivePlayer())


def test_rows_render_and_you_group_first(qtbot):
    backend = make_backend()
    # " a rat " sorts before the YOU group alphabetically — YOU must still win.
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
    # Custom Timers (two leading spaces) sorts before " a rat " (one).
    assert groups == [YOU_GROUP, TRIGGER_TIMER_GROUP, " a rat "]
    names = window.current_row_names()
    assert names == ["Clarity", "Custom Timer", "Tainted Breath"]


def _add_other_buff(backend, name: str, target: str, minutes: float = 20) -> None:
    backend.timers.add_spell(
        SpellRow(
            name=name,
            group=target,
            updated_at=NOW,
            spell=Spell(id=hash((name, target)) % 9999, name=name),
            ends_at=NOW + timedelta(minutes=minutes),
            total_duration_s=minutes * 60.0,
        )
    )


def test_raid_mode_renders_spell_headers_with_targets(qtbot):
    backend = make_backend()  # Clarity (YOU) + a Custom Timer
    for target in ("Joe", "Bob", "Ann"):
        _add_other_buff(backend, "Aegolism", target)
    backend.settings.spellwindow.raid_group_by_spell = True
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    headers = window.current_header_texts()
    # The spell name heads a section; the targets are the rows under it.
    assert "Aegolism" in headers
    assert {"Ann", "Bob", "Joe"} <= set(window.current_row_labels())
    # YOU stays first and target-headed; its own buff still shows the spell name.
    assert headers[0] == YOU_GROUP.strip() or headers[0] == YOU_GROUP
    assert "Clarity" in window.current_row_labels()


def test_raid_mode_off_keeps_target_headers(qtbot):
    backend = make_backend()
    for target in ("Joe", "Bob", "Ann"):
        _add_other_buff(backend, "Aegolism", target)
    assert backend.settings.spellwindow.raid_group_by_spell is False  # default
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    # Targets remain the headers; each row shows the spell.
    assert {"Ann", "Bob", "Joe"} <= set(window.current_groups())
    assert window.current_row_labels().count("Aegolism") == 3


def test_post_expiry_row_flashes_and_click_dismisses(qtbot):
    backend = make_backend()
    backend.timers.add_spell(
        SpellRow(
            name="Aegolism",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=Spell(id=7, name="Aegolism"),
            ends_at=NOW + timedelta(seconds=10),
            total_duration_s=10.0,
            post_expiry_persist_s=30.0,
        )
    )
    # Cross ends_at: the row lingers with expired_at stamped (flashing state).
    backend.timers.tick(NOW + timedelta(seconds=11))
    window = _shown_window(qtbot, backend)

    widget = next(w for w in window._row_widgets.values() if w.row_name == "Aegolism")
    assert widget.expired is True
    assert widget._value.text() == "REBUFF"
    # The flash toggles emphasis styling on/off.
    window._flash_on = False
    window._toggle_flash()  # -> on
    assert widget._name.styleSheet() != ""
    window._toggle_flash()  # -> off
    assert widget._name.styleSheet() == ""

    # Left-click over the flashing row dismisses it.
    pos = widget.mapTo(window, widget.rect().center())
    assert window._dismiss_expired_at(pos) is True
    assert backend.timers.find("Aegolism", YOU_GROUP) is None


def test_flash_style_cleared_when_row_recast(qtbot):
    """Regression: an expired flashing row that is recast must not keep the
    red/bold flash on its live countdown."""
    from nparseplus.ui.spellwindow import _RowWidget

    widget = _RowWidget()
    qtbot.addWidget(widget)
    expired = SpellRow(
        name="Aegolism",
        group="Joe",
        updated_at=NOW,
        is_target_player=True,
        spell=Spell(id=7, name="Aegolism"),
        ends_at=NOW,
        total_duration_s=100.0,
        post_expiry_persist_s=30.0,
        expired_at=NOW,
    )
    widget.update_row(expired, NOW)
    widget.apply_flash(True)
    assert widget.expired is True
    assert widget._value.styleSheet() != ""  # flashing
    # Recast: a fresh live row reuses the same widget.
    live = SpellRow(
        name="Aegolism",
        group="Joe",
        updated_at=NOW,
        is_target_player=True,
        spell=Spell(id=7, name="Aegolism"),
        ends_at=NOW + timedelta(minutes=5),
        total_duration_s=300.0,
    )
    widget.update_row(live, NOW)
    assert widget.expired is False
    assert widget._value.styleSheet() == ""  # no stale flash styling


def test_live_row_is_not_dismissed_by_click(qtbot):
    backend = make_backend()  # Clarity (YOU) is live, not expired
    window = _shown_window(qtbot, backend)
    widget = next(w for w in window._row_widgets.values() if w.row_name == "Clarity")
    assert widget.expired is False
    pos = widget.mapTo(window, widget.rect().center())
    assert window._dismiss_expired_at(pos) is False
    assert backend.timers.find("Clarity", YOU_GROUP) is not None


def _add_you_spell(backend, name: str, minutes: float) -> None:
    backend.timers.add_spell(
        SpellRow(
            name=name,
            group=YOU_GROUP,
            updated_at=NOW,
            spell=Spell(id=hash(name) % 9999, name=name),
            ends_at=NOW + timedelta(minutes=minutes),
            total_duration_s=minutes * 60.0,
        )
    )


def test_default_sort_is_time_remaining_soonest_first(qtbot):
    backend = make_backend()  # seeds Clarity in YOU at +35m
    _add_you_spell(backend, "Zephyr", 5)  # sooner
    _add_you_spell(backend, "Alacrity", 60)  # later
    assert backend.settings.spellwindow.row_sort == "time_remaining"  # default
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    # Soonest-to-expire first, regardless of name order (YOU group).
    you = [n for n in window.current_row_names() if n in {"Zephyr", "Clarity", "Alacrity"}]
    assert you == ["Zephyr", "Clarity", "Alacrity"]


def test_alphabetical_sort_ignores_time_remaining(qtbot):
    backend = make_backend()
    _add_you_spell(backend, "Zephyr", 5)
    _add_you_spell(backend, "Alacrity", 60)
    backend.settings.spellwindow.row_sort = "alphabetical"
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    you = [n for n in window.current_row_names() if n in {"Zephyr", "Clarity", "Alacrity"}]
    assert you == ["Alacrity", "Clarity", "Zephyr"]


def test_time_remaining_sorts_counter_last(qtbot):
    backend = make_backend()
    # A counter (no ends_at) shares a group with two timed rows.
    backend.timers.add_counter(
        CounterRow(name="Tashan", group="Joe", updated_at=NOW, is_target_player=False)
    )
    backend.timers.add_spell(
        SpellRow(
            name="Slow",
            group="Joe",
            updated_at=NOW,
            is_target_player=False,
            spell=Spell(id=11, name="Slow"),
            ends_at=NOW + timedelta(seconds=90),
            total_duration_s=90.0,
            detrimental=True,
        )
    )
    backend.timers.add_spell(
        SpellRow(
            name="Malaise",
            group="Joe",
            updated_at=NOW,
            is_target_player=False,
            spell=Spell(id=12, name="Malaise"),
            ends_at=NOW + timedelta(seconds=30),
            total_duration_s=30.0,
            detrimental=True,
        )
    )
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    # Timed rows soonest-first; the counter (never expires) sorts last.
    joe_rows = [n for n in window.current_row_names() if n in {"Malaise", "Slow", "Tashan"}]
    assert joe_rows == ["Malaise", "Slow", "Tashan"]


def test_row_sort_key_helper():
    slow = SpellRow(
        name="Slow",
        group="Joe",
        updated_at=NOW,
        spell=Spell(id=11, name="Slow"),
        ends_at=NOW + timedelta(seconds=90),
        total_duration_s=90.0,
    )
    counter = CounterRow(name="Tashan", group="Joe", updated_at=NOW)
    roll = RollRow(
        name="Joe",
        group=" Random -- 333",
        updated_at=NOW,
        roll=42,
        max_roll=333,
        ends_at=NOW + timedelta(seconds=30),
        total_duration_s=30.0,
    )
    # alphabetical: (0, name) so all keys are comparable (number, str) tuples.
    assert row_sort_key(slow, NOW, "alphabetical") == (0, "slow")
    # time_remaining: seconds-left first, name tiebreak.
    assert row_sort_key(slow, NOW, "time_remaining") == (90.0, "slow")
    # counters have no ends_at -> sort last (infinite), name-tiebroken.
    assert row_sort_key(counter, NOW, "time_remaining") == (float("inf"), "tashan")
    # rolls sort by roll value descending regardless of mode, name-tiebroken.
    assert row_sort_key(roll, NOW, "time_remaining") == (-42, "joe")
    assert row_sort_key(roll, NOW, "alphabetical") == (-42, "joe")


ROLL_GROUP = " Random -- 333"


def _add_roll(backend, roller: str, value: int) -> None:
    backend.timers.add_roll(
        RollRow(
            name=roller,
            group=ROLL_GROUP,
            updated_at=NOW,
            roll=value,
            max_roll=333,
            ends_at=NOW + timedelta(seconds=30),
            total_duration_s=30.0,
        )
    )


def _rendered_rolls(window, rollers: set[str]) -> list[str]:
    return [n for n in window.current_row_names() if n in rollers]


def test_rolls_render_highest_first(qtbot):
    backend = make_backend()
    _add_roll(backend, "Joe", 42)
    _add_roll(backend, "Amy", 88)
    _add_roll(backend, "Zed", 15)
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    # Highest roll first regardless of insertion order.
    assert _rendered_rolls(window, {"Joe", "Amy", "Zed"}) == ["Amy", "Joe", "Zed"]


def test_rolls_render_highest_first_even_in_alphabetical_mode(qtbot):
    backend = make_backend()
    backend.settings.spellwindow.row_sort = "alphabetical"
    _add_roll(backend, "Joe", 42)
    _add_roll(backend, "Amy", 88)
    _add_roll(backend, "Zed", 15)
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    # Alphabetical mode does not apply to rolls — still roll-descending.
    assert _rendered_rolls(window, {"Joe", "Amy", "Zed"}) == ["Amy", "Joe", "Zed"]


def test_equal_rolls_tiebreak_on_name_casefold(qtbot):
    backend = make_backend()
    _add_roll(backend, "zed", 50)
    _add_roll(backend, "amy", 50)
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    # Equal roll values -> name casefold ascending.
    assert _rendered_rolls(window, {"zed", "amy"}) == ["amy", "zed"]


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
            group=MOB_TIMER_GROUP,
            updated_at=NOW,
            ends_at=NOW + timedelta(minutes=6),
            total_duration_s=360.0,
        )
    )
    backend.timers.add_timer(
        TimerRow(
            name="Ring 8 Roll Timer",
            group=ROLL_TIMER_GROUP,
            updated_at=NOW,
            ends_at=NOW + timedelta(minutes=30),
            total_duration_s=1800.0,
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
    assert "Custom Timer" in names  # custom (trigger) timer stays
    assert "Butcherblock to Freeport" in names  # boats stay
    assert "a decaying skeleton" in names  # mob respawn timers stay
    assert "Ring 8 Roll Timer" in names  # roll timers stay
    assert "Joe" in names  # the roll stays


@pytest.mark.parametrize(
    ("setting", "gone", "kept"),
    [
        ("show_boats", "Butcherblock to Freeport", "a decaying skeleton"),
        ("show_mob_timers", "a decaying skeleton", "Butcherblock to Freeport"),
        ("show_roll_timers", "Ring 8 Roll Timer", "Butcherblock to Freeport"),
        ("show_custom_timers", "Custom Timer", "Butcherblock to Freeport"),
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
        "Ring 8 Roll Timer",
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

    row = backend.timers.find("Custom Timer", TRIGGER_TIMER_GROUP)
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

    assert format_mmss(-5) == "00:00"
    assert format_mmss(65) == "01:05"
    assert format_mmss(3723) == "1:02:03"


# -- countdown rounding + the shared one-second grid ---------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (30.0, "00:30"),  # exact: a fresh 30s timer reads its nominal value
        (29.001, "00:30"),  # ceiling, not truncation (EQTool divergence)
        (64.1, "01:05"),
        (0.2, "00:01"),  # any time left reads at least 1, never 0
        (0.0, "00:00"),
        (-5.0, "00:00"),
        (3722.5, "1:02:03"),
    ],
)
def test_format_mmss_ceils(seconds: float, expected: str) -> None:
    assert format_mmss(seconds) == expected


def _value_of(window, row_name: str) -> str:
    return next(w for w in window._row_widgets.values() if w.row_name == row_name)._value.text()


def test_fresh_row_shows_its_full_duration(qtbot):
    """A 30 s timer must read 00:30 the moment it starts — the old truncating
    format made it appear already one second in."""
    backend = make_backend()  # holds a 30 s "Custom Timer" anchored at NOW
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    window.refresh(now=NOW)
    assert _value_of(window, "Custom Timer") == "00:30"


def test_row_digit_holds_across_the_second(qtbot):
    """Renders sampled through one wall-clock second must all agree: a row
    steps on the boundary, not at some per-row sub-second phase.

    The wall-clock-anchored row is the one that used to drift — trigger
    timers, PigParse rolls, and restored buffs all arrive mid-second.
    """
    backend = make_backend()
    backend.timers.add_timer(
        TimerRow(
            name="Wall Clock",
            group=TRIGGER_TIMER_GROUP,
            updated_at=NOW,
            ends_at=NOW.replace(microsecond=613_000) + timedelta(seconds=300),
            total_duration_s=300.0,
        )
    )
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)
    seen: dict[str, set[str]] = {"Custom Timer": set(), "Wall Clock": set()}
    for micros in range(0, 1_000_000, 100_000):
        window.refresh(now=NOW.replace(microsecond=micros) + timedelta(seconds=10))
        for name, values in seen.items():
            values.add(_value_of(window, name))
    assert [len(v) for v in seen.values()] == [1, 1]


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

    header = next(
        h for h in window._headers.values() if h.property("group_key") == TRIGGER_TIMER_GROUP
    )
    pos = header.mapTo(window, header.rect().center())
    row, group = window._context_target(pos)
    assert row is None
    assert group == TRIGGER_TIMER_GROUP

    row, group = window._context_target(window.rect().bottomRight())
    assert row is None and group is None


def test_clear_row_group_and_all(qtbot):
    backend = make_backend()
    _add_category_rows(backend)
    window = _shown_window(qtbot, backend)

    row = backend.timers.find("Custom Timer", TRIGGER_TIMER_GROUP)
    window._clear_row(row)
    assert "Custom Timer" not in window.current_row_names()
    assert backend.timers.find("Custom Timer", TRIGGER_TIMER_GROUP) is None

    window._clear_group(BOATS_GROUP)
    assert "Butcherblock to Freeport" not in window.current_row_names()

    window._clear_all()
    assert backend.timers.snapshot() == []
    assert window.current_row_names() == []
    assert window.current_groups() == []


def test_clear_other_players_keeps_your_own_and_non_spell_rows(qtbot):
    backend = make_backend()  # seeds a YOU Clarity + a Custom Timer (TimerRow)
    backend.timers.add_spell(
        SpellRow(
            name="Aegolism",
            group="Joe",
            updated_at=NOW,
            spell=Spell(id=3, name="Aegolism"),
            ends_at=NOW + timedelta(minutes=35),
            total_duration_s=35 * 60.0,
        )
    )
    window = _shown_window(qtbot, backend)
    assert "Joe" in window.current_groups()

    window._clear_other_players()
    # your own buff and the (non-spell) custom timer survive; Joe's spell is gone.
    assert window.current_groups() == [YOU_GROUP, TRIGGER_TIMER_GROUP]
    assert "Aegolism" not in window.current_row_names()
    assert "Clarity" in window.current_row_names()


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


# -- bar fade to red (bar_fade_to_red) ----------------------------------------


def _bar_color_of(window, row_name: str) -> str:
    return next(w for w in window._row_widgets.values() if w.row_name == row_name)._color


def test_fade_color_endpoints():
    """A full bar is untouched; an empty one is the target red."""
    assert fade_color(COLOR_BENEFICIAL, 1.0) == COLOR_BENEFICIAL
    assert fade_color(COLOR_BENEFICIAL, 1.5) == COLOR_BENEFICIAL
    assert fade_color(COLOR_BENEFICIAL, 0.0) == COLOR_DETRIMENTAL
    assert fade_color(COLOR_BENEFICIAL, -0.5) == COLOR_DETRIMENTAL


def test_fade_color_is_monotonic_and_distinct():
    ramp = [fade_color(COLOR_BENEFICIAL, f / 10) for f in range(10, -1, -1)]
    assert ramp[0] == COLOR_BENEFICIAL and ramp[-1] == COLOR_DETRIMENTAL
    # The red channel climbs the whole way down — the bar visibly reddens.
    reds = [int(color[1:3], 16) for color in ramp]
    assert reds == sorted(reds)
    assert len(set(ramp)) >= 8


def test_fade_color_is_quantized():
    """Fractions inside one bucket must yield the SAME string — update_row only
    restyles when the color changes, and a continuous fade would defeat that."""
    step = 1.0 / FADE_STEPS
    assert fade_color(COLOR_BENEFICIAL, 0.5) == fade_color(COLOR_BENEFICIAL, 0.5 + step / 4)
    assert fade_color(COLOR_BENEFICIAL, 0.5) != fade_color(COLOR_BENEFICIAL, 0.5 - step)


def test_row_bar_color_excludes_boats_and_both_kinds_of_roll():
    spell = Spell(id=1, name="Clarity")
    beneficial = SpellRow(
        name="Clarity",
        group=YOU_GROUP,
        updated_at=NOW,
        spell=spell,
        ends_at=NOW,
        total_duration_s=100.0,
    )
    boat = TimerRow(
        name="Barrel Barge", group=BOATS_GROUP, updated_at=NOW, ends_at=NOW, total_duration_s=100.0
    )
    api_roll = TimerRow(
        name="Ring 8", group=ROLL_TIMER_GROUP, updated_at=NOW, ends_at=NOW, total_duration_s=100.0
    )
    random_roll = RollRow(
        name="Joe",
        group=" Random -- 333",
        updated_at=NOW,
        roll=100,
        max_roll=333,
        ends_at=NOW,
        total_duration_s=180.0,
    )
    for fraction in (1.0, 0.5, 0.0):
        assert row_bar_color(boat, fraction, True) == COLOR_TIMER
        assert row_bar_color(api_roll, fraction, True) == COLOR_TIMER
        assert row_bar_color(random_roll, fraction, True) == COLOR_ROLL
    # ...while an ordinary spell row does fade.
    assert row_bar_color(beneficial, 0.2, True) != COLOR_BENEFICIAL
    # ...and nothing fades with the setting off.
    assert row_bar_color(beneficial, 0.2, False) == COLOR_BENEFICIAL


def test_bar_reddens_over_a_rows_life(qtbot):
    backend = make_backend()  # Clarity: 35 minutes in the YOU group
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)

    window.refresh(now=NOW)
    assert _bar_color_of(window, "Clarity") == COLOR_BENEFICIAL

    window.refresh(now=NOW + timedelta(minutes=17, seconds=30))
    midway = _bar_color_of(window, "Clarity")

    window.refresh(now=NOW + timedelta(minutes=35))
    assert _bar_color_of(window, "Clarity") == COLOR_DETRIMENTAL

    reds = [int(c[1:3], 16) for c in (COLOR_BENEFICIAL, midway, COLOR_DETRIMENTAL)]
    assert reds == sorted(reds) and len(set(reds)) == 3

    # A recast returns the row to its full-duration color.
    window.refresh(now=NOW)
    assert _bar_color_of(window, "Clarity") == COLOR_BENEFICIAL


def test_bar_fade_can_be_turned_off(qtbot):
    backend = make_backend()
    backend.settings.spellwindow.bar_fade_to_red = False
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)

    for minutes in (0, 17, 34):
        window.refresh(now=NOW + timedelta(minutes=minutes))
        assert _bar_color_of(window, "Clarity") == COLOR_BENEFICIAL


def test_fade_does_not_restyle_every_tick(qtbot):
    """The quantization guard: a long row must not churn its stylesheet at the
    250 ms refresh rate (setStyleSheet forces a full Qt style re-parse)."""
    backend = make_backend()
    window = SpellTimerWindow(backend)
    qtbot.addWidget(window)

    seen = []
    for tick in range(20):  # 5 seconds of ticks on a 35-minute buff
        window.refresh(now=NOW + timedelta(milliseconds=250 * tick))
        seen.append(_bar_color_of(window, "Clarity"))
    assert len(set(seen)) == 1


# -- variable respawn ("pop") windows (#125) -----------------------------------

POP_BASE_S = 400.0
POP_WINDOW_S = 900.0
POP_OPENS_AT = NOW + timedelta(seconds=POP_BASE_S)
POP_CLOSES_AT = POP_OPENS_AT + timedelta(seconds=POP_WINDOW_S)


def _pop_row(name: str = "--Dead-- Trakanon") -> TimerRow:
    return TimerRow(
        name=name,
        group=MOB_TIMER_GROUP,
        updated_at=NOW,
        ends_at=POP_OPENS_AT,
        total_duration_s=POP_BASE_S,
        window_ends_at=POP_CLOSES_AT,
    )


def test_a_row_in_its_window_does_not_sort_to_the_top_of_its_section():
    """The bug this fixes: a phase-2 row's ends_at is in the past, so the old
    key pinned it above everything for the whole window."""
    pop = _pop_row()
    sooner = TimerRow(
        name="--Dead-- a gnoll",
        group=MOB_TIMER_GROUP,
        updated_at=NOW,
        ends_at=POP_OPENS_AT + timedelta(seconds=60),
        total_duration_s=60.0,
    )
    later = TimerRow(
        name="--Dead-- a bat",
        group=MOB_TIMER_GROUP,
        updated_at=NOW,
        ends_at=POP_OPENS_AT + timedelta(seconds=5000),
        total_duration_s=5000.0,
    )
    # Mid-window: 840 s of window left, so it belongs BETWEEN the two.
    mid = POP_OPENS_AT + timedelta(seconds=60)
    ordered = sorted([later, pop, sooner], key=lambda r: row_sort_key(r, mid, "time_remaining"))
    assert [r.name for r in ordered] == ["--Dead-- a gnoll", "--Dead-- Trakanon", "--Dead-- a bat"]


def test_row_sort_key_is_unchanged_before_the_window_opens():
    """Phase 1 must be byte-identical to the old behaviour."""
    pop = _pop_row()
    assert row_sort_key(pop, NOW, "time_remaining") == (POP_BASE_S, "--dead-- trakanon")
    assert row_sort_key(pop, NOW, "alphabetical") == (0, "--dead-- trakanon")


def test_bar_color_reads_the_pop_window_first():
    pop = _pop_row()
    # No ``now`` -> the old type coding, so every existing call site is intact.
    assert bar_color(pop) == COLOR_TIMER
    assert bar_color(pop, NOW) == COLOR_TIMER
    assert bar_color(pop, POP_OPENS_AT) == COLOR_POP_WINDOW
    assert row_bar_color(pop, 1.0, False, POP_OPENS_AT) == COLOR_POP_WINDOW
    assert row_bar_color(pop, 1.0, False, NOW) == COLOR_TIMER


def test_a_window_row_still_fades_and_the_faded_color_differs():
    """_fades stays true for a Mob Timers row: its phase-2 ratio is honest, so
    the red fade should visibly run across the window."""
    pop = _pop_row()
    full = row_bar_color(pop, 1.0, True, POP_OPENS_AT)
    nearly_gone = row_bar_color(pop, 0.05, True, POP_CLOSES_AT - timedelta(seconds=45))
    assert full == COLOR_POP_WINDOW
    assert nearly_gone != full


def test_window_row_renders_phase_one_then_pop(qtbot):
    backend = make_backend()
    # Fade off, so _color is the base colour and this test is about the phase
    # rather than about how far the red fade has run.
    backend.settings.spellwindow.bar_fade_to_red = False
    backend.timers.add_timer(_pop_row())
    window = _shown_window(qtbot, backend)

    window.refresh(now=NOW + timedelta(seconds=100))
    widget = next(w for w in window._row_widgets.values() if w.row_name == "--Dead-- Trakanon")
    # Phase 1: plain countdown to the window opening, ordinary timer colour.
    assert widget._value.text() == format_mmss(300.0)
    assert widget._color == COLOR_TIMER

    window.refresh(now=POP_OPENS_AT + timedelta(seconds=60))
    # Phase 2: counting to the LATEST possible pop, and it says so.
    assert widget._value.text() == f"POP {format_mmss(POP_WINDOW_S - 60)}"
    assert widget._color == COLOR_POP_WINDOW


def test_the_same_row_widget_survives_the_crossover(qtbot):
    """The no-subclass decision, pinned: refresh() keys widget reuse on
    type(row).__name__, so a WindowTimerRow subclass would rebuild the row at
    the exact moment the user is watching it flip."""
    backend = make_backend()
    backend.timers.add_timer(_pop_row())
    window = _shown_window(qtbot, backend)

    window.refresh(now=POP_OPENS_AT - timedelta(seconds=1))
    before = next(w for w in window._row_widgets.values() if w.row_name == "--Dead-- Trakanon")
    window.refresh(now=POP_OPENS_AT + timedelta(seconds=1))
    after = next(w for w in window._row_widgets.values() if w.row_name == "--Dead-- Trakanon")
    assert before is after


def test_the_phase_comes_from_now_not_from_the_stamp(qtbot):
    """The driver ticks at 100 ms and this window repaints at 250 ms, so a
    frame can land either side of tick(). Rendering must follow the frame's
    clock, or digits, bar and colour disagree for up to a quarter second."""
    backend = make_backend()
    backend.settings.spellwindow.bar_fade_to_red = False
    row = backend.timers.add_timer(_pop_row())
    window = _shown_window(qtbot, backend)

    # tick() has NOT run, so window_opened_at is unset — yet this frame is
    # inside the window and must render as such.
    assert row.window_opened_at is None
    window.refresh(now=POP_OPENS_AT + timedelta(seconds=30))
    widget = next(w for w in window._row_widgets.values() if w.row_name == "--Dead-- Trakanon")
    assert widget._value.text().startswith("POP ")
    assert widget._color == COLOR_POP_WINDOW
