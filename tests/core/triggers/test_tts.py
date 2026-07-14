"""Speaker implementations — command construction and queue behavior only.

No test here ever spawns a real TTS process: command building is tested via
``_command`` and the queue via a subclass whose ``_utter`` records instead of
shelling out.
"""

import threading
import time

from nparseplus.audio.tts import (
    EspeakSpeaker,
    MacSaydSpeaker,
    MacSaySpeaker,
    NullSpeaker,
    Speaker,
    SubprocessSpeaker,
    WindowsSpeaker,
    default_speaker,
    parse_say_voices,
)


def test_null_speaker_is_a_speaker_and_does_nothing() -> None:
    speaker = NullSpeaker()
    assert isinstance(speaker, Speaker)
    speaker.speak("anything")  # no error, no side effects


def test_default_speaker_returns_a_speaker() -> None:
    speaker = default_speaker(volume=0.5)
    assert isinstance(speaker, Speaker)
    if isinstance(speaker, SubprocessSpeaker):
        speaker.close()


class RecordingSpeaker(SubprocessSpeaker):
    """Records utterances; the first one blocks until released."""

    def __init__(self) -> None:
        self.uttered: list[str] = []
        self.first_started = threading.Event()
        self.release = threading.Event()
        self._first = True
        super().__init__()

    def _command(self, text: str) -> list[str]:
        return ["true", text]

    def _utter(self, text: str) -> None:
        self.uttered.append(text)
        if self._first:
            self._first = False
            self.first_started.set()
            self.release.wait(timeout=5)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_queue_drops_oldest_beyond_five_pending() -> None:
    speaker = RecordingSpeaker()
    try:
        speaker.speak("blocker")
        assert speaker.first_started.wait(timeout=5)
        # worker is busy with "blocker"; enqueue 7 — the first two must drop
        for i in range(7):
            speaker.speak(f"msg{i}")
        speaker.release.set()
        assert wait_until(lambda: len(speaker.uttered) == 6)
        assert speaker.uttered == ["blocker", "msg2", "msg3", "msg4", "msg5", "msg6"]
    finally:
        speaker.release.set()
        speaker.close()


def test_blank_text_is_not_enqueued() -> None:
    speaker = RecordingSpeaker()
    try:
        speaker.speak("")
        speaker.speak("   ")
        speaker.speak("real")
        assert wait_until(lambda: speaker.uttered == ["real"])
    finally:
        speaker.release.set()
        speaker.close()


def test_mac_say_command_includes_voice_and_volume_prefix() -> None:
    speaker = MacSaySpeaker(voice="Alex", volume=0.4)
    try:
        cmd = speaker._command("Fear is open")
        assert cmd[0] == "/usr/bin/say"
        assert cmd[1:3] == ["-v", "Alex"]
        assert cmd[3] == "[[volm 0.40]] Fear is open"

        plain = MacSaySpeaker()
        try:
            assert plain._command("hi") == ["/usr/bin/say", "[[volm 1.00]] hi"]
        finally:
            plain.close()
        assert MacSaydSpeaker is MacSaySpeaker  # spec-name alias
    finally:
        speaker.close()


def test_windows_command_is_a_powershell_one_liner() -> None:
    speaker = WindowsSpeaker(voice="Microsoft Zira", volume=0.5)
    try:
        cmd = speaker._command("it's dead")
        assert cmd[0] == "powershell"
        script = cmd[-1]
        assert "System.Speech" in script
        assert "$s.Volume = 50" in script
        assert "SelectVoice('Microsoft Zira')" in script
        assert "$s.Speak('it''s dead');" in script  # quote-escaped
    finally:
        speaker.close()


def test_espeak_command_maps_volume_to_amplitude() -> None:
    speaker = EspeakSpeaker(voice="en-us", volume=0.5, executable="espeak-ng")
    try:
        cmd = speaker._command("hello")
        assert cmd == ["espeak-ng", "-a", "50", "-v", "en-us", "hello"]
    finally:
        speaker.close()


def test_parse_say_voices() -> None:
    output = (
        "Alex                en_US    # Most people recognize me by my voice.\n"
        "Bad News            en_US    # The light you see at the end of the tunnel...\n"
        "Ellen               nl_BE    # Hallo, mijn naam is Ellen.\n"
        "not a voice line\n"
    )
    assert parse_say_voices(output) == ["Alex", "Bad News", "Ellen"]
    assert parse_say_voices("") == []
