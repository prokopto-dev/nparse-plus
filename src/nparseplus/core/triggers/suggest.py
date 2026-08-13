"""Turn a log line you just saw into a trigger (#82).

The pure half of the Console window's "Create trigger from this line…": the
window is a renderer over this, the same split ``ch_chain.parse_ch_cadence``
and ``chrome``/``skins`` already use. No EQTool equivalent exists — its
``TriggerTestSampleGenerator`` solves the inverse problem (pattern -> sample
line), so this is designed for how nParse+ works rather than ported.

Three things have to be right or the produced trigger is worse than useless:

* **The timestamp goes.** The pipeline matches on ``LineInfo.message``, so a
  pattern carrying ``[Wed Jul 15 12:00:00 2026]`` would never fire. The
  Console renders its own ``[HH:MM:SS] `` prefix on top of that, so both
  shapes are stripped.
* **Escaping.** The literal parts of the line are ``re.escape``\\ d, which
  also protects any brace the line itself carries: ``{`` becomes ``\\{``, and
  :meth:`Trigger._compile`'s ``\\{(\\w+)\\}`` placeholder pass cannot match
  across the backslash before the closing brace, so a log line mentioning
  ``{c}`` stays a literal instead of silently becoming a token.
* **Tokens.** A pattern built only from literals matches exactly one line
  forever. The player's name becomes ``{c}`` and a leading actor becomes
  ``{name}`` — the tokens :class:`~nparseplus.core.triggers.model.Trigger`
  already understands, using its own ``[\\w` ]+`` name expansion rather than a
  second name pattern invented here. What the actor pattern accepts must
  stay a **subset** of what that expansion can consume, or the token cannot
  match the text it replaced (see ``_ACTOR_WORD``); the round-trip tests
  assert the capture equals the replaced text, not merely that it matched.

The actor heuristic is deliberately conservative: it only fires on a leading
run of capitalised words followed by a lowercase word ("Gorenaire begins…",
"Lord Nagafen hits…"). Capitalised pronouns are skipped — "You"/"Your" lines
are yours, not an actor's, and "It begins to rain." has no actor at all — as
is a bare leading article: EQ writes lowercase mob names after one ("A cliff
golem hits you"), where the capitalised run is just the article and
tokenising it would produce ``{name} cliff golem hits you``. No token beats a
wrong token; the literal form is always offered as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Leading "[Wed Jul 15 12:00:00 2026] " (a raw log line) or "[12:00:00] "
#: (what the Console window renders). The clock is required, so a message
#: that legitimately opens with a bracket is left alone.
_TIMESTAMP_PREFIX_RE = re.compile(r"^\[[^\]]*\d{1,2}:\d{2}:\d{2}[^\]]*\]\s*")

#: What one word of an actor name may contain. This MUST stay a subset of
#: ``EQ_NAME_CHARS`` — the class ``{name}`` expands to — or the token cannot
#: consume the very text it replaced. An apostrophe here was exactly that
#: bug: "Gorenaire's corpse falls to the ground." tokenised the possessive
#: into the name, and since ``[\w` ]+`` cannot cross the apostrophe the
#: pattern re-matched from the "s" instead. It still reported a match, and
#: quietly captured "s" — a wrong answer is worse than a missing one.
_ACTOR_WORD = r"[\w`]*"

#: A leading run of capitalised words, followed either by a lowercase word
#: (the verb in every EQ actor line) or by a possessive "'s" — which is left
#: OUT of the name and matched literally, so "{name}'s corpse falls to the
#: ground." captures the owner. ``\w`` covers the digits pet names carry, and
#: the backtick appears inside EQ names ("Ixiblat`Fer").
_ACTOR_RE = re.compile(rf"^[A-Z]{_ACTOR_WORD}(?: [A-Z]{_ACTOR_WORD})*(?=(?: [a-z])|(?:'s\b))")

#: Capitalised openers that are never an actor name on their own: pronouns
#: (yours are "You"/"Your" lines; "It begins to rain.") and bare articles.
_NOT_ACTORS = frozenset(
    {"you", "your", "i", "it", "he", "she", "they", "we", "this", "that", "there", "a", "an", "the"}
)

_MAX_NAME_CHARS = 48

#: A ``{word}`` sequence in OUTPUT text. ``Trigger.expand`` rewrites ``{c}``,
#: ``{COUNTER}`` and any token naming a capture — and the model has no escape
#: for a literal brace in output text (EQTool parity: the C# has none
#: either). So a log line that happens to contain one cannot be reproduced
#: verbatim on the overlay, and copying it through unchanged is the bad
#: option: "cast {c} on me" would announce your own character's name, and
#: "{COUNTER} times" a tally that changes on every fire, where the log said
#: neither. Dropping the braces is the closest stable rendering.
_OUTPUT_TOKEN_RE = re.compile(r"\{(\w+)\}")


def neutralize_output_tokens(text: str) -> str:
    """Strip the braces from ``{word}`` sequences so ``expand`` leaves them be.

    Applied to text taken from the log, never to the tokens this module
    inserts deliberately. Every ``{word}`` goes, not only the ones expand
    would rewrite today: which ones are live depends on the trigger's capture
    groups at match time, so sparing "safe" ones is a rule that breaks the
    moment the user adds a group to the pattern.
    """
    return _OUTPUT_TOKEN_RE.sub(r"\1", text)


@dataclass(frozen=True, slots=True)
class TriggerSuggestion:
    """Both forms of a trigger built from one log line; the user picks.

    ``pattern``/``display_text`` are the tokenised offer (regex mode);
    ``literal``/``literal_display`` are the plain-text (non-regex) offer.
    With no tokens applied ``pattern`` is simply the fully escaped message,
    which still matches the line it came from.

    The two ``*_display`` fields are OUTPUT text and are not interchangeable
    with the search fields: ``literal`` keeps the message verbatim because a
    non-regex search is a substring test, while ``literal_display`` has any
    brace token neutralized because output text is expanded.
    """

    message: str
    name: str
    pattern: str
    literal: str
    display_text: str
    tokens: tuple[str, ...] = ()
    literal_display: str = ""

    @property
    def has_tokens(self) -> bool:
        return bool(self.tokens)


def strip_timestamp(line: str) -> str:
    """Drop a leading log or Console timestamp and surrounding whitespace."""
    return _TIMESTAMP_PREFIX_RE.sub("", line).strip()


def suggest_trigger_name(message: str) -> str:
    """A short, human-readable trigger name derived from the line."""
    text = " ".join(message.split()).strip(" .!,'\"")
    if len(text) > _MAX_NAME_CHARS:
        head = text[:_MAX_NAME_CHARS].rsplit(" ", 1)[0] or text[:_MAX_NAME_CHARS]
        text = head.rstrip(" .!,")
    return text or "New Trigger"


def _player_spans(message: str, player_name: str) -> list[tuple[int, int, str, str]]:
    name = (player_name or "").strip()
    # "You" is the Trigger Editor's stand-in when no character is known yet,
    # and EQ writes the second person for your own actions — never your name.
    # Tokenising it would build a trigger that matches until the app learns
    # who you are and then quietly stops.
    if not name or name.lower() in {"you", "your"}:
        return []
    # Word boundaries only where they mean anything: a name ending in a
    # backtick has no \b after it, and \b would then never match.
    prefix = r"\b" if name[:1].isalnum() or name[:1] == "_" else ""
    suffix = r"\b" if name[-1:].isalnum() or name[-1:] == "_" else ""
    try:
        pattern = re.compile(f"{prefix}{re.escape(name)}{suffix}", re.IGNORECASE)
    except re.error:  # pragma: no cover - re.escape makes this unreachable
        return []
    return [(m.start(), m.end(), "{c}", "c") for m in pattern.finditer(message)]


def _actor_span(message: str) -> tuple[int, int, str, str] | None:
    match = _ACTOR_RE.match(message)
    if match is None or match.group(0).lower() in _NOT_ACTORS:
        return None
    return (match.start(), match.end(), "{name}", "name")


def suggest_trigger_text(line: str, player_name: str = "") -> TriggerSuggestion:
    """Build the tokenised and literal trigger forms for one log line.

    ``line`` may still carry a timestamp (raw log line or Console row).
    ``player_name`` is the character whose name becomes ``{c}``; pass the same
    name the Trigger Editor tests with, or the prefilled trigger will not
    match its own source line in the test box.
    """
    message = strip_timestamp(line)
    if not message:
        return TriggerSuggestion(message="", name="", pattern="", literal="", display_text="")
    literal_display = neutralize_output_tokens(message)

    spans = _player_spans(message, player_name)
    actor = _actor_span(message)
    # A line opening with your own name is already covered by {c}.
    if actor is not None and not any(
        start < actor[1] and actor[0] < end for start, end, _, _ in spans
    ):
        spans.append(actor)
    spans.sort(key=lambda span: span[0])

    pattern_parts: list[str] = []
    display_parts: list[str] = []
    tokens: list[str] = []
    cursor = 0
    for start, end, token, label in spans:
        # Search text escapes the literal halves; display text neutralizes
        # their brace tokens. Same spans, different output languages — the
        # token we insert is added raw to both, because there it is meant.
        pattern_parts.append(re.escape(message[cursor:start]))
        pattern_parts.append(token)
        display_parts.append(neutralize_output_tokens(message[cursor:start]))
        display_parts.append(token)
        if label not in tokens:
            tokens.append(label)
        cursor = end
    pattern_parts.append(re.escape(message[cursor:]))
    display_parts.append(neutralize_output_tokens(message[cursor:]))

    return TriggerSuggestion(
        message=message,
        name=suggest_trigger_name(message),
        pattern="".join(pattern_parts),
        literal=message,
        display_text="".join(display_parts),
        tokens=tuple(tokens),
        literal_display=literal_display,
    )
