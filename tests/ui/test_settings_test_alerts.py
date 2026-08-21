"""Settings > Audio & Overlays — the Test alerts group (#85).

The buttons are the whole Qt half of the feature: they name a sample and hand
it to the injected runner. Everything they do afterwards is Qt-free and lives
in ``tests/core/test_testalerts.py``.
"""

import pytest
from PySide6.QtWidgets import QGroupBox, QPushButton

from nparseplus.config.settings import Settings
from nparseplus.core import testalerts
from nparseplus.ui.settingswindow import UnifiedSettingsWindow

pytestmark = pytest.mark.qt


class FakeRunner:
    def __init__(self) -> None:
        self.fired: list[str] = []
        self.cleared = 0

    def fire(self, key: str) -> bool:
        self.fired.append(key)
        return True

    def clear(self) -> None:
        self.cleared += 1


def _legacy() -> dict:
    return {
        "maps": {"line_width": 1, "grid_line_width": 1, "opacity": 80},
        "discord": {"opacity": 80, "bg_opacity": 25},
    }


def _window(qtbot, **kwargs) -> UnifiedSettingsWindow:
    window = UnifiedSettingsWindow(
        Settings(), on_save=lambda: None, legacy_config=_legacy(), **kwargs
    )
    qtbot.addWidget(window)
    return window


def _test_buttons(window: UnifiedSettingsWindow) -> list[QPushButton]:
    page = window._pages_by_name["Audio & Overlays"]
    for box in page.findChildren(QGroupBox):
        if box.title() == "Test alerts":
            return box.findChildren(QPushButton)
    return []


def test_there_is_one_button_per_sample(qtbot) -> None:
    window = _window(qtbot, test_alerts=FakeRunner())
    buttons = _test_buttons(window)
    assert len(buttons) == len(testalerts.SAMPLES)
    for button, sample in zip(buttons, testalerts.SAMPLES, strict=True):
        assert sample.label in button.text()
        assert sample.blurb in button.toolTip()


def test_a_button_names_its_sample_to_the_runner(qtbot) -> None:
    runner = FakeRunner()
    window = _window(qtbot, test_alerts=runner)
    for button in _test_buttons(window):
        button.click()
    assert runner.fired == [sample.key for sample in testalerts.SAMPLES]


def test_a_sample_that_leaves_a_row_says_so_in_its_tooltip(qtbot) -> None:
    window = _window(qtbot, test_alerts=FakeRunner())
    tips = {
        sample.key: button.toolTip()
        for sample, button in zip(testalerts.SAMPLES, _test_buttons(window), strict=True)
    }
    for sample in testalerts.SAMPLES:
        assert ("Leaves" in tips[sample.key]) == bool(sample.leaves), sample.key


def test_hiding_the_window_takes_the_rehearsal_back(qtbot) -> None:
    runner = FakeRunner()
    window = _window(qtbot, test_alerts=runner)
    window.show()
    before = runner.cleared
    window.hide()
    assert runner.cleared == before + 1


def test_without_a_runner_the_group_offers_nothing_to_press(qtbot) -> None:
    """A settings window built with no backend (tests, and app.py before the
    driver exists) must still build its page."""
    window = _window(qtbot)
    assert _test_buttons(window) == []
