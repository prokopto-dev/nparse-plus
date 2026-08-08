"""The Appearance page, the tray skin submenu, and live skin switching.

A skin is the one look setting that applies without a restart — the tray can
flip it mid-fight — so "does the change actually reach the windows" is the
thing worth pinning down.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from nparseplus.config.settings import Settings, load_settings, save_settings
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.models import Spell
from nparseplus.core.timers import (
    MOB_TIMER_GROUP,
    ROLL_TIMER_GROUP,
    TRIGGER_TIMER_GROUP,
    YOU_GROUP,
    RollRow,
    SpellRow,
    TimerRow,
)
from nparseplus.helpers.application import build_skin_menu
from nparseplus.ui import skins
from nparseplus.ui.dpswindow import _AttackerRow
from nparseplus.ui.eventoverlay import EventOverlayWindow, split_alert_text
from nparseplus.ui.settingswindow import UnifiedSettingsWindow
from nparseplus.ui.spellwindow import SpellTimerWindow, header_kind

pytestmark = pytest.mark.qt


@pytest.fixture(autouse=True)
def _restore_skin():
    yield
    skins.set_skin(skins.DEFAULT_SKIN)


# -- settings persistence -------------------------------------------------------


def test_appearance_settings_round_trip(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    assert settings.general.skin == "duxa"
    assert settings.general.frame_opacity == 100
    assert settings.general.overlay_text_size == 32
    assert settings.general.alert_emphasis == "pulse"

    settings.general.skin = "velious"
    settings.general.frame_opacity = 72
    settings.general.overlay_text_size = 42
    settings.general.alert_emphasis = "glow"
    save_settings(settings, path)

    loaded = load_settings(path)
    assert loaded.general.skin == "velious"
    assert loaded.general.frame_opacity == 72
    assert loaded.general.overlay_text_size == 42
    assert loaded.general.alert_emphasis == "glow"


def test_a_settings_file_from_before_the_redesign_still_loads(tmp_path) -> None:
    """An upgrade must not need the new keys to exist."""
    path = tmp_path / "settings.json"
    path.write_text('{"general": {"theme": "dark", "font_size": 14}}')
    loaded = load_settings(path)
    assert loaded.general.skin == skins.DEFAULT_SKIN
    assert loaded.general.font_size == 14


# -- the Appearance page --------------------------------------------------------


def _settings_window(qtbot, settings, **kwargs):
    window = UnifiedSettingsWindow(settings, on_save=lambda: None, legacy_config={}, **kwargs)
    qtbot.addWidget(window)
    return window


def test_the_picker_opens_on_the_saved_skin(qtbot) -> None:
    settings = Settings()
    settings.general.skin = "ledger"
    window = _settings_window(qtbot, settings)
    assert window.selected_skin() == "ledger"


def test_picking_a_skin_previews_it_live(qtbot) -> None:
    applied: list[str] = []
    settings = Settings()
    window = _settings_window(
        qtbot, settings, on_appearance_changed=lambda: applied.append(settings.general.skin)
    )
    window._preview_skin("velious")
    assert window.selected_skin() == "velious"
    assert settings.general.skin == "velious"
    assert applied == ["velious"]


def test_closing_without_applying_puts_the_preview_back(qtbot) -> None:
    """The picker previews live; Close is the undo. Without this a curious
    click would silently redress every overlay for good."""
    applied: list[str] = []
    settings = Settings()
    window = _settings_window(
        qtbot, settings, on_appearance_changed=lambda: applied.append(settings.general.skin)
    )
    window._preview_skin("ledger")
    window._close_discarding_preview()
    assert settings.general.skin == "duxa"
    assert window.selected_skin() == "duxa"
    assert applied == ["ledger", "duxa"]


def test_apply_keeps_the_preview_and_re_baselines_it(qtbot) -> None:
    settings = Settings()
    saves: list[int] = []
    window = UnifiedSettingsWindow(
        settings,
        on_save=lambda: saves.append(1),
        legacy_config={},
        on_appearance_changed=lambda: None,
    )
    qtbot.addWidget(window)
    window._preview_skin("velious")
    window.apply()
    assert settings.general.skin == "velious"
    assert saves
    # A later Close must not revert what Apply made durable.
    window._close_discarding_preview()
    assert settings.general.skin == "velious"


def test_apply_writes_the_rest_of_the_appearance_page(qtbot) -> None:
    settings = Settings()
    window = _settings_window(qtbot, settings)
    window._overlay_text_size.setCurrentIndex(window._overlay_text_size.findData(42))
    window._alert_emphasis.setCurrentIndex(window._alert_emphasis.findData("glow"))
    window._frame_opacity.setValue(72)
    window._overlay_shadow.setChecked(False)
    window.apply()
    assert settings.general.overlay_text_size == 42
    assert settings.general.alert_emphasis == "glow"
    assert settings.general.frame_opacity == 72
    assert settings.general.overlay_text_shadow is False


def test_a_custom_text_size_from_the_json_survives_a_round_trip(qtbot) -> None:
    """Hand-edited settings are supported everywhere else; the combo must not
    quietly snap the value to its nearest preset."""
    settings = Settings()
    settings.general.overlay_text_size = 37
    window = _settings_window(qtbot, settings)
    window.apply()
    assert settings.general.overlay_text_size == 37


def test_show_page_selects_the_appearance_row(qtbot) -> None:
    window = _settings_window(qtbot, Settings())
    window.show_page("Appearance")
    assert window._sidebar.currentItem().text() == "Appearance"
    window.show_page("Not A Page")  # unknown titles just show the window
    assert window._sidebar.currentItem().text() == "Appearance"


# -- the tray submenu -----------------------------------------------------------


def test_tray_skin_menu_lists_every_skin_and_checks_the_active_one(qtbot) -> None:
    menu = QMenu()
    qtbot.addWidget(menu)
    actions, appearance = build_skin_menu(menu, current_skin="velious")
    assert [action.text() for action in actions] == [
        skins.SKINS[name].label for name in skins.SKIN_ORDER
    ]
    checked = [name for action, name in actions.items() if action.isChecked()]
    assert checked == ["velious"]
    assert appearance is not None


def test_tray_skin_menu_is_absent_without_a_callback(qtbot) -> None:
    menu = QMenu()
    qtbot.addWidget(menu)
    actions, appearance = build_skin_menu(menu, current_skin="duxa", enabled=False)
    assert actions == {} and appearance is None
    assert [action.text() for action in menu.actions()] == []


# -- live switching -------------------------------------------------------------


def _spell(name, group, detrimental=False, cooldown=False):
    now = datetime.now()
    return SpellRow(
        name=name,
        group=group,
        ends_at=now + timedelta(seconds=120),
        total_duration_s=300,
        updated_at=now,
        spell=Spell(id=1, name=name),
        detrimental=detrimental,
        is_cooldown=cooldown,
    )


class _Timers:
    def __init__(self, rows):
        self.rows = rows

    def snapshot(self):
        return list(self.rows)


def _backend(settings, rows):
    return SimpleNamespace(
        settings=settings, timers=_Timers(rows), player=ActivePlayer(), fights=None
    )


def test_switching_skin_relays_the_rows_live(qtbot) -> None:
    """Duxa stacks a bar under the row; Ledger paints the bar as the row. The
    switch has to reach existing widgets, not just newly created ones."""
    settings = Settings()
    skins.set_skin("duxa")
    window = SpellTimerWindow(_backend(settings, [_spell("Clarity", YOU_GROUP)]))
    qtbot.addWidget(window)
    window.refresh()
    row = next(iter(window._row_widgets.values()))
    assert row._bar.isVisible() or not window.isVisible()
    assert row.maximumHeight() > skins.LEDGER.row_height

    settings.general.skin = "ledger"
    skins.set_skin("ledger")
    window.apply_skin()
    row = next(iter(window._row_widgets.values()))
    assert row.height() == skins.LEDGER.row_height
    assert not row._bar.isVisible()


def test_ledger_rows_grow_with_the_user_font_size_instead_of_clipping(qtbot) -> None:
    settings = Settings()
    settings.general.font_size = 32
    settings.general.skin = "ledger"
    skins.set_skin("ledger")
    window = SpellTimerWindow(_backend(settings, [_spell("Clarity", YOU_GROUP)]))
    qtbot.addWidget(window)
    window.refresh()
    row = next(iter(window._row_widgets.values()))
    assert row.height() == skins.full_row_height(skins.LEDGER, 32)
    assert row.height() > skins.LEDGER.row_height


def test_ledger_dps_rows_grow_with_the_user_font_size(qtbot) -> None:
    row = _AttackerRow(skin=skins.LEDGER, font_size=32)
    qtbot.addWidget(row)
    assert row.minimumHeight() == skins.full_row_height(skins.LEDGER, 32) + 2
    assert row.maximumHeight() == row.minimumHeight()


def test_frame_opacity_reaches_the_panel_without_touching_window_opacity(qtbot) -> None:
    settings = Settings()
    settings.general.frame_opacity = 40
    window = SpellTimerWindow(_backend(settings, []))
    qtbot.addWidget(window)
    window.apply_skin()
    assert window._container._frame_opacity == pytest.approx(0.4)
    assert window.windowOpacity() == 1.0


# -- header accents -------------------------------------------------------------


def test_header_kind_maps_groups_to_the_accent_the_bars_use() -> None:
    assert header_kind(YOU_GROUP, []) == skins.KIND_YOU
    assert header_kind(TRIGGER_TIMER_GROUP, []) == skins.KIND_TIMER
    assert header_kind(MOB_TIMER_GROUP, []) == skins.KIND_TIMER
    assert header_kind(ROLL_TIMER_GROUP, []) == skins.KIND_ROLL


def test_a_debuffed_mob_reads_red_and_a_buffed_player_does_not() -> None:
    mob = [_spell("Malaise", "a sand giant", detrimental=True)]
    assert header_kind("a sand giant", mob) == skins.KIND_DETRIMENTAL
    friend = [_spell("Aegolism", "Tankenstein")]
    assert header_kind("Tankenstein", friend) == skins.KIND_PLAYER


def test_an_all_cooldown_group_reads_as_a_cooldown() -> None:
    rows = [_spell("Harm Touch", "Cooldowns", detrimental=True, cooldown=True)]
    assert header_kind("Cooldowns", rows) == skins.KIND_COOLDOWN


def test_a_group_of_only_rolls_reads_as_rolls_whatever_its_name() -> None:
    now = datetime.now()
    rolls = [
        RollRow(
            name="Joe",
            group="Raid",
            ends_at=now + timedelta(seconds=60),
            total_duration_s=60,
            updated_at=now,
            roll=99,
            max_roll=100,
        )
    ]
    assert header_kind("Raid", rolls) == skins.KIND_ROLL


def test_a_plain_timer_group_is_not_mistaken_for_a_player() -> None:
    now = datetime.now()
    rows = [
        TimerRow(
            name="Sand Giant pop",
            group=TRIGGER_TIMER_GROUP,
            ends_at=now + timedelta(seconds=60),
            total_duration_s=60,
            updated_at=now,
        )
    ]
    assert header_kind(TRIGGER_TIMER_GROUP, rows) == skins.KIND_TIMER


# -- alert splitting ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kicker", "headline"),
    [
        ("Gorenaire — ENRAGED", "Gorenaire", "ENRAGED"),
        ("Gorenaire - ENRAGED", "Gorenaire", "ENRAGED"),
        ("FTE: Someone", "FTE", "Someone"),
        ("ENRAGED", "", "ENRAGED"),
        ("", "", ""),
        ("— ENRAGED", "", "— ENRAGED"),  # nothing on the left to be a kicker
    ],
)
def test_split_alert_text(text, kicker, headline) -> None:
    assert split_alert_text(text) == (kicker, headline)


def test_the_overlay_reports_the_whole_alert_not_the_split_headline(qtbot) -> None:
    """``current_text`` feeds the reset match; splitting is presentation only."""
    from nparseplus.core.events import OverlayEvent

    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)
    overlay.handle_event(OverlayEvent(text="Gorenaire — ENRAGED", foreground="Yellow"))
    assert overlay.current_text() == "Gorenaire — ENRAGED"
    assert overlay._center_text.text() == "ENRAGED"
    overlay.handle_event(OverlayEvent(text="ENRAGED", reset=True))
    assert overlay.current_text() == "Gorenaire — ENRAGED"  # the halves don't match
    overlay.handle_event(OverlayEvent(text="Gorenaire — ENRAGED", reset=True))
    assert overlay.current_text() == ""


def test_alert_emphasis_pulses_only_while_an_alert_is_up(qtbot) -> None:
    from nparseplus.core.events import OverlayEvent

    overlay = EventOverlayWindow(emphasis="pulse")
    qtbot.addWidget(overlay)
    assert not overlay._pulse_timer.isActive()
    overlay.handle_event(OverlayEvent(text="ENRAGED", foreground="Yellow"))
    assert overlay._pulse_timer.isActive()
    overlay.clear_text()
    assert not overlay._pulse_timer.isActive()


def test_plain_emphasis_never_pulses(qtbot) -> None:
    from nparseplus.core.events import OverlayEvent

    overlay = EventOverlayWindow(emphasis="plain")
    qtbot.addWidget(overlay)
    overlay.handle_event(OverlayEvent(text="ENRAGED", foreground="Yellow"))
    assert not overlay._pulse_timer.isActive()


def test_overlay_text_size_reaches_the_headline(qtbot) -> None:
    overlay = EventOverlayWindow(text_size=48)
    qtbot.addWidget(overlay)
    assert "font-size: 48px" in overlay._center_text.styleSheet()
    overlay.apply_skin(text_size=22)
    assert "font-size: 22px" in overlay._center_text.styleSheet()


def test_overlay_base_font_size_reaches_the_kicker_not_the_headline(qtbot) -> None:
    overlay = EventOverlayWindow(font_size=20, text_size=48)
    qtbot.addWidget(overlay)
    kicker_size = skins.px(20, skins.DUXA.alert_kicker_scale)
    display_size = skins.px(20, skins.SMALL_DISPLAY.scale)
    assert f"font-size: {kicker_size}px" in overlay._alert_kicker.styleSheet()
    assert f'font-family: "{skins.NOTO_SANS}"' in overlay._alert_kicker.styleSheet()
    assert f"font-size: {display_size}px" in overlay._utility_header.styleSheet()
    assert all(
        f"font-size: {display_size}px" in chip.styleSheet()
        for chip in overlay._region_titles.values()
    )
    assert "font-size: 48px" in overlay._center_text.styleSheet()
    assert f'font-family: "{skins.NOTO_SANS}"' in overlay._center_text.styleSheet()

    overlay.apply_skin(font_size=10)
    kicker_size = skins.px(10, skins.DUXA.alert_kicker_scale)
    display_size = skins.px(10, skins.SMALL_DISPLAY.scale)
    assert f"font-size: {kicker_size}px" in overlay._alert_kicker.styleSheet()
    assert f"font-size: {display_size}px" in overlay._utility_header.styleSheet()
    assert "font-size: 48px" in overlay._center_text.styleSheet()


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_alert_content_is_centered_in_every_skin(qtbot, skin_name) -> None:
    skins.set_skin(skin_name)
    overlay = EventOverlayWindow()
    qtbot.addWidget(overlay)

    for widget in (overlay._alert_kicker, overlay._center_text):
        assert bool(widget.alignment() & Qt.AlignmentFlag.AlignHCenter)
        assert not bool(widget.alignment() & Qt.AlignmentFlag.AlignLeft)
    for widget in (overlay._alert_kicker, overlay._center_text, overlay._alert_rule):
        item = overlay._alert_layout.itemAt(overlay._alert_layout.indexOf(widget))
        assert bool(item.alignment() & Qt.AlignmentFlag.AlignHCenter)


def test_an_unknown_emphasis_falls_back_instead_of_raising(qtbot) -> None:
    overlay = EventOverlayWindow(emphasis="disco")
    qtbot.addWidget(overlay)
    assert overlay._emphasis == "pulse"
