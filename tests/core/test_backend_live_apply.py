"""The Qt-free half of the settings window's live-apply seams.

``Backend.apply_dps_settings`` established the shape: the settings window
mutates the settings tree and calls a Backend method that pushes the change
onto whatever the app built at launch. These are the two that joined it —
overlay durations (#67) and the sharing mode (#69).
"""

from __future__ import annotations

from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings


class StubSpeaker:
    def speak(self, *_a, **_k) -> None: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...


def backend_for(mode: str = "off"):
    settings = Settings()
    settings.sharing.mode = mode
    return build_backend(settings, speaker=StubSpeaker())


def test_apply_overlay_timings_retimes_the_running_trigger_engine() -> None:
    backend = backend_for()
    assert backend.trigger_engine.display_text_seconds == 4.0  # the default
    backend.settings.general.overlay_text_seconds = 12.0
    backend.apply_overlay_timings()
    assert backend.trigger_engine.display_text_seconds == 12.0


def test_apply_sharing_mode_off_stops_the_client_and_forgets_it() -> None:
    backend = backend_for("pigparse")
    client = backend.sharing_client
    assert client is not None  # built because the mode asked for it

    backend.settings.sharing.mode = "off"
    backend.apply_sharing_mode()

    assert backend.sharing_client is None  # so stop() cannot stop it twice
    assert backend.sharing.status == "off"
    # The coordinator dropped it too, so nothing outbound can reach it.
    backend.stop()


def test_apply_sharing_mode_does_not_build_a_client_when_turned_on() -> None:
    """Off -> on stays a restart (the L half of #69): ten handlers captured
    the REST client at construction, so there is nowhere to put a new one.
    This pins that the seam does not half-do it."""
    backend = backend_for("off")
    backend.settings.sharing.mode = "pigparse"
    backend.apply_sharing_mode()
    assert backend.sharing_client is None


def test_apply_dps_settings_pushes_the_new_knobs() -> None:
    """The whole DPS page reaches the tracker the app built at launch.

    The page is the only place these can be changed and the tracker outlives
    every settings window, so a knob the seam forgets is a knob that silently
    needs a restart.
    """
    backend = backend_for()
    assert backend.fights.damage_sources == "melee+mine"  # the built default

    backend.settings.dps.damage_sources = "all"
    backend.settings.dps.spell_credit_window_seconds = 5.0
    backend.settings.dps.count_pet_damage = True
    backend.settings.dps.trailing_window_seconds = 6.0
    backend.apply_dps_settings()

    assert backend.fights.damage_sources == "all"
    assert backend.fights.spell_credit_window_s == 5.0
    assert backend.fights.count_pet_damage is True
    assert backend.fights.trailing_window_s == 6.0


def test_the_dps_handler_follows_the_pet_the_app_built() -> None:
    """composition wires ONE PlayerPet; the DPS tracker must see that one."""
    backend = backend_for()
    backend.player_pet.set_name("Vexer")
    assert backend.fights.pet_name == "Vexer"
    backend.player_pet.reset()
    assert backend.fights.pet_name == ""
