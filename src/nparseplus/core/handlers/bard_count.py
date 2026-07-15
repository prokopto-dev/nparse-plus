"""BardCountHandler — bard swarm/AOE hit-and-resist tallies.

Port of EQTool's Services/Handlers/BardCountHandler.cs: per-target AOE
messages that land within 500 ms of each other (winces, "bound by silver
strands of music"/"bound in chords of music", and resist lines for the bard
songs that need them) are aggregated into one session. When a session goes
quiet its summary ("N Total | N Hits | N Resists") is spoken, overlaid, and
published as a System CommsEvent.

Divergences from the C#:
- The C# finalizes sessions with background tasks after a real 500 ms; this
  port finalizes lazily from log-line timestamps (on the next event outside
  the window, or via ``flush``), which keeps the handler deterministic and
  replay-exact.
- When a ``TimersService`` is supplied, each hit/resist also bumps a
  per-spell CounterRow so the tallies survive in the timer window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import (
    CommsEvent,
    LineEvent,
    OverlayEvent,
    ResistSpellEvent,
    YouBeginCastingEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.counters import CounterLists, load_counter_lists
from nparseplus.core.timers import YOU_GROUP, CounterRow, TimersService
from nparseplus.core.triggers.engine import Speaker

TRACK_WINDOW_MS = 500

_RESIST_TARGET_RE = re.compile(
    r"^Your target resisted(?: the)? (?P<spell>.+?)(?: spell)?\.$", re.IGNORECASE
)
_RESIST_YOU_RE = re.compile(r"^You resist(?: the)? (?P<spell>.+?)(?: spell)?!?$", re.IGNORECASE)
_WINCE_RE = re.compile(r"\bwinces\.$", re.IGNORECASE)
_HIT_SUBSTRINGS = ("is bound by silver strands of music", "is bound in chords of music")

# Map grave accents and curly quotes (U+2018/19/1C/1D) to plain ASCII quotes.
_QUOTE_TRANSLATION = str.maketrans(
    {"`": "'", chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'}
)


def _normalize_spell_name(name: str | None) -> str:
    if name is None or not name.strip():
        return ""
    normalized = name.strip().translate(_QUOTE_TRANSLATION)
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")
    return normalized


@dataclass
class _Session:
    spell_name: str  # "" while anonymous
    start_time: datetime
    last_event_time: datetime
    hits: int = 0
    resists: int = 0


class BardCountHandler(BaseHandler):
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        speaker: Speaker | None = None,
        timers: TimersService | None = None,
        counter_lists: CounterLists | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.speaker = speaker
        self.timers = timers
        self.lists = counter_lists if counter_lists is not None else load_counter_lists()
        self._sessions: list[_Session] = []
        # Mirrors ActivePlayer.UserCastingSpell for GetActiveSpellName().
        self._casting_spell_name = ""
        bus.subscribe(LineEvent, self._on_line)
        bus.subscribe(ResistSpellEvent, self._on_resist)
        bus.subscribe(YouBeginCastingEvent, self._on_begin_casting)

    # -- event intake -----------------------------------------------------------

    def _on_begin_casting(self, event: YouBeginCastingEvent) -> None:
        self._casting_spell_name = event.spell.name

    def _is_bard_resist_spell(self, normalized: str) -> bool:
        return any(
            _normalize_spell_name(name).casefold() == normalized.casefold()
            for name in self.lists.bard_spells_that_need_resists
        )

    def _needs_counts(self, normalized: str) -> bool:
        return any(
            _normalize_spell_name(name).casefold() == normalized.casefold()
            for name in self.lists.spells_that_need_counts
        )

    def _on_line(self, event: LineEvent) -> None:
        self._finalize_expired(event.timestamp)
        message = event.line
        for pattern in (_RESIST_TARGET_RE, _RESIST_YOU_RE):
            match = pattern.match(message)
            if match:
                spell_name = _normalize_spell_name(match.group("spell"))
                if self._is_bard_resist_spell(spell_name):
                    self._attach(event.timestamp, spell_name, is_resist=True, force=True)
                    return

        lowered = message.lower()
        if any(token in lowered for token in _HIT_SUBSTRINGS) or _WINCE_RE.search(message):
            self._attach(
                event.timestamp,
                _normalize_spell_name(self._casting_spell_name),
                hit_only=True,
            )

    def _on_resist(self, event: ResistSpellEvent) -> None:
        self._finalize_expired(event.timestamp)
        spell_name = _normalize_spell_name(event.spell.name)
        if self._is_bard_resist_spell(spell_name):
            self._attach(event.timestamp, spell_name, is_resist=True, force=True)

    # -- session bookkeeping -----------------------------------------------------

    def _in_window(self, session: _Session, timestamp: datetime) -> bool:
        delta_ms = abs((timestamp - session.last_event_time).total_seconds() * 1000.0)
        return delta_ms <= TRACK_WINDOW_MS

    def _bump(
        self, session: _Session, timestamp: datetime, hit_only: bool, is_resist: bool
    ) -> None:
        if hit_only:
            session.hits += 1
        if is_resist:
            session.resists += 1
        session.last_event_time = timestamp
        if self.timers is not None:
            label = session.spell_name or "AE"
            kind = "Resists" if is_resist else "Hits"
            self.timers.add_counter(
                CounterRow(name=f"{label} {kind}", group=YOU_GROUP, updated_at=timestamp)
            )

    def _attach(
        self,
        timestamp: datetime,
        spell_name: str,
        hit_only: bool = False,
        is_resist: bool = False,
        force: bool = False,
    ) -> None:
        normalized = _normalize_spell_name(spell_name)
        if normalized and (
            force or self._needs_counts(normalized) or self._is_bard_resist_spell(normalized)
        ):
            named = [
                s
                for s in self._sessions
                if s.spell_name.casefold() == normalized.casefold()
                and self._in_window(s, timestamp)
            ]
            if named:
                self._bump(
                    max(named, key=lambda s: s.last_event_time), timestamp, hit_only, is_resist
                )
                return
            anonymous = [
                s for s in self._sessions if not s.spell_name and self._in_window(s, timestamp)
            ]
            if anonymous:
                session = max(anonymous, key=lambda s: s.last_event_time)
                session.spell_name = normalized
                self._bump(session, timestamp, hit_only, is_resist)
                return
            session = _Session(
                spell_name=normalized, start_time=timestamp, last_event_time=timestamp
            )
            self._sessions.append(session)
            self._bump(session, timestamp, hit_only, is_resist)
            return

        recent = [s for s in self._sessions if self._in_window(s, timestamp)]
        if recent:
            self._bump(max(recent, key=lambda s: s.last_event_time), timestamp, hit_only, is_resist)
            return
        session = _Session(spell_name="", start_time=timestamp, last_event_time=timestamp)
        self._sessions.append(session)
        self._bump(session, timestamp, hit_only, is_resist)

    # -- finalize ------------------------------------------------------------------

    def _finalize_expired(self, now: datetime) -> None:
        expired = [
            s
            for s in self._sessions
            if (now - s.last_event_time).total_seconds() * 1000.0 > TRACK_WINDOW_MS
        ]
        for session in expired:
            self._sessions.remove(session)
            self._emit(session)

    def flush(self) -> None:
        """Finalize every pending session (end of log / tests)."""
        pending, self._sessions = self._sessions, []
        for session in pending:
            self._emit(session)

    def _emit(self, session: _Session) -> None:
        total = session.hits + session.resists
        if total == 0:
            return
        parts = [f"{total} Total"]
        if session.hits:
            parts.append(f"{session.hits} Hit{'' if session.hits == 1 else 's'}")
        if session.resists:
            parts.append(f"{session.resists} Resist{'' if session.resists == 1 else 's'}")
        text = " | ".join(parts)

        # Persistent record in the chat stream, like the C#.
        self.bus.publish(
            CommsEvent(
                timestamp=session.last_event_time,
                line=text,
                channel=CommsChannel.SAY,
                content=text,
                sender="System",
            )
        )
        self.bus.publish(OverlayEvent(text=text, foreground="Yellow"))
        if self.speaker is not None:
            self.speaker.speak(text)
