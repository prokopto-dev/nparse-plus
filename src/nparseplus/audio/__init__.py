"""Audio layer: text-to-speech speakers (no Qt)."""

from nparseplus.audio.tts import (
    EspeakSpeaker,
    MacSaydSpeaker,
    MacSaySpeaker,
    NullSpeaker,
    Speaker,
    SubprocessSpeaker,
    WindowsSpeaker,
    default_speaker,
    list_voices,
)

__all__ = [
    "EspeakSpeaker",
    "MacSaySpeaker",
    "MacSaydSpeaker",
    "NullSpeaker",
    "Speaker",
    "SubprocessSpeaker",
    "WindowsSpeaker",
    "default_speaker",
    "list_voices",
]
