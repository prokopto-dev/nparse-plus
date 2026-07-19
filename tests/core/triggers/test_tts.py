"""Speaker implementations — command construction and queue behavior only.

No test here ever spawns a real TTS process: command building is tested via
``_command`` and the queue via a subclass whose ``_utter`` records instead of
shelling out.
"""

import sys
import threading
import time

import nparseplus.audio.tts as tts
from nparseplus.audio.tts import (
    EspeakSpeaker,
    MacSaydSpeaker,
    MacSaySpeaker,
    NullSpeaker,
    Speaker,
    SubprocessSpeaker,
    VoiceInfo,
    WindowsSpeaker,
    default_speaker,
    parse_espeak_voices,
    parse_say_voices,
    parse_windows_voices,
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


def test_windows_command_empty_and_sapi_prefix_use_system_speech() -> None:
    # Empty voice -> System.Speech, no SelectVoice.
    blank = WindowsSpeaker(voice="", volume=0.5)
    try:
        script = blank._command("hi")[-1]
        assert "System.Speech" in script
        assert "SelectVoice" not in script
        assert "SynthesizeTextToStream" not in script
    finally:
        blank.close()

    # sapi: prefix -> System.Speech with the prefix stripped before SelectVoice.
    sapi = WindowsSpeaker(voice="sapi:David", volume=0.5)
    try:
        script = sapi._command("hi")[-1]
        assert "System.Speech" in script
        assert "SelectVoice('David')" in script
        assert "sapi:" not in script
    finally:
        sapi.close()


def test_windows_command_winrt_prefix_uses_winrt_synthesis() -> None:
    speaker = WindowsSpeaker(voice="winrt:Microsoft Aria (Natural)", volume=0.5)
    try:
        cmd = speaker._command("it's dead")
        assert cmd[0] == "powershell"
        script = cmd[-1]
        # WinRT branch: matches the DisplayName and synthesises to a stream.
        assert "Microsoft Aria (Natural)" in script
        assert "SynthesizeTextToStream" in script
        assert "SpeechSynthesis.SpeechSynthesizer" in script
        assert "$s.Options.AudioVolume = 0.50" in script
        assert "it''s dead" in script  # quote-escaped text
        # Robustness fallback to System.Speech is present.
        assert "System.Speech" in script
    finally:
        speaker.close()


def test_no_window_creationflags_set_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(tts.sys, "platform", "win32")
    monkeypatch.setattr(tts.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert tts._no_window_creationflags() == 0x08000000


def test_no_window_creationflags_zero_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(tts.sys, "platform", "linux")
    assert tts._no_window_creationflags() == 0


def test_utter_suppresses_console_window(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(tts.sys, "platform", "win32")
    monkeypatch.setattr(tts.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    class _FakeProc:
        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(tts.subprocess, "Popen", fake_popen)
    speaker = WindowsSpeaker(voice="", volume=1.0)
    try:
        speaker._utter("hi")
    finally:
        speaker.close()
    assert captured["kwargs"]["creationflags"] == 0x08000000


def test_interrupt_clears_pending_queue() -> None:
    speaker = RecordingSpeaker()
    try:
        speaker.speak("blocker")
        assert speaker.first_started.wait(timeout=5)
        # worker is busy on "blocker"; queue three more, then interrupt.
        for i in range(3):
            speaker.speak(f"msg{i}")
        speaker.interrupt()  # drop the three still queued
        speaker.release.set()  # let the in-flight "blocker" finish
        assert wait_until(lambda: speaker.uttered == ["blocker"])
        time.sleep(0.05)  # prove nothing queued survived the interrupt
        assert speaker.uttered == ["blocker"]
    finally:
        speaker.release.set()
        speaker.close()


def test_interrupt_terminates_the_in_flight_utterance() -> None:
    class SleepSpeaker(SubprocessSpeaker):
        # A real (cross-platform) long-running child so interrupt() must kill it.
        def _command(self, text: str) -> list[str]:
            return [sys.executable, "-c", "import time; time.sleep(30)"]

    speaker = SleepSpeaker()
    proc = None
    try:
        speaker.speak("go")
        assert wait_until(lambda: speaker._current is not None)
        proc = speaker._current
        assert proc.poll() is None  # child is running
        speaker.interrupt()
        assert wait_until(lambda: proc.poll() is not None)  # terminated promptly
    finally:
        if proc is not None:
            proc.kill()
        speaker.close()


def test_parse_windows_voices_dedupes_preferring_winrt() -> None:
    stdout = (
        "SAPI\tMicrosoft David Desktop\n"
        "SAPI\tMicrosoft Zira Desktop\n"
        "SAPI\tShared Voice (Natural)\n"
        "WINRT\tShared Voice (Natural)\n"
        "WINRT\tMicrosoft Aria\n"
        "\n"
    )
    voices = parse_windows_voices(stdout)
    by_id = {v.id: v for v in voices}

    david = by_id["sapi:Microsoft David Desktop"]
    assert david.label == "Microsoft David Desktop"
    assert david.engine == "sapi"

    aria = by_id["winrt:Microsoft Aria"]
    assert aria.label == "Microsoft Aria (Natural)"  # (Natural) appended
    assert aria.engine == "winrt"

    # "Shared Voice (Natural)" appears as both SAPI and WINRT with the same
    # label; the WinRT record wins and the SAPI one is dropped.
    shared = [v for v in voices if v.label == "Shared Voice (Natural)"]
    assert len(shared) == 1
    assert shared[0].engine == "winrt"
    assert shared[0].id == "winrt:Shared Voice (Natural)"


def test_parse_windows_voices_tolerates_literal_backslash_t() -> None:
    # Single-quoted PowerShell emits a literal "\t", not a real tab.
    voices = parse_windows_voices("SAPI\\tMicrosoft David\nWINRT\\tMicrosoft Aria\n")
    assert voices == [
        VoiceInfo(id="sapi:Microsoft David", label="Microsoft David", engine="sapi"),
        VoiceInfo(id="winrt:Microsoft Aria", label="Microsoft Aria (Natural)", engine="winrt"),
    ]


def test_parse_espeak_voices() -> None:
    stdout = (
        "Pty Language       Age/Gender VoiceName          File                 Other Languages\n"
        " 5  af              --/M      Afrikaans           gmw/af\n"
        " 5  en-us           --/M      English             gmw/en-US\n"
        " 5  de              --/M      German              gmw/de\n"
        "\n"
    )
    voices = parse_espeak_voices(stdout)
    assert voices == [
        VoiceInfo(id="af", label="Afrikaans (af)", engine="espeak"),
        VoiceInfo(id="en-us", label="English (en-us)", engine="espeak"),
        VoiceInfo(id="de", label="German (de)", engine="espeak"),
    ]
    assert parse_espeak_voices("") == []


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
    assert parse_say_voices(output) == [
        VoiceInfo(id="Alex", label="Alex", engine="say"),
        VoiceInfo(id="Bad News", label="Bad News", engine="say"),
        VoiceInfo(id="Ellen", label="Ellen", engine="say"),
    ]
    assert parse_say_voices("") == []


def test_say_voice_id_round_trips_through_mac_speaker() -> None:
    # A say VoiceInfo.id is a bare name -> passed straight through as `voice`.
    voice = VoiceInfo(id="Alex", label="Alex", engine="say")
    speaker = MacSaySpeaker(voice=voice.id, volume=1.0)
    try:
        assert speaker._command("hi")[1:3] == ["-v", "Alex"]
    finally:
        speaker.close()
