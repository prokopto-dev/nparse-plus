"""Copying a fight parse out of the DPS meter (#78).

The clipboard is injected, so no test here touches the machine's real one —
see ``ui/clipboard.system_clipboard_copy`` for why that matters on Windows.
"""

from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QPoint

from nparseplus.config.settings import Settings
from nparseplus.core.dps import FightTracker
from nparseplus.core.events import DamageEvent, SlainEvent
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database
from nparseplus.ui.dpswindow import DpsMeterWindow, _AttackerRow

pytestmark = pytest.mark.qt

T0 = datetime(2026, 7, 15, 10, 0, 0)


def _damage(seconds: float, attacker: str, target: str, dmg: int) -> DamageEvent:
    return DamageEvent(
        timestamp=T0 + timedelta(seconds=seconds),
        target_name=target,
        attacker_name=attacker,
        damage_done=dmg,
        damage_type="slash",
    )


class _FakeBackend:
    def __init__(self, zone: str = "permafrost") -> None:
        self.settings = Settings()
        self.fights = FightTracker()
        self.zones = load_zone_database()
        self.player = ActivePlayer(name="Genartik", zone=zone)


class _Clipboard:
    """Records what would have gone to the system clipboard."""

    def __init__(self, works: bool = True) -> None:
        self.texts: list[str] = []
        self._works = works

    def __call__(self, text: str) -> bool:
        self.texts.append(text)
        return self._works


@pytest.fixture
def backend() -> _FakeBackend:
    return _FakeBackend()


@pytest.fixture
def clipboard() -> _Clipboard:
    return _Clipboard()


def _window(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> DpsMeterWindow:
    window = DpsMeterWindow(backend, copy_to_clipboard=clipboard)
    qtbot.addWidget(window)
    window.refresh()
    return window


def test_copy_fight_puts_the_parse_on_the_clipboard(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    backend.fights.add_damage(_damage(1, "Vebanab", "Lady Vox", 30))
    window = _window(qtbot, backend, clipboard)

    assert window.copy_fight("Lady Vox")
    assert len(clipboard.texts) == 1
    assert clipboard.texts[0].startswith("Fight Details: Lady Vox Dmg: 100    You 70% ")


def test_copying_an_unknown_fight_writes_nothing(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    window = _window(qtbot, backend, clipboard)
    assert not window.copy_fight("a gnoll")
    assert clipboard.texts == []


def test_a_copy_announces_itself(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    """EQTool raised a balloon; the window says so and app.py owns the tray."""
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    window = _window(qtbot, backend, clipboard)
    with qtbot.waitSignal(window.parse_copied, timeout=500) as caught:
        window.copy_fight("lady vox")  # casefolded, like end_fight
    assert caught.args == ["Lady Vox"]


def test_a_failed_clipboard_write_is_not_announced(qtbot, backend: _FakeBackend) -> None:
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    clipboard = _Clipboard(works=False)
    window = _window(qtbot, backend, clipboard)
    seen: list[str] = []
    window.parse_copied.connect(seen.append)
    assert not window.copy_fight("Lady Vox")
    assert seen == []


# -- auto-copy on a notable kill -------------------------------------------------


def _slain(victim: str) -> SlainEvent:
    return SlainEvent(timestamp=T0 + timedelta(seconds=30), victim=victim)


def test_a_notable_kill_copies_itself(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_slain("Lady Vox"))
    assert clipboard.texts and "Fight Details: Lady Vox" in clipboard.texts[0]


def test_a_kael_faction_kill_does_not(qtbot, clipboard: _Clipboard) -> None:
    backend = _FakeBackend(zone="kael")
    victim = backend.zones.kael_faction_mobs[0]
    backend.fights.add_damage(_damage(0, "You", victim, 70))
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_slain(victim))
    assert clipboard.texts == []


def test_ordinary_trash_does_not(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    backend.fights.add_damage(_damage(0, "You", "a decaying skeleton", 70))
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_slain("a decaying skeleton"))
    assert clipboard.texts == []


def test_the_setting_turns_auto_copy_off_but_not_the_manual_copy(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    backend.settings.dps.auto_copy_notable_kills = False
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_slain("Lady Vox"))
    assert clipboard.texts == []
    assert window.copy_fight("Lady Vox")


def test_auto_copy_defaults_on_to_match_eqtool() -> None:
    assert Settings().dps.auto_copy_notable_kills is True


def test_other_events_are_ignored(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_damage(0, "You", "Lady Vox", 70))
    assert clipboard.texts == []


# -- the context menu ------------------------------------------------------------


def test_the_context_menu_offers_every_fight_on_screen(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    backend.fights.add_damage(_damage(1, "You", "a gnoll", 10))
    window = _window(qtbot, backend, clipboard)
    assert window.current_targets() == ["Lady Vox", "a gnoll"]


def test_clicking_an_attacker_row_offers_only_that_row_s_group(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    """A right-click on a row means "this fight", not "pick one"."""
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    backend.fights.add_damage(_damage(1, "You", "a gnoll", 10))
    window = _window(qtbot, backend, clipboard)
    window.show()
    qtbot.waitExposed(window)
    window.refresh()

    rows = window.findChildren(_AttackerRow)
    gnoll = next(row for row in rows if row.target_name == "a gnoll")
    inside = gnoll.mapTo(window, gnoll.rect().center())
    assert window.menu_targets(inside) == ["a gnoll"]


def test_clicking_outside_any_group_offers_every_fight(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    """The session footer belongs to no fight, so it offers all of them."""
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    backend.fights.add_damage(_damage(1, "You", "a gnoll", 10))
    window = _window(qtbot, backend, clipboard)
    window.show()
    qtbot.waitExposed(window)
    window.refresh()

    footer = window._footer
    outside = footer.mapTo(window, footer.rect().center())
    assert window.menu_targets(outside) == ["Lady Vox", "a gnoll"]


def test_an_empty_meter_offers_nothing(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    window = _window(qtbot, backend, clipboard)
    assert window.menu_targets(QPoint(0, 0)) == []
