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
