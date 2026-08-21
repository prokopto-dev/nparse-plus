"""Rehearsing an alert (#85): the real path, and nothing left behind.

Everything here drives a real ``build_backend`` — the whole point of the
feature is that a test alert is not a mock, so a test of it that mocks the
pipeline would assert nothing. The driver is never started; ``_iterate`` is
called by hand where the deferral matters, exactly as ``test_driver_inbox``
does.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core import testalerts
from nparseplus.core.events import (
    CorpseMarkerEvent,
    FTEEvent,
    OverlayEvent,
    PlayerLocationEvent,
    RandomRollEvent,
    SlainEvent,
    SpellWornOffOtherEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.timers import MOB_TIMER_GROUP, YOU_GROUP, RollRow, TimerRow


class StubSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(self, text: str, *_a, **_k) -> None:
        self.said.append(text)

    def interrupt(self) -> None: ...
    def close(self) -> None: ...


@pytest.fixture
def backend():
    settings = Settings()
    settings.sharing.mode = "off"
    made = build_backend(settings, speaker=StubSpeaker())
    yield made
    made.stop()


@contextmanager
def accepting_commands(driver) -> Iterator[None]:
    """The state ``start()`` leaves the driver in, without the poll loop."""
    with driver._command_lock:
        driver._accepting_commands = True
    try:
        yield
    finally:
        with driver._command_lock:
            driver._accepting_commands = False


def collect(bus, *event_types) -> list[object]:
    seen: list[object] = []
    for event_type in event_types:
        bus.subscribe(event_type, seen.append)
    return seen


# -- the samples themselves ---------------------------------------------------


def test_every_sample_is_a_line_the_real_parser_chain_claims(backend) -> None:
    """A sample that stopped matching would still 'work' — silently, forever."""
    produced = {
        "fte": FTEEvent,
        "fte_timer": FTEEvent,
        "root_break": SpellWornOffOtherEvent,
        "random_roll": RandomRollEvent,
    }
    assert set(produced) == set(testalerts.SAMPLES_BY_KEY)
    for key, event_type in produced.items():
        seen = collect(backend.bus, event_type)
        assert backend.test_alerts.fire(key) is True
        assert seen, f"{key} reached no {event_type.__name__}"


def test_an_unknown_key_is_refused_and_queues_nothing(backend) -> None:
    seen = collect(backend.bus, OverlayEvent)
    assert backend.test_alerts.fire("death_loop") is False
    assert seen == []


def test_fte_speaks_and_overlays_exactly_as_a_live_engage_does(backend) -> None:
    alerts = collect(backend.bus, OverlayEvent)
    backend.test_alerts.fire("fte")
    assert [(a.text, a.foreground) for a in alerts] == [
        (f"{testalerts.TEST_PLAYER} FTE a training dummy", "Yellow")
    ]
    assert backend.speaker._delegate.said == [f"{testalerts.TEST_PLAYER} F T E a training dummy"]


def test_root_break_honours_the_toggles_rather_than_forcing_them(backend) -> None:
    """The one deliberate divergence from EQTool's RunTest, which forces them on."""
    backend.settings.general.root_break_overlay = False
    backend.settings.general.root_break_audio = False
    alerts = collect(backend.bus, OverlayEvent)
    backend.test_alerts.fire("root_break")
    assert alerts == []
    assert backend.speaker._delegate.said == []

    backend.settings.general.root_break_overlay = True
    backend.settings.general.root_break_audio = True
    backend.test_alerts.fire("root_break")
    assert [(a.text, a.foreground) for a in alerts] == [("Paralyzing Earth has worn off!", "Red")]
    assert backend.speaker._delegate.said == ["Paralyzing Earth has worn off!"]


def test_the_roll_sample_lands_as_one_group_of_three(backend) -> None:
    backend.test_alerts.fire("random_roll")
    rolls = [row for row in backend.timers.snapshot() if isinstance(row, RollRow)]
    assert sorted(row.roll for row in rolls) == [194, 742, 908]
    assert {row.group for row in rolls} == {f" Random -- {testalerts.TEST_ROLL_MAX}"}


# -- the thread contract ------------------------------------------------------


def test_nothing_is_published_at_enqueue_time(backend) -> None:
    """The bus and TimersService belong to the driver thread; a button does not."""
    alerts = collect(backend.bus, OverlayEvent)
    driver = backend.driver
    with accepting_commands(driver):
        fired: list[bool] = []
        caller = threading.Thread(target=lambda: fired.append(backend.test_alerts.fire("fte")))
        caller.start()
        caller.join(timeout=2)
        assert fired == [True]
        assert alerts == []  # queued, not run by the caller

        driver._iterate()
        assert len(alerts) == 1

        driver._iterate()  # drained means gone
        assert len(alerts) == 1


# -- what a rehearsal leaves behind -------------------------------------------


def test_the_two_stateless_samples_leave_no_row_at_all(backend) -> None:
    for key in ("fte", "root_break"):
        backend.test_alerts.fire(key)
        assert backend.timers.snapshot() == [], key


def test_the_rows_a_sample_does_leave_are_stamped_as_the_test_s(backend) -> None:
    for key in ("fte_timer", "random_roll"):
        backend.test_alerts.clear()
        backend.test_alerts.fire(key)
        rows = backend.timers.snapshot()
        assert rows, key
        assert {row.owner for row in rows} == {testalerts.TEST_OWNER}, key


def test_the_engage_rule_timer_is_the_real_one_not_a_relabelled_sample(backend) -> None:
    backend.test_alerts.fire("fte_timer")
    rows = [row for row in backend.timers.snapshot() if isinstance(row, TimerRow)]
    assert [row.name for row in rows] == ["--97% Rule-- Zlandicar"]
    assert rows[0].group == MOB_TIMER_GROUP
    assert rows[0].total_duration_s == 61.0


def test_firing_again_replaces_the_last_rehearsal_rather_than_stacking(backend) -> None:
    for _ in range(3):
        backend.test_alerts.fire("random_roll")
    assert len(backend.timers.snapshot()) == 3  # three rolls, not nine


def test_clear_takes_the_rows_back_and_leaves_real_ones_alone(backend) -> None:
    real = backend.timers.add_timer(
        TimerRow(
            name="--Dead-- a real mob",
            group=MOB_TIMER_GROUP,
            updated_at=datetime.now(),
            ends_at=datetime.now() + timedelta(minutes=20),
            total_duration_s=1200.0,
        )
    )
    backend.test_alerts.fire("fte_timer")
    assert len(backend.timers.snapshot()) == 2

    backend.test_alerts.clear()
    assert backend.timers.snapshot() == [real]


def test_a_rehearsed_row_is_never_persisted(backend) -> None:
    """It would come back after a restart as a row nothing owns."""
    backend.test_alerts.fire("fte_timer")
    assert backend.timers.snapshot()  # the row is there...
    saved = backend.timers.export_respawn_timers(MOB_TIMER_GROUP, datetime.now())
    assert saved == []  # ...and the store never sees it


def test_no_sample_touches_death_pet_or_fight_state(backend) -> None:
    """The audit behind leaving the death-loop alert out (see the module).

    Your own death persists a corpse marker, broadcasts a waypoint, resets pet
    tracking and freezes every fight targeting you. This pins that no sample
    that IS offered goes anywhere near it.
    """
    backend.bus.publish(
        PlayerLocationEvent(
            timestamp=datetime.now(), line="", line_number=1, location=Loc(x=1.0, y=2.0, z=3.0)
        )
    )
    backend.player.name = "Testcharacter"
    backend.player.zone = "kael"
    backend.player_pet.set_name("Vexer")
    deaths = collect(backend.bus, SlainEvent, CorpseMarkerEvent)

    for sample in testalerts.SAMPLES:
        backend.test_alerts.fire(sample.key)

    assert deaths == []
    assert backend.player_pet.pet_name == "Vexer"
    assert backend.fights.snapshot(datetime.now()) == []


def test_every_sample_declares_what_it_leaves(backend) -> None:
    """``leaves`` is what the button's tooltip promises; keep it true."""
    for sample in testalerts.SAMPLES:
        backend.test_alerts.clear()
        backend.test_alerts.fire(sample.key)
        left = bool(backend.timers.snapshot())
        assert left == bool(sample.leaves), sample.key


def test_a_row_of_your_own_is_never_stamped_as_the_test_s(backend) -> None:
    """A synthetic line is still a LineEvent, and ``SpellTimerHandler``
    finalizes a self-spell that prints no completion message on whatever line
    arrives after its cast time. That row is the player's, not the test's, so
    a YOU_GROUP row appearing mid-rehearsal must survive the clear."""
    added: list[str] = []

    def add_a_self_row() -> None:
        if added:
            return
        added.append("Harmshield")
        backend.timers.add_timer(
            TimerRow(
                name="Harmshield",
                group=YOU_GROUP,
                updated_at=datetime.now(),
                ends_at=datetime.now() + timedelta(seconds=18),
                total_duration_s=18.0,
            )
        )

    backend.timers.on_change.append(add_a_self_row)
    backend.test_alerts.fire("fte_timer")
    backend.test_alerts.clear()

    left = {row.name: row for row in backend.timers.snapshot()}
    assert "Harmshield" in left
    assert left["Harmshield"].owner == ""
    assert "--97% Rule-- Zlandicar" not in left  # the rehearsal's own row went
