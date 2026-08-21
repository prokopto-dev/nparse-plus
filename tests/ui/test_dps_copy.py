"""Copying a fight parse out of the DPS meter (#78).

The clipboard is injected, so no test here touches the machine's real one —
see ``ui/clipboard.system_clipboard_copy`` for why that matters on Windows.
"""

from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QPoint

from nparseplus.config.settings import Settings, get_player
from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker
from nparseplus.core.enums import Server
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    DamageEvent,
    DpsBestResetEvent,
    NotableKillEvent,
    SlainEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.handlers.player_profile import PlayerProfileHandler
from nparseplus.core.handlers.you_zoned import YouZonedHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database
from nparseplus.ui.dpswindow import DpsMeterWindow, _AttackerRow
from nparseplus.ui.qtbridge import QtEventBridge

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
    """Stands in for the slice of ``composition.Backend`` the window uses.

    ``dps_best_owner``/``reset_dps_best`` mirror the real seam: the reset is
    refused unless the token handed back still names the current character.
    """

    def __init__(self) -> None:
        self.settings = Settings()
        self.fights = FightTracker()
        self.best_owner: object = ("genartik", "green")
        self.reset_calls: list[object] = []

    def dps_best_owner(self) -> object:
        return self.best_owner

    def reset_dps_best(self, expect: object) -> None:
        self.reset_calls.append(expect)
        if expect == self.best_owner:  # the driver-thread check, in miniature
            self.fights.reset_best()


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
        assert window.copy_fight("lady vox")  # casefolded, like end_fight
    assert caught.args == ["lady vox"]


def test_a_failed_clipboard_write_is_not_announced(qtbot, backend: _FakeBackend) -> None:
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    clipboard = _Clipboard(works=False)
    window = _window(qtbot, backend, clipboard)
    seen: list[str] = []
    window.parse_copied.connect(seen.append)
    assert not window.copy_fight("Lady Vox")
    assert seen == []


# -- auto-copy on a notable kill -------------------------------------------------
#
# Whether a kill IS notable is decided on the driver thread and tested in
# tests/core/dps/test_notable_kill.py — it turns on the zone the kill happened
# in, which this thread cannot observe correctly. All the window owes the
# feature is the setting and the clipboard.


def _notable(victim: str, parse: str = "", zone: str = "permafrost") -> NotableKillEvent:
    return NotableKillEvent(
        timestamp=T0 + timedelta(seconds=30),
        victim=victim,
        zone=zone,
        parse=parse or f"Fight Details: {victim} Dmg: 70    You 100% DPS:2 DMG:70",
    )


def test_a_notable_kill_copies_itself(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_notable("Lady Vox"))
    assert clipboard.texts and "Fight Details: Lady Vox" in clipboard.texts[0]


def test_the_carried_parse_is_copied_even_with_no_rows_left(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    """The meter is empty — zoning out cleared it before the GUI woke up.

    The window must still copy, because the parse it is copying was built at
    the kill. Re-reading the rows here is exactly what would lose a boss
    killed on the way out of the zone.
    """
    window = _window(qtbot, backend, clipboard)
    assert backend.fights.snapshot(T0) == []
    window.handle_event(_notable("Lady Vox"))
    assert clipboard.texts == ["Fight Details: Lady Vox Dmg: 70    You 100% DPS:2 DMG:70"]


def test_the_setting_turns_auto_copy_off_but_not_the_manual_copy(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    backend.settings.dps.auto_copy_notable_kills = False
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_notable("Lady Vox"))
    assert clipboard.texts == []
    assert window.copy_fight("Lady Vox")


def test_auto_copy_defaults_on_to_match_eqtool() -> None:
    assert Settings().dps.auto_copy_notable_kills is True


def test_other_events_are_ignored(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    """Including the slain line itself: the window never re-judges a kill."""
    backend.fights.add_damage(_damage(0, "You", "Lady Vox", 70))
    window = _window(qtbot, backend, clipboard)
    window.handle_event(_damage(1, "You", "Lady Vox", 70))
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


# -- the session controls (#83) ---------------------------------------------------


def test_start_new_session_moves_now_into_last(
    qtbot, backend: _FakeBackend, clipboard: _Clipboard
) -> None:
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 900))
    backend.fights.end_fight("a gnoll", T0 + timedelta(seconds=25))
    window = _window(qtbot, backend, clipboard)
    assert backend.fights.session_summary().last_session is None

    window.end_session()
    summary = backend.fights.session_summary()
    assert summary.last_session is not None
    assert summary.last_session.total_damage == 900
    assert summary.current_session.total_damage == 0
    assert summary.best.total_damage == 900  # the lifetime record is untouched


def test_clear_last_session_drops_it(qtbot, backend: _FakeBackend, clipboard: _Clipboard) -> None:
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 900))
    backend.fights.end_fight("a gnoll", T0 + timedelta(seconds=25))
    window = _window(qtbot, backend, clipboard)
    window.end_session()
    window.clear_last_session()
    assert backend.fights.session_summary().last_session is None


class _Confirm:
    """Stands in for the modal, so no test blocks on ``QMessageBox.exec``."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.titles: list[str] = []

    def __call__(self, parent, title: str, text: str) -> bool:
        self.titles.append(title)
        return self.answer


def test_reset_best_asks_first_and_obeys_a_refusal(qtbot, backend: _FakeBackend) -> None:
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 900))
    backend.fights.end_fight("a gnoll", T0 + timedelta(seconds=25))
    confirm = _Confirm(answer=False)
    window = DpsMeterWindow(backend, copy_to_clipboard=_Clipboard(), confirm_reset=confirm)
    qtbot.addWidget(window)

    window.reset_best()
    assert confirm.titles == ["Reset best"]
    assert backend.fights.session_summary().best.total_damage == 900


def test_reset_best_clears_the_record_but_not_the_session(qtbot, backend: _FakeBackend) -> None:
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 900))
    backend.fights.end_fight("a gnoll", T0 + timedelta(seconds=25))
    window = DpsMeterWindow(
        backend, copy_to_clipboard=_Clipboard(), confirm_reset=_Confirm(answer=True)
    )
    qtbot.addWidget(window)

    window.reset_best()
    summary = backend.fights.session_summary()
    assert summary.best.total_damage == 0
    assert summary.current_session.total_damage == 900

    # The footer follows the driver's answer, not the click — the reset is
    # dispatched, so there is nothing to render until it comes back.
    window.handle_event(DpsBestResetEvent(timestamp=T0, cleared=True))
    assert window.footer_text().startswith("Best 0 dps")


def test_a_character_change_during_the_dialog_cancels_the_reset(
    qtbot, backend: _FakeBackend
) -> None:
    """The confirmation runs a modal loop and the driver keeps parsing.

    It checks for a log-file switch every three seconds, so a dialog left open
    that long can span one. The reset must not land on whoever the driver
    switched to — that would destroy the lifetime record of a character the
    user was not even looking at.
    """
    backend.fights.add_damage(_damage(0, "You", "a gnoll", 900))
    backend.fights.end_fight("a gnoll", T0 + timedelta(seconds=25))

    class _SwitchWhileOpen(_Confirm):
        """The driver switches characters while the user reads the dialog."""

        def __call__(self, parent, title: str, text: str) -> bool:
            backend.best_owner = ("vebanab", "green")
            return super().__call__(parent, title, text)

    window = DpsMeterWindow(
        backend, copy_to_clipboard=_Clipboard(), confirm_reset=_SwitchWhileOpen(answer=True)
    )
    qtbot.addWidget(window)
    window.reset_best()

    # Dispatched with the stale token, and the backend's own check refuses it.
    assert backend.reset_calls == [("genartik", "green")]
    assert backend.fights.session_summary().best.total_damage == 900


def test_the_refusal_is_raised_from_the_driver_s_answer(qtbot, backend: _FakeBackend) -> None:
    """Not from a local guess: the window cannot see the switch that matters.

    ``submit_to_driver`` queues the reset and the driver drains it up to a
    poll interval later, so a GUI-side check can pass and the reset still be
    refused. Only ``DpsBestResetEvent`` knows which happened.
    """
    window = _window(qtbot, backend, _Clipboard())

    with qtbot.waitSignal(window.reset_refused, timeout=500):
        window.handle_event(DpsBestResetEvent(timestamp=T0, cleared=False))


def test_a_reset_that_succeeded_announces_nothing(qtbot, backend: _FakeBackend) -> None:
    window = _window(qtbot, backend, _Clipboard())
    refused: list[int] = []
    window.reset_refused.connect(lambda: refused.append(1))

    window.handle_event(DpsBestResetEvent(timestamp=T0, cleared=True))
    assert refused == []


def test_the_reset_carries_the_character_it_was_asked_for(qtbot, backend: _FakeBackend) -> None:
    """The token captured before the dialog is what reaches the driver."""
    window = DpsMeterWindow(
        backend, copy_to_clipboard=_Clipboard(), confirm_reset=_Confirm(answer=True)
    )
    qtbot.addWidget(window)

    window.reset_best()
    assert backend.reset_calls == [("genartik", "green")]


def test_saying_no_dispatches_nothing_and_announces_nothing(qtbot, backend: _FakeBackend) -> None:
    """Saying No is not the same as being overtaken by a character change."""
    window = DpsMeterWindow(
        backend, copy_to_clipboard=_Clipboard(), confirm_reset=_Confirm(answer=False)
    )
    qtbot.addWidget(window)
    refused: list[int] = []
    window.reset_refused.connect(lambda: refused.append(1))

    window.reset_best()
    assert refused == []
    assert backend.reset_calls == []


# -- end to end: the batch the bridge really delivers -----------------------------


def test_the_after_slain_youzoned_batch_still_copies(qtbot) -> None:
    """The whole path: driver publishes, the bridge buffers, the window drains.

    ``AfterPlayerChangedEvent`` restores Permafrost from the profile, Lady Vox
    dies there, and the player zones to Kael — all parsed before the GUI wakes
    up, which is exactly what ``QtEventBridge`` is for. By the time the window
    sees anything, ``player.zone`` is Kael, so any zone this thread sampled
    would lose the kill. Nothing here samples one.
    """
    settings = Settings()
    get_player(settings, "Genartik", "green").zone = "permafrost"

    bus = EventBus()
    player = ActivePlayer(name="Genartik", server=Server.GREEN, zone="")
    tracker = FightTracker()
    YouZonedHandler(bus, player)
    PlayerProfileHandler(bus, player, settings)
    DpsHandler(bus, player, tracker, zones=load_zone_database())

    backend = _FakeBackend()
    backend.settings = settings
    backend.fights = tracker
    clipboard = _Clipboard()
    window = DpsMeterWindow(backend, copy_to_clipboard=clipboard)
    qtbot.addWidget(window)

    bridge = QtEventBridge(bus)
    bridge.event_received.connect(window.handle_event)

    # One poll of the log: everything below is parsed before the GUI runs.
    bus.publish(_damage(0, "You", "Lady Vox", 70))
    bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    bus.publish(SlainEvent(timestamp=T0 + timedelta(seconds=1), victim="Lady Vox"))
    bus.publish(
        YouZonedEvent(
            timestamp=T0 + timedelta(seconds=2), long_name="Kael Drakkel", short_name="kael"
        )
    )
    assert player.zone == "kael"
    assert clipboard.texts == []  # nothing has reached the GUI thread yet

    bridge.flush_now()

    assert clipboard.texts and "Fight Details: Lady Vox" in clipboard.texts[0]
    bridge.detach()
