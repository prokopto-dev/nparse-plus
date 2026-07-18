"""Text-to-speech speakers — the Python analogue of EQTool's TextToSpeach service.

All real speakers shell out to a platform TTS binary from a single daemon
worker thread, so ``speak()`` never blocks the caller and utterances play one
at a time. The pending queue is capped: when more than ``MAX_PENDING`` texts
are waiting the oldest is dropped (stale alerts are worthless in EQ combat).

No Qt anywhere in this module.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceInfo:
    """A selectable TTS voice.

    ``id`` is the opaque value stored in ``settings.tts_voice`` and handed to
    the speaker as its ``voice`` argument. On Windows it is engine-prefixed
    (``sapi:<name>`` / ``winrt:<DisplayName>``) so the speaker knows which
    synthesis path to use; on macOS/espeak it is the bare voice name / ``-v``
    language code. Stdlib-only (no Qt) — the settings combo consumes these.
    """

    id: str
    label: str  # combo display text
    engine: str = ""  # "", "say", "sapi", "winrt", "espeak"


@runtime_checkable
class Speaker(Protocol):
    """Anything that can speak a line of text (ITextToSpeach.Say)."""

    def speak(self, text: str) -> None: ...


class NullSpeaker:
    """Silently swallows everything (headless / TTS unavailable / tests)."""

    def speak(self, text: str) -> None:
        return


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _no_window_creationflags() -> int:
    """``CREATE_NO_WINDOW`` on Windows, ``0`` everywhere else.

    nParse+ runs as a windowed/frozen GUI with no console, so each child
    ``powershell`` process would otherwise pop a fresh console window — once
    per utterance. Mirrors the flag the voice-enumeration path already sets.
    """
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


class SubprocessSpeaker:
    """Base: queue worker thread that runs one TTS subprocess at a time."""

    MAX_PENDING = 5

    def __init__(self) -> None:
        self._queue: deque[str] = deque()
        self._cv = threading.Condition()
        self._closed = False
        self._thread = threading.Thread(target=self._worker, daemon=True, name="tts-speaker")
        self._thread.start()

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        with self._cv:
            if self._closed:
                return
            while len(self._queue) >= self.MAX_PENDING:
                self._queue.popleft()  # drop-oldest
            self._queue.append(text)
            self._cv.notify()

    def close(self) -> None:
        """Stop the worker after any in-flight utterance finishes."""
        with self._cv:
            self._closed = True
            self._queue.clear()
            self._cv.notify()

    def _worker(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                text = self._queue.popleft()
            try:
                self._utter(text)
            except Exception:
                logger.exception("TTS utterance failed")

    def _utter(self, text: str) -> None:
        subprocess.run(
            self._command(text),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_no_window_creationflags(),
        )

    def _command(self, text: str) -> list[str]:
        raise NotImplementedError


class MacSaySpeaker(SubprocessSpeaker):
    """macOS ``/usr/bin/say``; volume via an Apple speech ``[[volm 0.X]]`` prefix."""

    SAY_PATH = "/usr/bin/say"

    def __init__(self, voice: str = "", volume: float = 1.0) -> None:
        self.voice = voice
        self.volume = volume
        super().__init__()

    def _command(self, text: str) -> list[str]:
        args = [self.SAY_PATH]
        if self.voice:
            args += ["-v", self.voice]
        args.append(f"[[volm {_clamp(self.volume):.2f}]] {text}")
        return args


# The task brief names this class "MacSaydSpeaker"; keep that spelling working.
MacSaydSpeaker = MacSaySpeaker


class WindowsSpeaker(SubprocessSpeaker):
    """Windows TTS via PowerShell.

    Two synthesis backends, chosen by the ``voice`` id (see ``VoiceInfo``):

    * empty or ``sapi:<name>`` -> classic ``System.Speech`` (the safe fallback
      and the only path CI can represent);
    * ``winrt:<DisplayName>`` -> the WinRT ``SpeechSynthesizer`` used for the
      modern "Natural" voices, synthesised to a temp .wav and played back.
    """

    def __init__(self, voice: str = "", volume: float = 1.0) -> None:
        self.voice = voice
        self.volume = volume
        super().__init__()

    def _command(self, text: str) -> list[str]:
        voice = self.voice or ""
        if voice.startswith("winrt:"):
            script = self._winrt_script(text, voice[len("winrt:") :])
        else:
            script = self._sapi_script(text, voice)
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]

    def _sapi_script(self, text: str, voice: str) -> str:
        """Classic System.Speech; ``voice`` may carry a ``sapi:`` prefix."""
        if voice.startswith("sapi:"):
            voice = voice[len("sapi:") :]
        volume = int(_clamp(self.volume) * 100)
        escaped_text = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Volume = {volume}; "
        )
        if voice:
            escaped_voice = voice.replace("'", "''")
            script += f"$s.SelectVoice('{escaped_voice}'); "
        script += f"$s.Speak('{escaped_text}');"
        return script

    def _winrt_script(self, text: str, display_name: str) -> str:
        """WinRT SpeechSynthesizer -> temp .wav -> SoundPlayer.PlaySync().

        Passed via ``-Command`` (not ``-EncodedCommand``) so the payload stays
        inspectable and unit-testable; single-quoted literals are escaped by
        doubling. If anything in the WinRT path throws (old OS, missing voice,
        stream failure) the catch block still speaks via System.Speech so an
        alert is never silently lost.
        """
        escaped_text = text.replace("'", "''")
        escaped_name = display_name.replace("'", "''")
        audio_volume = f"{_clamp(self.volume):.2f}"
        fallback_volume = int(_clamp(self.volume) * 100)
        return (
            "try { "
            "[void][Windows.Media.SpeechSynthesis.SpeechSynthesizer,"
            "Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]; "
            "$s = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer; "
            "$v = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices "
            f"| ? DisplayName -eq '{escaped_name}' | select -First 1; "
            "if ($v) { $s.Voice = $v }; "
            f"$s.Options.AudioVolume = {audio_volume}; "
            "$stream = [System.WindowsRuntimeSystemExtensions]::AsTask("
            f"$s.SynthesizeTextToStreamAsync('{escaped_text}'))"
            ".GetAwaiter().GetResult(); "
            "$ns = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream); "
            "$tmp = [System.IO.Path]::GetTempFileName(); "
            "$fs = [System.IO.File]::Create($tmp); "
            "$ns.CopyTo($fs); $fs.Close(); "
            "(New-Object System.Media.SoundPlayer $tmp).PlaySync(); "
            "Remove-Item $tmp -ErrorAction SilentlyContinue; "
            "} catch { "
            "Add-Type -AssemblyName System.Speech; "
            "$f = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$f.Volume = {fallback_volume}; "
            f"$f.Speak('{escaped_text}'); "
            "}"
        )


class EspeakSpeaker(SubprocessSpeaker):
    """Linux/other espeak-ng (or espeak) on PATH."""

    def __init__(self, voice: str = "", volume: float = 1.0, executable: str | None = None) -> None:
        self.voice = voice
        self.volume = volume
        self.executable = executable or find_espeak() or "espeak-ng"
        super().__init__()

    def _command(self, text: str) -> list[str]:
        # espeak amplitude is 0-200 with 100 the default; map volume 1.0 -> 100.
        args = [self.executable, "-a", str(int(_clamp(self.volume) * 100))]
        if self.voice:
            args += ["-v", self.voice]
        args.append(text)
        return args


def find_espeak() -> str | None:
    return shutil.which("espeak-ng") or shutil.which("espeak")


def default_speaker(voice: str = "", volume: float = 1.0) -> Speaker:
    """Best speaker for the current platform, or NullSpeaker when TTS is absent."""
    import os

    if sys.platform == "darwin":
        if os.path.exists(MacSaySpeaker.SAY_PATH):
            return MacSaySpeaker(voice=voice, volume=volume)
        return NullSpeaker()
    if sys.platform.startswith("win"):
        return WindowsSpeaker(voice=voice, volume=volume)
    if find_espeak():
        return EspeakSpeaker(voice=voice, volume=volume)
    return NullSpeaker()


# ``say -v ?`` lines look like:  "Bad News            en_US    # The light ..."
_SAY_VOICE_RE = re.compile(r"^(?P<name>.+?)\s{2,}(?P<locale>[a-zA-Z]{2,3}[_-][A-Za-z0-9]+)\s+#")


def parse_say_voices(output: str) -> list[VoiceInfo]:
    """macOS ``say -v ?`` -> VoiceInfo records (id == label == voice name)."""
    voices: list[VoiceInfo] = []
    for line in output.splitlines():
        match = _SAY_VOICE_RE.match(line)
        if match:
            name = match.group("name").strip()
            voices.append(VoiceInfo(id=name, label=name, engine="say"))
    return voices


def _list_say_voices() -> list[VoiceInfo]:
    try:
        result = subprocess.run(
            [MacSaySpeaker.SAY_PATH, "-v", "?"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_say_voices(result.stdout)


# One PowerShell invocation prints two tab-separated sections. The WinRT block
# is guarded so an OS without the projection still yields the SAPI voices.
_WINDOWS_VOICE_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() "
    "| ? {$_.Enabled} | % { 'SAPI' + [char]9 + $_.VoiceInfo.Name }; "
    "try { "
    "[void][Windows.Media.SpeechSynthesis.SpeechSynthesizer,"
    "Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]; "
    "[Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices "
    "| % { 'WINRT' + [char]9 + $_.DisplayName } "
    "} catch { }"
)


def _split_voice_tag(line: str, tag: str) -> str | None:
    """Return the name after ``<tag>`` + separator, tolerating a real tab or a
    literal ``\\t`` (single-quoted PowerShell would emit the latter)."""
    if not line.startswith(tag):
        return None
    rest = line[len(tag) :].lstrip()
    if rest.startswith("\\t"):
        rest = rest[2:]
    return rest.strip()


def parse_windows_voices(stdout: str) -> list[VoiceInfo]:
    """Parse the combined ``SAPI\\t...`` / ``WINRT\\t...`` sections.

    Deduped by display label, preferring the WinRT (Natural) voice.
    """
    records: list[VoiceInfo] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        name = _split_voice_tag(line, "SAPI")
        if name is not None:
            if name:
                records.append(VoiceInfo(id=f"sapi:{name}", label=name, engine="sapi"))
            continue
        name = _split_voice_tag(line, "WINRT")
        if name:
            label = name if "(" in name else f"{name} (Natural)"
            records.append(VoiceInfo(id=f"winrt:{name}", label=label, engine="winrt"))
    by_label: dict[str, VoiceInfo] = {}
    for rec in records:
        existing = by_label.get(rec.label)
        if existing is None or (rec.engine == "winrt" and existing.engine != "winrt"):
            by_label[rec.label] = rec
    return list(by_label.values())


def _list_windows_voices() -> list[VoiceInfo]:
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _WINDOWS_VOICE_SCRIPT,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_no_window_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_windows_voices(result.stdout)


def parse_espeak_voices(stdout: str) -> list[VoiceInfo]:
    """Parse ``espeak-ng --voices`` columns.

    Header: ``Pty Language Age/Gender VoiceName File Other Languages``. Column 2
    is the ``-v`` language code (the voice id); column 4 is the VoiceName.
    """
    voices: list[VoiceInfo] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Pty"):  # blank / header row
            continue
        parts = stripped.split()
        if len(parts) < 4:
            continue
        lang = parts[1]
        name = parts[3]
        voices.append(VoiceInfo(id=lang, label=f"{name} ({lang})", engine="espeak"))
    return voices


def _list_espeak_voices() -> list[VoiceInfo]:
    executable = find_espeak()
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "--voices"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_espeak_voices(result.stdout)


def list_voices() -> list[VoiceInfo]:
    """Available TTS voices for the current platform (empty when TTS is absent).

    macOS parses ``say -v ?``; Windows enumerates SAPI + WinRT via PowerShell;
    Linux/other parse ``espeak-ng --voices`` when the binary is on PATH.
    """
    if sys.platform == "darwin":
        return _list_say_voices()
    if sys.platform.startswith("win"):
        return _list_windows_voices()
    if find_espeak():
        return _list_espeak_voices()
    return []
