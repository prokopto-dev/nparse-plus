"""Runtime trigger model — port of EQTool's Trigger.cs / TriggerComponents.cs.

Search-text token semantics (Trigger.cs):

* ``{word}`` placeholders become named capture groups matching EQ names:
  ``(?P<word>[\\w` ]+)`` (letters/digits/underscore, backtick and spaces).
* ``{c}`` / ``{C}`` expand to the current character's name (regex-escaped) and
  are substituted *before* the generic placeholder pass; a trigger containing
  the token recompiles when the player changes.
* ``.NET`` named groups ``(?<name>...)`` in built-in patterns are translated to
  Python's ``(?P<name>...)`` (lookarounds are left untouched).
* When ``use_regex`` is false the search text is a case-insensitive substring.

Output-text expansion (``expand``):

* ``{c}`` / ``{C}`` -> player name (verbatim, not escaped).
* ``{COUNTER}`` (any casing) -> the trigger's current match tally. Handled
  before the placeholder pass because it also looks like a ``{word}`` token.
* Remaining ``{word}`` tokens -> values captured by the last successful match.
"""

from __future__ import annotations

import logging
import re
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

logger = logging.getLogger(__name__)

# EQTool converts each {word} placeholder into ``(?<word>[\w` ]+)``.
EQ_NAME_CHARS = r"[\w` ]+"

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_COUNTER_TOKEN_RE = re.compile(r"\{COUNTER\}", re.IGNORECASE)
# .NET named group syntax -> Python. Requires a word-start char after "<" so
# lookbehinds ``(?<=``/``(?<!`` are not rewritten.
_DOTNET_GROUP_RE = re.compile(r"\(\?<([A-Za-z_]\w*)>")


class TriggerAudioType(StrEnum):
    NONE = "None"
    TEXT_TO_SPEECH = "TextToSpeech"
    SOUND_FILE = "SoundFile"


class TimerType(StrEnum):
    NO_TIMER = "NoTimer"
    COUNT_DOWN = "CountDown"
    COUNT_UP = "CountUp"
    STOPWATCH = "Stopwatch"
    REPEATING_TIMER = "RepeatingTimer"


class TimerRestartBehavior(StrEnum):
    START_NEW_TIMER = "StartNewTimer"
    RESTART_TIMER = "RestartTimer"
    DO_NOTHING = "DoNothing"


class TriggerOutput(BaseModel):
    """One output block (Basic / Timer Ending / Timer Ended tabs)."""

    model_config = ConfigDict(extra="ignore")

    display_text_enabled: bool = False
    display_text: str = ""
    display_text_color: str = "Red"
    audio_type: TriggerAudioType = TriggerAudioType.NONE
    tts_text: str = ""
    interrupt_speech: bool = False
    sound_file: str = ""


class EndEarlyEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    search_text: str = ""
    use_regex: bool = False


class TriggerTimer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timer_type: TimerType = TimerType.COUNT_DOWN
    timer_name: str = ""
    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    milliseconds: int = 0
    # Redundant convenience value carried by builtin_triggers.json; used only
    # when the hours/minutes/seconds parts are absent.
    duration_seconds: float | None = None
    restart_behavior: TimerRestartBehavior = TimerRestartBehavior.START_NEW_TIMER
    bar_color: str = "MediumPurple"
    icon_name: str = "Feign Death"
    show_in_overlay: bool = False
    end_early_texts: list[EndEarlyEntry] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        """Timer duration in seconds (TriggerTimer.Duration)."""
        parts = self.hours * 3600 + self.minutes * 60 + self.seconds + self.milliseconds / 1000
        if parts <= 0 and self.duration_seconds:
            return float(self.duration_seconds)
        return float(parts)

    @property
    def is_enabled(self) -> bool:
        return self.timer_type != TimerType.NO_TIMER


class TriggerTimerEnding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    hours: int = 0
    minutes: int = 0
    seconds: int = 1
    output: TriggerOutput = Field(default_factory=TriggerOutput)

    @property
    def threshold(self) -> float:
        return float(self.hours * 3600 + self.minutes * 60 + self.seconds)


class TriggerTimerEnded(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    output: TriggerOutput = Field(default_factory=TriggerOutput)


class TriggerCounter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reset_enabled: bool = False
    hours: int = 0
    minutes: int = 0
    seconds: int = 0

    @property
    def reset_after(self) -> float:
        return float(self.hours * 3600 + self.minutes * 60 + self.seconds)


class Trigger(BaseModel):
    """A user or built-in trigger. Also the shape builtin_triggers.json loads into."""

    model_config = ConfigDict(extra="ignore")

    trigger_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trigger_enabled: bool = False
    trigger_name: str = ""
    folder_id: str | None = None
    built_in_folder_path: str | None = None
    is_built_in: bool = False
    built_in_id: str | None = None
    customized: bool = False
    built_in_folder: str = ""
    category: str = "Default"
    comments: str = ""
    # Zone gate: when set, the trigger only fires while the player is in this
    # zone (short key, case-insensitive). Empty/None means everywhere.
    zone: str | None = None
    search_text: str = ""
    # None means "not persisted" and defaults to regex mode (EffectiveUseRegex).
    use_regex: bool | None = None
    basic: TriggerOutput | None = None
    timer: TriggerTimer | None = None
    timer_ending: TriggerTimerEnding | None = None
    timer_ended: TriggerTimerEnded | None = None
    counter: TriggerCounter | None = None
    # Legacy pre-"Basic tab" output fields (Trigger.cs keeps them so old saved
    # triggers still work); effective_basic() folds them in when basic is None.
    display_text_enabled: bool = False
    display_text: str | None = None
    audio_text_enabled: bool = False
    audio_text: str | None = None

    # --- runtime state (JsonIgnore territory in C#) --------------------------
    _player_name: str = PrivateAttr(default="")
    _current_counter: int = PrivateAttr(default=0)
    _value_hash: dict[str, str] = PrivateAttr(default_factory=dict)
    _regex: re.Pattern[str] | None = PrivateAttr(default=None)
    _regex_key: tuple[str, str | None] | None = PrivateAttr(default=None)

    @property
    def player_name(self) -> str:
        return self._player_name

    @player_name.setter
    def player_name(self, value: str) -> None:
        value = value or ""
        if self._player_name != value:
            self._player_name = value
            # {c} regex invalidation falls out of the cache key; the counter
            # tally always restarts on a character change (Trigger.cs).
            self._current_counter = 0

    @property
    def current_counter(self) -> int:
        return self._current_counter

    @current_counter.setter
    def current_counter(self, value: int) -> None:
        self._current_counter = value

    @property
    def effective_use_regex(self) -> bool:
        return True if self.use_regex is None else self.use_regex

    @property
    def has_context_token(self) -> bool:
        """Whether the pattern contains the {c} macro (case-insensitive)."""
        return "{c}" in self.search_text.lower()

    def matches_zone(self, current_zone: str | None) -> bool:
        return not self.zone or self.zone.lower() == (current_zone or "").lower()

    def effective_basic(self) -> TriggerOutput:
        """Basic output, synthesized from the legacy fields for old triggers."""
        if self.basic is not None:
            return self.basic
        return TriggerOutput(
            display_text_enabled=self.display_text_enabled,
            display_text=self.display_text or "",
            audio_type=(
                TriggerAudioType.TEXT_TO_SPEECH
                if self.audio_text_enabled
                else TriggerAudioType.NONE
            ),
            tts_text=self.audio_text or "",
        )

    @property
    def trigger_regex(self) -> re.Pattern[str] | None:
        """Compiled pattern, cached until search text (or, for {c}, player) changes."""
        key = (self.search_text, self._player_name if self.has_context_token else None)
        if self._regex_key != key:
            self._regex = self._compile()
            self._regex_key = key
        return self._regex

    def _compile(self) -> re.Pattern[str] | None:
        if not self.search_text.strip():
            return None
        escaped_player = re.escape(self._player_name)
        converted = self.search_text.replace("{c}", escaped_player).replace("{C}", escaped_player)
        converted = _DOTNET_GROUP_RE.sub(r"(?P<\1>", converted)
        while match := _PLACEHOLDER_RE.search(converted):
            group = f"(?P<{match.group(1)}>{EQ_NAME_CHARS})"
            converted = converted[: match.start()] + group + converted[match.end() :]
        try:
            return re.compile(converted, re.IGNORECASE)
        except re.error:
            logger.warning("trigger %r pattern failed to compile: %r", self.trigger_name, converted)
            return None

    def matches(self, line: str) -> bool:
        """Test a log line, honoring the regex/plain-text setting.

        On a regex match the captured group values are saved for output
        expansion (Trigger.Matches + SaveNamedGroupValues).
        """
        if not line or not self.search_text.strip():
            return False
        if self.effective_use_regex:
            regex = self.trigger_regex
            if regex is None:
                return False
            match = regex.search(line)
            if match is not None:
                self._save_named_group_values(match)
                return True
            return False
        return self.search_text.lower() in line.lower()

    def _save_named_group_values(self, match: re.Match[str]) -> None:
        # C# walks Match.Groups, which includes "0" (the whole match) and the
        # numbered groups as well as the named ones. Unmatched groups are "".
        self._value_hash["0"] = match.group(0)
        for index, value in enumerate(match.groups(), start=1):
            self._value_hash[str(index)] = value or ""
        for name, value in match.groupdict().items():
            self._value_hash[name] = value or ""

    def expand(self, text: str | None) -> str:
        """Expand {c}/{COUNTER}/{word} tokens in an output text template."""
        if not text:
            return ""
        return self._expand_output_text(text)

    def _expand_output_text(self, text: str) -> str:
        rv = text.replace("{c}", self._player_name).replace("{C}", self._player_name)
        # {COUNTER} first: it also matches the generic {word} pattern but is a
        # macro, not a captured group.
        if _COUNTER_TOKEN_RE.search(rv):
            rv = _COUNTER_TOKEN_RE.sub(str(self._current_counter), rv)
        # Faithful port of the C# loop: placeholders are enumerated against a
        # snapshot while each known token replaces the FIRST remaining
        # placeholder in the working string (unknown tokens are skipped and
        # left in place).
        snapshot = rv
        for match in _PLACEHOLDER_RE.finditer(snapshot):
            group_name = match.group(1)
            if group_name in self._value_hash:
                value = self._value_hash[group_name]
                rv = _PLACEHOLDER_RE.sub(lambda _m, _v=value: _v, rv, count=1)
        return rv
