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
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


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
    """Windows System.Speech via a PowerShell one-liner."""

    def __init__(self, voice: str = "", volume: float = 1.0) -> None:
        self.voice = voice
        self.volume = volume
        super().__init__()

    def _command(self, text: str) -> list[str]:
        volume = int(_clamp(self.volume) * 100)
        escaped_text = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Volume = {volume}; "
        )
        if self.voice:
            escaped_voice = self.voice.replace("'", "''")
            script += f"$s.SelectVoice('{escaped_voice}'); "
        script += f"$s.Speak('{escaped_text}');"
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


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


def parse_say_voices(output: str) -> list[str]:
    voices: list[str] = []
    for line in output.splitlines():
        match = _SAY_VOICE_RE.match(line)
        if match:
            voices.append(match.group("name").strip())
    return voices


def list_voices() -> list[str]:
    """Available TTS voice names. macOS parses ``say -v ?``; others best-effort empty."""
    if sys.platform != "darwin":
        return []
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
