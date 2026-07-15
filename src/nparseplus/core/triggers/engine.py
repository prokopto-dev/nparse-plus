"""TriggerEngine — port of EQTool's TriggerHandler + TriggerActionExecutor +
TriggerTimerManager, minus threads and WPF.

The engine subscribes to :class:`LineEvent` on the bus. Where EQTool runs a
250 ms DispatcherTimer, this port exposes ``tick(now)``: the host calls it
periodically (or tests call it directly) and the engine fires Timer Ending /
Timer Ended outputs, re-arms repeating timers, resets inactive counters and
clears expired overlay text.

Side effects go through small injected protocols (``Speaker`` / ``TimerSink``)
and bus events (``OverlayEvent`` / ``TimerBarEvent``), never Qt.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from nparseplus.core.bus import EventBus, Unsubscribe
from nparseplus.core.events import LineEvent, OverlayEvent, TimerBarEvent
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.model import (
    TimerRestartBehavior,
    TimerType,
    Trigger,
    TriggerAudioType,
    TriggerOutput,
)

logger = logging.getLogger(__name__)

DEFAULT_BAR_COLOR = "MediumPurple"
DEFAULT_TEXT_COLOR = "Red"
DEFAULT_ICON = "Feign Death"
# TriggerActionExecutor shows display text for 5 seconds before resetting it.
DISPLAY_TEXT_SECONDS = 5.0


class Speaker(Protocol):
    """Text-to-speech sink (structurally matched by nparseplus.audio speakers)."""

    def speak(self, text: str) -> None: ...


class TimerSink(Protocol):
    """Receives trigger timers (EQTool's SpellWindowViewModel role)."""

    def add_timer(
        self, name: str, seconds: int, color: str, icon: str, restart_behavior: str
    ) -> None: ...

    def cancel(self, name: str) -> None: ...


@dataclass
class _ActiveTimer:
    trigger: Trigger
    name: str
    end_time: datetime
    duration: float  # seconds
    ending_fired: bool = False


@dataclass
class _ActiveCounter:
    trigger: Trigger
    last_match: datetime
    reset_after: float  # seconds


@dataclass
class _PendingOverlayReset:
    due: datetime
    text: str
    foreground: str


def _now() -> datetime:
    # Naive local time — the whole pipeline (log timestamps, driver ticks,
    # TimersService) uses naive local datetimes; mixing in aware values makes
    # comparisons raise TypeError.
    return datetime.now()


@dataclass
class TriggerEngine:
    bus: EventBus
    player: ActivePlayer
    speaker: Speaker
    timers: TimerSink
    clock: Callable[[], datetime] = _now
    sound_player: Callable[[str], None] | None = None

    _triggers: list[Trigger] = field(default_factory=list, init=False)
    _active_timers: list[_ActiveTimer] = field(default_factory=list, init=False)
    _active_counters: list[_ActiveCounter] = field(default_factory=list, init=False)
    _pending_resets: list[_PendingOverlayReset] = field(default_factory=list, init=False)
    _unsubscribe: Unsubscribe | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._unsubscribe = self.bus.subscribe(LineEvent, self._on_line)

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    # --- trigger set ---------------------------------------------------------

    @property
    def triggers(self) -> list[Trigger]:
        return self._triggers

    def set_triggers(self, triggers: list[Trigger]) -> None:
        self._triggers = list(triggers)

    def set_player(self, name: str) -> None:
        """Player changed: recompiles {c} triggers and restarts their counters."""
        for trigger in self._triggers:
            trigger.player_name = name

    # --- line handling (TriggerHandler.LogEvents_LineEvent) -------------------

    def _on_line(self, event: LineEvent) -> None:
        line = event.line
        # give active timers a chance to end early on this line
        self._check_end_early(line)

        current_zone = self.player.zone if self.player else ""
        player_name = self.player.name if self.player else ""
        for trigger in [t for t in self._triggers if t.trigger_enabled]:
            # skip triggers restricted to a zone the player isn't currently in
            if not trigger.matches_zone(current_zone):
                continue
            trigger.player_name = player_name
            if not trigger.matches(line):
                continue

            # bump the {COUNTER} tally before expanding any output
            trigger.current_counter += 1

            self._execute(trigger.effective_basic(), trigger.expand)

            if trigger.timer is not None and trigger.timer.is_enabled:
                self._handle_timer_match(trigger)

            if trigger.counter is not None and trigger.counter.reset_enabled:
                self._handle_counter_match(trigger)

            # a line is consumed by the first matching trigger
            return

    # --- output execution (TriggerActionExecutor.Execute) ---------------------

    def _execute(self, output: TriggerOutput | None, expand: Callable[[str], str]) -> None:
        if output is None:
            return

        if output.display_text_enabled and output.display_text.strip():
            text = expand(output.display_text)
            color = output.display_text_color or DEFAULT_TEXT_COLOR
            self.bus.publish(OverlayEvent(text=text, foreground=color, reset=False))
            self._pending_resets.append(
                _PendingOverlayReset(
                    due=self.clock() + _seconds(DISPLAY_TEXT_SECONDS),
                    text=text,
                    foreground=color,
                )
            )

        if output.audio_type == TriggerAudioType.TEXT_TO_SPEECH and output.tts_text.strip():
            self.speaker.speak(expand(output.tts_text))
        elif (
            output.audio_type == TriggerAudioType.SOUND_FILE
            and output.sound_file.strip()
            and self.sound_player is not None
        ):
            self.sound_player(output.sound_file)

    # --- timers (TriggerTimerManager) -----------------------------------------

    def _handle_timer_match(self, trigger: Trigger) -> None:
        timer = trigger.timer
        if timer is None or not timer.is_enabled:
            return
        duration = timer.duration
        if duration <= 0:
            return
        name = trigger.expand(timer.timer_name.strip() or trigger.trigger_name)
        now = self.clock()

        existing = next((t for t in self._active_timers if t.name.lower() == name.lower()), None)
        if timer.restart_behavior == TimerRestartBehavior.DO_NOTHING and existing is not None:
            return
        if timer.restart_behavior == TimerRestartBehavior.RESTART_TIMER and existing is not None:
            existing.end_time = now + _seconds(duration)
            existing.duration = duration
            existing.ending_fired = False
            self._emit_timer(trigger, name, duration)
            return

        self._active_timers.append(
            _ActiveTimer(
                trigger=trigger, name=name, end_time=now + _seconds(duration), duration=duration
            )
        )
        self._emit_timer(trigger, name, duration)

    def _emit_timer(self, trigger: Trigger, name: str, duration: float) -> None:
        timer = trigger.timer
        if timer is None:
            return
        seconds = math.ceil(duration)
        self.timers.add_timer(
            name,
            seconds,
            timer.bar_color or DEFAULT_BAR_COLOR,
            timer.icon_name.strip() or DEFAULT_ICON,
            str(timer.restart_behavior),
        )
        # "Show in overlay" mirrors the countdown as a bar in the overlay window.
        if timer.show_in_overlay:
            self.bus.publish(
                TimerBarEvent(
                    name=name,
                    total_seconds=seconds,
                    bar_color=timer.bar_color or DEFAULT_BAR_COLOR,
                )
            )

    def _check_end_early(self, line: str) -> None:
        """Cancel active timers whose trigger has a matching end-early text."""
        if not line:
            return
        to_cancel: list[_ActiveTimer] = []
        for active in self._active_timers:
            timer = active.trigger.timer
            entries = timer.end_early_texts if timer is not None else []
            for entry in entries:
                if not entry.search_text.strip():
                    continue
                hit = (
                    _safe_regex_match(entry.search_text, line)
                    if entry.use_regex
                    else entry.search_text.lower() in line.lower()
                )
                if hit:
                    to_cancel.append(active)
                    break
        for active in to_cancel:
            self._active_timers.remove(active)
            self.timers.cancel(active.name)

    def _handle_counter_match(self, trigger: Trigger) -> None:
        counter = trigger.counter
        if counter is None or not counter.reset_enabled or counter.reset_after <= 0:
            return
        existing = next((c for c in self._active_counters if c.trigger is trigger), None)
        if existing is None:
            self._active_counters.append(
                _ActiveCounter(
                    trigger=trigger, last_match=self.clock(), reset_after=counter.reset_after
                )
            )
        else:
            existing.last_match = self.clock()
            existing.reset_after = counter.reset_after

    # --- periodic work (TriggerTimerManager.Tick) ------------------------------

    def tick(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        ending_to_fire: list[_ActiveTimer] = []
        ended_to_fire: list[_ActiveTimer] = []

        for active in list(self._active_timers):
            remaining = (active.end_time - now).total_seconds()
            trigger = active.trigger

            ending = trigger.timer_ending
            if (
                not active.ending_fired
                and ending is not None
                and ending.enabled
                and ending.threshold > 0
                and 0 < remaining <= ending.threshold
            ):
                active.ending_fired = True
                ending_to_fire.append(active)

            if remaining <= 0:
                ended = trigger.timer_ended
                if ended is not None and ended.enabled:
                    ended_to_fire.append(active)

                timer = trigger.timer
                if timer is not None and timer.timer_type == TimerType.REPEATING_TIMER:
                    # re-arm a repeating timer
                    active.end_time = now + _seconds(active.duration)
                    active.ending_fired = False
                    self._emit_timer(trigger, active.name, active.duration)
                else:
                    self._active_timers.remove(active)

        for counter in list(self._active_counters):
            if (
                counter.reset_after > 0
                and (now - counter.last_match).total_seconds() >= counter.reset_after
            ):
                # zero the {COUNTER} tally so it restarts on the next match
                counter.trigger.current_counter = 0
                self._active_counters.remove(counter)

        for pending in list(self._pending_resets):
            if now >= pending.due:
                self.bus.publish(
                    OverlayEvent(text=pending.text, foreground=pending.foreground, reset=True)
                )
                self._pending_resets.remove(pending)

        for active in ending_to_fire:
            assert active.trigger.timer_ending is not None
            self._execute(active.trigger.timer_ending.output, active.trigger.expand)
        for active in ended_to_fire:
            assert active.trigger.timer_ended is not None
            self._execute(active.trigger.timer_ended.output, active.trigger.expand)


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=value)


def _safe_regex_match(pattern: str, line: str) -> bool:
    try:
        return re.search(pattern, line, re.IGNORECASE) is not None
    except re.error:
        return False
