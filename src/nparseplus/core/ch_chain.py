"""Complete Heal chain protocol — parsing and warn logic.

Port of EQTool's Services/CHService.cs (``ChainData``/``ShouldWarnOfChain``)
and the ``ChCheck`` message scanner from
Services/Parsing/CompleteHealCommsHandler.cs. The bus-facing handlers live in
``nparseplus.core.handlers.complete_heal``; this module is pure logic so it
can be tested without a bus.

CH chain calls look like ``'GG 014 CH -- Wreckognize'``: an optional raid tag
(``GG``), a chain position (three digits like ``014``, a repeated letter like
``AAA``, or ``RAMP1``/``RAMP01``), the word CH (or RCH for a re-CH), and the
heal target.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache

from nparseplus.core.events import CompleteHealEvent

logger = logging.getLogger(__name__)

# A chain entry goes stale when its target has not been called for this long
# (CompleteHealHandler.GetOrCreateChain).
CHAIN_IDLE_EXPIRY = timedelta(seconds=20)

_RCH_WORD_RE = re.compile(r"\bCH\b", re.IGNORECASE)
_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_RECIPIENT_KEEP_RE = re.compile(r"[^A-Za-z '`]")

# Raid-leader CH cadence callouts (#15) — "healers to 4 (seconds)",
# "chain to 3", "CH to 5", or "4 second chain". nparseplus extension (no
# EQTool equivalent). Each pattern is a user-editable regex whose FIRST
# capturing group is the seconds between chained casts; these are the stock
# defaults (also the default value of the ch_cadence_patterns setting).
DEFAULT_CH_CADENCE_PATTERNS: tuple[str, ...] = (
    r"\b(?:healers?|heals?|chain|ch)\s+to\s+(\d{1,2})\b",
    r"\b(\d{1,2})\s*(?:s|sec|secs|second|seconds)\s+(?:chain|ch|heals?|casts?)\b",
)
# Bounds keep stray numbers ("healers to 40 people") from reading as a cadence.
_CADENCE_MIN_S = 1
_CADENCE_MAX_S = 30


@lru_cache(maxsize=32)
def _compiled_cadence(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Compile (and cache) the cadence patterns; bad regexes are skipped so one
    typo in a user pattern can't break cadence detection for the rest."""
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            logger.warning("CH cadence pattern failed to compile: %r", pattern)
    return tuple(compiled)


def parse_ch_cadence(content: str, patterns: Sequence[str] | None = None) -> int | None:
    """The declared seconds-between-casts from a raid CH cadence call, or None.

    ``patterns`` are user-editable regexes (each with a first capturing group
    for the number); ``None``/empty falls back to
    :data:`DEFAULT_CH_CADENCE_PATTERNS`. The matched number must fall in
    [1, 30]. Pure and wall-clock-free (#15).
    """
    pats = tuple(patterns) if patterns else DEFAULT_CH_CADENCE_PATTERNS
    for regex in _compiled_cadence(pats):
        match = regex.search(content)
        if match and match.groups():
            seconds = _to_int(match.group(1))
            if _CADENCE_MIN_S <= seconds <= _CADENCE_MAX_S:
                return seconds
    return None


@dataclass
class ChainData:
    """Per-target chain state (CHService.ChainData)."""

    highest_order: str = ""
    your_chain_order: str = ""


def should_warn_of_chain(chain: ChainData, event: CompleteHealEvent) -> bool:
    """True when the caller one position before yours just called CH.

    Faithful port of CHService.ShouldWarnOfChain, including its wrap-around
    rules ('z'->'a' letters, highest->001 numbers) and the mutation of
    ``chain`` as calls are observed.
    """
    if not event.position or event.position.isspace():
        return False

    increased_order = False
    first_run = False
    if event.caster == "You":
        chain.your_chain_order = event.position
        first_run = True
    if not chain.highest_order.strip() or event.position > chain.highest_order:
        chain.highest_order = event.position
        increased_order = True

    if not chain.your_chain_order.strip() or first_run:
        return False

    if (
        chain.highest_order[0].isalpha()
        and event.position[0].isalpha()
        and chain.your_chain_order[0].isalpha()
    ):
        highest = chain.highest_order[0].lower()
        mine = chain.your_chain_order[0].lower()
        current = event.position[0].lower()
        dif = ord(mine) - ord(current)
        return (mine != "a" or not increased_order or dif != 1) and (
            (highest == "z" and mine == "a" and current == "z" and not increased_order) or dif == 1
        )

    if (
        chain.highest_order[0].isdigit()
        and event.position[0].isdigit()
        and chain.your_chain_order[0].isdigit()
    ):
        highest = _to_int(chain.highest_order)
        mine = _to_int(chain.your_chain_order)
        current = _to_int(event.position)
        dif = mine - current
        return (mine != 1 or not increased_order or dif != 1) and (
            (current == highest and mine == 1 and not increased_order) or dif == 1
        )

    return False


def _to_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _find_ch_word(line: str) -> tuple[str, int, str]:
    """Locate the CH/RCH keyword. Returns (keyword, index, possibly-rewritten
    line); index is -1 when no CH call is present (ChCheck's front half)."""
    lower = line.lower()
    ch_word = " ch "
    ch_index = lower.find(" ch ")
    if ch_index == -1:
        ch_word = "ch "
        ch_index = 0 if lower.startswith("ch ") else -1

    if " rch " in lower:
        # An RCH call may also contain a standalone CH word; strip it first.
        line = _RCH_WORD_RE.sub("", line)
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        ch_word = " rch "
        ch_index = line.lower().find(" rch ")
    if ch_index == -1:
        ch_word = " rch "
        ch_index = lower.find(" rch ")
    return ch_word, ch_index, line


def _find_position(tokens_source: str) -> str:
    """Chain position: last 3-digit / repeated-letter 3-char token, falling
    back to a RAMP<n> token, then '000'."""
    three_char = [t for t in tokens_source.split(" ") if len(t) == 3]
    for token in reversed(three_char):
        digits = "".join(c for c in token if c.isdigit())
        if len(digits) == 3:
            return digits
        if len(set(token)) == 1 and token[0].isalpha():
            return token

    position = ""
    ramp_tokens = [t for t in tokens_source.split(" ") if t.lower().startswith("ramp")]
    for token in reversed(ramp_tokens):
        if len(token) in (5, 6):
            position = token
    return position or "000"


def parse_ch_message(
    sender: str,
    content: str,
    timestamp: datetime,
    *,
    configured_tag: str = "",
    npcs: frozenset[str] = frozenset(),
    line: str = "",
    line_number: int = 0,
) -> CompleteHealEvent | None:
    """Parse one comms message as a CH chain call (ChCheck port).

    ``configured_tag`` mirrors the ChChainTagOverlay setting: when set, only
    messages starting with that tag are accepted. ``npcs`` is the casefolded
    master NPC name set used to keep multi-word NPC heal targets intact.
    """
    ch_word, ch_index, content = _find_ch_word(content)
    if ch_index == -1:
        return None

    remainder = content
    tag = configured_tag
    if tag and not tag.isspace():
        if not remainder.startswith(tag):
            return None
    else:
        tag = ""
    if tag and not tag.isspace():
        remainder = remainder[len(tag) :]

    position = _find_position(remainder)

    ch_index = remainder.lower().find(ch_word)
    before = remainder[:ch_index].split()
    if len(before) > 2:
        return None
    remainder = remainder[ch_index + len(ch_word) :]

    possible_recipient = _RECIPIENT_KEEP_RE.sub("", remainder.replace(position, "")).strip()
    recipient = ""
    if possible_recipient.casefold() in npcs:
        recipient = possible_recipient
    else:
        splits = possible_recipient.split()
        if len(splits) > 2:
            return None
        for item in splits:
            if len(item) >= 3:
                recipient = item
                break

    if not recipient or len(recipient) < 3:
        return None

    if tag and not tag.isspace() and " " in tag:
        tag = ""
        if configured_tag and not configured_tag.isspace():
            return None

    caster = sender
    if " " in caster:
        caster = caster.rsplit(" ", 1)[1].strip()

    return CompleteHealEvent(
        timestamp=timestamp,
        line=line,
        line_number=line_number,
        recipient=recipient.strip(),
        tag=tag,
        position=position,
        caster=caster,
    )


@dataclass
class CHChainService:
    """Tracks per-target chains and answers "should this call warn me?"

    Port of CompleteHealHandler's chainDatas bookkeeping; uses the event
    timestamps instead of wall-clock ``DateTime.Now`` so replays are exact.
    """

    @dataclass
    class _Entry:
        chain: ChainData
        updated_at: datetime

    _entries: dict[str, _Entry] = field(default_factory=dict)

    def observe(self, event: CompleteHealEvent) -> bool:
        now = event.timestamp
        stale = [
            target
            for target, entry in self._entries.items()
            if now - entry.updated_at > CHAIN_IDLE_EXPIRY
        ]
        for target in stale:
            del self._entries[target]

        entry = self._entries.get(event.recipient)
        if entry is None:
            entry = self._Entry(chain=ChainData(), updated_at=now)
            self._entries[event.recipient] = entry
        entry.updated_at = now
        return should_warn_of_chain(entry.chain, event)
