"""The lifetime Best row is per character and survives a restart (#83).

Qt-free, and never touches the real settings.json — every Settings here is
built in memory or written to a tmp path.
"""

from datetime import datetime, timedelta

import pytest

from nparseplus.config.settings import Settings, get_player, load_settings, save_settings
from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker, PlayerDamage
from nparseplus.core.enums import Server
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    BeforePlayerChangedEvent,
    DamageEvent,
)
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.handlers.dps_persistence import DpsPersistenceHandler
from nparseplus.core.player import ActivePlayer

T0 = datetime(2026, 7, 8, 21, 0, 0)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def saves() -> list[int]:
    return []


class _Rig:
    """The two DPS handlers on one bus, wired in composition's order."""

    def __init__(self, settings: Settings, saves: list[int]) -> None:
        self.bus = EventBus()
        self.settings = settings
        self.player = ActivePlayer(name="Genartik", server=Server.GREEN)
        self.tracker = FightTracker()
        self.saves = saves
        # DpsHandler first, as composition builds it: it clears the meter on
        # BeforePlayerChangedEvent, which is what stops the outgoing
        # character's live fights re-merging into the incoming one.
        self.dps = DpsHandler(self.bus, self.player, self.tracker)
        self.handler = DpsPersistenceHandler(
            self.bus,
            self.player,
            settings,
            self.tracker,
            request_save=lambda: saves.append(1),
        )

    def switch_to(self, name: str) -> None:
        """The Before/After pair the log-file swap publishes."""
        self.bus.publish(BeforePlayerChangedEvent(timestamp=T0))
        self.player.name = name
        self.bus.publish(AfterPlayerChangedEvent(timestamp=T0))

    def stored(self, name: str):
        return get_player(self.settings, name, self.player.server_key).best_damage


@pytest.fixture
def rig(settings: Settings, saves: list[int]) -> _Rig:
    return _Rig(settings, saves)


def _long_fight(tracker: FightTracker, damage: int, target: str = "a gnoll") -> None:
    """A fight long enough to clear the 20 s session gate."""
    tracker.add_damage(
        DamageEvent(
            timestamp=T0,
            target_name=target,
            attacker_name="You",
            damage_done=damage,
            damage_type="hit",
        )
    )
    tracker.end_fight(target, T0 + timedelta(seconds=25))


# -- round trip -------------------------------------------------------------------


def test_the_best_is_written_into_the_active_character_s_profile(rig: _Rig) -> None:
    _long_fight(rig.tracker, 900)
    saved = rig.stored("Genartik")
    assert saved is not None
    assert saved.total_damage == 900
    assert saved.highest_hit == 900
    assert rig.saves  # the debounced saver was asked to persist


def test_it_round_trips_through_a_real_settings_file(tmp_path) -> None:
    """What a restart actually does: write the file, read it back."""
    path = tmp_path / "settings.json"
    settings = Settings()
    rig = _Rig(settings, [])
    _long_fight(rig.tracker, 1234)
    save_settings(settings, path)

    reloaded = load_settings(path)
    fresh = _Rig(reloaded, [])
    fresh.bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    assert fresh.tracker.best.total_damage == 1234
    assert fresh.tracker.best.highest_hit == 1234


def test_only_changes_to_the_best_ask_for_a_save(rig: _Rig) -> None:
    """``on_change`` fires on every damage line; ``request_save`` arms a
    thread, so an unguarded export would start one per hit."""
    _long_fight(rig.tracker, 900)
    before = len(rig.saves)
    # More damage, but nothing that beats the max-merged reading.
    _long_fight(rig.tracker, 10, target="a rat")
    assert len(rig.saves) == before


# -- per character ----------------------------------------------------------------


def test_switching_characters_swaps_the_best(rig: _Rig) -> None:
    _long_fight(rig.tracker, 900, target="a gnoll")
    rig.switch_to("Vebanab")
    assert rig.tracker.best == PlayerDamage()

    _long_fight(rig.tracker, 100, target="a rat")
    assert rig.tracker.best.total_damage == 100

    rig.switch_to("Genartik")
    assert rig.tracker.best.total_damage == 900


def test_a_best_never_bleeds_between_characters(rig: _Rig) -> None:
    """Restoring assigns rather than max-merging, or a rogue's record would
    stick to the cleric they shared a session with."""
    _long_fight(rig.tracker, 9000, target="a gnoll")
    rig.switch_to("Vebanab")
    _long_fight(rig.tracker, 5, target="a rat")
    assert rig.tracker.best.total_damage == 5
    assert rig.stored("Vebanab").total_damage == 5
    assert rig.stored("Genartik").total_damage == 9000


def test_a_character_with_no_server_key_is_not_stored(settings: Settings) -> None:
    rig = _Rig(settings, [])
    rig.player.server = None
    _long_fight(rig.tracker, 900)
    assert settings.players == []


def test_a_character_switch_clears_the_meter(rig: _Rig) -> None:
    """The rows on screen belong to the character who just left.

    Without this the outgoing character's still-live fight is re-merged into
    the incoming one's session by the next ``end_fight`` or ``tick`` — a bleed
    the per-character best exists to prevent, and one the fold on
    BeforePlayerChangedEvent has to happen before.
    """
    rig.tracker.add_damage(
        DamageEvent(
            timestamp=T0,
            target_name="a gnoll",
            attacker_name="You",
            damage_done=900,
            damage_type="hit",
        )
    )
    assert rig.tracker.fights
    rig.switch_to("Vebanab")
    assert rig.tracker.fights == []


def test_the_outgoing_character_keeps_their_last_fight(rig: _Rig) -> None:
    """Clearing folds the stats in first, while they are still active."""
    rig.tracker.add_damage(
        DamageEvent(
            timestamp=T0,
            target_name="a gnoll",
            attacker_name="You",
            damage_done=900,
            damage_type="hit",
        )
    )
    rig.tracker.tick(T0 + timedelta(seconds=25))  # past the 20 s session gate
    rig.switch_to("Vebanab")
    assert rig.stored("Genartik").total_damage == 900
    assert rig.tracker.best == PlayerDamage()


# -- the measurement-rules fingerprint ---------------------------------------------


def test_a_measurement_change_clears_the_best_and_persists_the_clearing(rig: _Rig) -> None:
    _long_fight(rig.tracker, 900)
    assert rig.stored("Genartik").total_damage == 900

    rig.tracker.configure(trailing_window_s=4.0)  # a measurement rule moved

    assert rig.tracker.best == PlayerDamage()
    assert rig.stored("Genartik").total_damage == 0


def test_a_stored_best_carries_the_rules_it_was_measured_under(rig: _Rig) -> None:
    _long_fight(rig.tracker, 900)
    assert rig.stored("Genartik").measurement_rules == rig.tracker.measurement_rules_key()


def test_a_best_measured_under_other_rules_is_not_restored(rig: _Rig) -> None:
    """The hole a live reset cannot reach: the knob moved while this character
    was not the one logged in."""
    _long_fight(rig.tracker, 900)
    rig.switch_to("Vebanab")
    # Genartik is not the active character, so this reset never reaches their
    # stored record — only the fingerprint can.
    rig.tracker.configure(damage_sources="all")
    assert rig.stored("Genartik").total_damage == 900

    rig.switch_to("Genartik")
    assert rig.tracker.best == PlayerDamage()
    # And the incomparable reading is gone from disk, not merely hidden.
    assert rig.stored("Genartik").total_damage == 0
    assert rig.stored("Genartik").measurement_rules == rig.tracker.measurement_rules_key()


def test_display_only_settings_do_not_invalidate_the_best(rig: _Rig) -> None:
    """``fight_retention_seconds`` decides how long a row is shown, never what
    any reading means — it is deliberately absent from ``_measurement_rules``."""
    _long_fight(rig.tracker, 900)
    rig.tracker.configure(fight_retention_s=120.0)
    assert rig.tracker.best.total_damage == 900
    assert rig.stored("Genartik").total_damage == 900


# -- what is deliberately not persisted --------------------------------------------


def test_last_session_stays_in_memory(rig: _Rig) -> None:
    """Explicitly a within-session record, not a lifetime one."""
    _long_fight(rig.tracker, 900)
    rig.tracker.end_session()
    assert rig.tracker.last_session is not None
    assert not hasattr(rig.stored("Genartik"), "last_session")

    rig.switch_to("Vebanab")
    rig.switch_to("Genartik")
    # A restart drops it; a character switch does not resurrect one either.
    assert rig.tracker.last_session is not None  # the tracker's own session


# -- the user-visible reset ---------------------------------------------------------


def test_reset_best_is_bound_to_the_character_it_was_asked_for(rig: _Rig) -> None:
    """The confirmation dialog runs a modal loop; the driver keeps parsing.

    Switch characters while it is open and an unbound reset zeroes the best
    just restored for the INCOMING character and exports the zero over their
    profile — the lifetime record of someone the user was not looking at.
    """
    _long_fight(rig.tracker, 900, target="a gnoll")  # Genartik's record
    owner = rig.handler.best_owner()

    rig.switch_to("Vebanab")
    _long_fight(rig.tracker, 400, target="a rat")  # Vebanab's own record
    assert rig.tracker.best.total_damage == 400

    # The user clicks Yes, still thinking of Genartik.
    assert rig.handler.reset_best(owner) is False

    assert rig.tracker.best.total_damage == 400
    assert rig.stored("Vebanab").total_damage == 400
    assert rig.stored("Genartik").total_damage == 900


def test_reset_best_goes_ahead_when_the_character_has_not_changed(rig: _Rig) -> None:
    _long_fight(rig.tracker, 900)
    owner = rig.handler.best_owner()
    assert rig.handler.reset_best(owner) is True
    assert rig.tracker.best == PlayerDamage()
    assert rig.stored("Genartik").total_damage == 0


def test_the_owner_token_follows_the_character(rig: _Rig) -> None:
    first = rig.handler.best_owner()
    rig.switch_to("Vebanab")
    assert rig.handler.best_owner() != first
    rig.switch_to("Genartik")
    assert rig.handler.best_owner() == first


def test_with_no_profile_there_is_no_owner_to_protect(rig: _Rig) -> None:
    """Nothing is persisted without one, so the reset is unconditional."""
    rig.player.server = None
    assert rig.handler.best_owner() is None
    assert rig.handler.reset_best(None) is True


def test_reset_best_clears_the_record_and_persists_it(rig: _Rig) -> None:
    _long_fight(rig.tracker, 900)
    rig.tracker.reset_best()
    assert rig.tracker.best == PlayerDamage()
    assert rig.stored("Genartik").total_damage == 0


def test_reset_best_leaves_the_current_session_alone(rig: _Rig) -> None:
    """Narrower than ``reset_session_stats`` on purpose."""
    _long_fight(rig.tracker, 900)
    assert rig.tracker.current_session.total_damage == 900
    rig.tracker.reset_best()
    assert rig.tracker.current_session.total_damage == 900


def test_the_bogus_hit_guard_still_applies_to_a_restored_best(rig: _Rig) -> None:
    """32000+ readings are refused by ``PlayerDamage.observe`` either way."""
    _long_fight(rig.tracker, 900)
    _long_fight(rig.tracker, 40000, target="a giant")
    assert rig.tracker.best.highest_hit == 900
    assert rig.stored("Genartik").highest_hit == 900
