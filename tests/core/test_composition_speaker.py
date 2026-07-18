"""Backend live TTS-voice swap.

``build_backend`` wraps the speaker in one ``_SwappableSpeaker`` holder that the
trigger engine AND every audio handler hold by reference, so replacing its
delegate (``Backend.set_speaker`` / ``rebuild_speaker``) changes the voice for
all of them without rebuilding the backend or restarting the app.
"""

from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings


class RecordingSpeaker:
    """A Speaker (plus SubprocessSpeaker-style ``close``) that records calls."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.said: list[str] = []
        self.closed = False

    def speak(self, text: str) -> None:
        self.said.append(text)

    def close(self) -> None:
        self.closed = True


def test_build_backend_shares_one_holder() -> None:
    first = RecordingSpeaker("first")
    backend = build_backend(Settings(), speaker=first)
    # The engine speaks through the very holder the backend exposes.
    assert backend.trigger_engine.speaker is backend.speaker
    backend.speaker.speak("alpha")
    assert first.said == ["alpha"]


def test_audio_handlers_all_hold_the_holder() -> None:
    backend = build_backend(Settings(), speaker=RecordingSpeaker("first"))
    with_speaker = [obj for obj in backend._retained if getattr(obj, "speaker", None) is not None]
    assert with_speaker, "expected retained audio handlers that received a speaker"
    assert all(obj.speaker is backend.speaker for obj in with_speaker)


def test_set_speaker_swaps_all_references_and_closes_old() -> None:
    first = RecordingSpeaker("first")
    backend = build_backend(Settings(), speaker=first)
    second = RecordingSpeaker("second")

    backend.set_speaker(second)

    assert first.closed is True  # the previous delegate is closed on swap
    # Anything holding the holder now routes to the new delegate.
    backend.trigger_engine.speaker.speak("beta")
    assert first.said == []
    assert second.said == ["beta"]


def test_rebuild_speaker_builds_from_current_settings(monkeypatch) -> None:
    import nparseplus.audio.tts as tts

    first = RecordingSpeaker("first")
    settings = Settings()
    backend = build_backend(settings, speaker=first)

    built: list[tuple[str, float]] = []
    rebuilt = RecordingSpeaker("rebuilt")

    def _fake_default_speaker(voice: str = "", volume: float = 1.0) -> RecordingSpeaker:
        built.append((voice, volume))
        return rebuilt

    # composition imports default_speaker lazily from the tts module.
    monkeypatch.setattr(tts, "default_speaker", _fake_default_speaker)
    settings.general.tts_voice = "winrt:New Voice"
    settings.general.global_audio_volume = 40

    backend.rebuild_speaker()

    assert built == [("winrt:New Voice", 0.4)]  # id + 0-1 volume from settings
    assert first.closed is True
    backend.speaker.speak("gamma")
    assert rebuilt.said == ["gamma"]
