"""Settings > DPS Meter — the page, its apply path, and the live seam."""

import pytest

from nparseplus.config.settings import Settings
from nparseplus.core.dps import DAMAGE_SOURCES, FightTracker
from nparseplus.ui.settingswindow import UnifiedSettingsWindow

pytestmark = pytest.mark.qt


def _legacy() -> dict:
    return {
        "maps": {"line_width": 1, "grid_line_width": 1, "opacity": 80},
        "discord": {"opacity": 80, "bg_opacity": 25},
    }


def _window(qtbot, settings: Settings, **kwargs) -> UnifiedSettingsWindow:
    window = UnifiedSettingsWindow(
        settings,
        on_save=lambda: None,
        legacy_config=_legacy(),
        **kwargs,
    )
    qtbot.addWidget(window)
    return window


def test_the_page_is_in_the_sidebar(qtbot) -> None:
    window = _window(qtbot, Settings())
    titles = [window._sidebar.item(i).text() for i in range(window._sidebar.count())]
    assert "DPS Meter" in titles


def test_the_page_shows_the_current_settings(qtbot) -> None:
    settings = Settings()
    settings.dps.damage_sources = "all"
    settings.dps.spell_credit_window_seconds = 4.0
    settings.dps.count_pet_damage = True
    settings.dps.fight_retention_seconds = 90.0
    settings.dps.trailing_window_seconds = 6.0
    settings.dps.session_min_fight_seconds = 5.0
    window = _window(qtbot, settings)
    assert window._dps_sources.currentData() == "all"
    assert window._dps_credit_window.value() == 4.0
    assert window._dps_count_pet.isChecked() is True
    assert window._dps_retention.value() == 90.0
    assert window._dps_window.value() == 6.0
    assert window._dps_session_min.value() == 5.0


def test_the_page_offers_every_mode_and_defaults_to_melee_plus_mine(qtbot) -> None:
    window = _window(qtbot, Settings())
    modes = [window._dps_sources.itemData(i) for i in range(window._dps_sources.count())]
    assert modes == list(DAMAGE_SOURCES)
    assert window._dps_sources.currentData() == "melee+mine"
    # Counting the pet as yours is opt-in — an opinion, not a fix.
    assert window._dps_count_pet.isChecked() is False


def test_apply_writes_every_knob_back(qtbot) -> None:
    settings = Settings()
    window = _window(qtbot, settings)
    window._dps_sources.setCurrentIndex(window._dps_sources.findData("melee"))
    window._dps_credit_window.setValue(3.5)
    window._dps_count_pet.setChecked(True)
    window._dps_retention.setValue(120.0)
    window._dps_window.setValue(8.0)
    window._dps_session_min.setValue(0.0)
    window.apply()
    assert settings.dps.damage_sources == "melee"
    assert settings.dps.spell_credit_window_seconds == 3.5
    assert settings.dps.count_pet_damage is True
    assert settings.dps.fight_retention_seconds == 120.0
    assert settings.dps.trailing_window_seconds == 8.0
    assert settings.dps.session_min_fight_seconds == 0.0


def test_apply_fires_the_live_callback(qtbot) -> None:
    calls: list[int] = []
    window = _window(qtbot, Settings(), on_dps_changed=lambda: calls.append(1))
    window.apply()
    assert calls == [1]


def test_apply_reaches_a_running_tracker_end_to_end(qtbot) -> None:
    """The seam the app wires: Apply -> Backend.apply_dps_settings -> tracker.

    Stands in for composition's wiring so the page cannot drift from the
    thing it configures without a test noticing.
    """
    settings = Settings()
    tracker = FightTracker(
        damage_sources=settings.dps.damage_sources,
        fight_retention_s=settings.dps.fight_retention_seconds,
        trailing_window_s=settings.dps.trailing_window_seconds,
        session_min_fight_s=settings.dps.session_min_fight_seconds,
        spell_credit_window_s=settings.dps.spell_credit_window_seconds,
        count_pet_damage=settings.dps.count_pet_damage,
    )

    def push() -> None:
        tracker.configure(
            damage_sources=settings.dps.damage_sources,
            fight_retention_s=settings.dps.fight_retention_seconds,
            trailing_window_s=settings.dps.trailing_window_seconds,
            session_min_fight_s=settings.dps.session_min_fight_seconds,
            spell_credit_window_s=settings.dps.spell_credit_window_seconds,
            count_pet_damage=settings.dps.count_pet_damage,
        )

    window = _window(qtbot, settings, on_dps_changed=push)
    assert tracker.damage_sources == "melee+mine"
    window._dps_sources.setCurrentIndex(window._dps_sources.findData("all"))
    window._dps_count_pet.setChecked(True)
    window._dps_retention.setValue(300.0)
    window._dps_window.setValue(4.0)
    window.apply()
    assert tracker.damage_sources == "all"
    assert tracker.count_pet_damage is True
    assert tracker.fight_retention_s == 300.0
    assert tracker.trailing_window_s == 4.0


def test_the_page_does_not_widen_the_window(qtbot) -> None:
    # The settings window's floor is its widest page; a new page must not
    # raise it (see MIN_SIZE and _scrollable).
    window = _window(qtbot, Settings())
    dps_index = next(
        i for i in range(window._sidebar.count()) if window._sidebar.item(i).text() == "DPS Meter"
    )
    page = window._stack.widget(dps_index)
    assert page.minimumSizeHint().width() <= window.minimumSizeHint().width()
