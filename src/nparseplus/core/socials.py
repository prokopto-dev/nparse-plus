"""EQ client socials — the in-game macro buttons (Qt-free).

The client stores a character's socials in the same
``<Name>_<ServerSuffix>.ini`` files that hold the friends list, under a
``[Socials]`` section keyed by grid position::

    [Socials]
    Page1Button1Name=Assist
    Page1Button1Color=13
    Page1Button1Line1=/assist
    Page1Button1Line2=/pet attack

This has no EQTool counterpart — EQTool never touched socials. It is an
nParse+ feature built on the same file-handling rules as ``core.friends``.

Grid extents differ between client builds (roughly 10 pages by 10-12 buttons,
up to 5 lines each), so nothing here hard-codes them: :func:`read_socials`
*discovers* the pages, buttons, lines, and even the index origin from the file
and treats the module constants only as floors. :func:`place_socials` then
refuses to emit a slot outside the discovered grid, which is what stops an
import from writing ``Page11Button1*`` keys into a ten-page client.

The writer merges at **key level** rather than regenerating the section:
keys belonging to a written slot are replaced in place, that slot's stale
``LineN`` keys are dropped, and everything else is kept verbatim — other
slots, comments, blanks, and unrecognised keys. That last case includes
unrecognised keys on a slot we rewrite: only ``Name``/``Color``/``LineN`` are
ours, and dropping something a future client build reads would be the worse
failure. :class:`Social` deliberately carries no bag of
unknown keys, because the write target is frequently a *different* file than
the one read (copy-to-alt, import-from-a-stranger) and carrying the source
file's stray keys into a target would be actively wrong. Preservation comes
from re-reading the target instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from nparseplus.core import eqini
from nparseplus.core.eqini import NULL_SENTINEL

SECTION = "Socials"
BACKUP_DIR_NAME = "socials_backup"

# Floors, not limits — read_socials grows past them when a file does.
DEFAULT_PAGES = 10
DEFAULT_BUTTONS = 10
MAX_LINES = 5
DEFAULT_COLOR = 13
MAX_COLOR = 255

_KEY_RE = re.compile(r"^Page(\d+)Button(\d+)(?:(Name|Color)|Line(\d+))$", re.IGNORECASE)

LINK_DELIM = "\x12"
"""The item-link delimiter (ASCII DC2), which opens and closes every link.

A macro line may legitimately contain one: ``\\x12<45 hex chars><display
text>\\x12`` is a clickable item link, and a social that fires it links the
item in chat. The client accepts links written into the ini this way — it is
how a macro advertises something clickable.
"""

DISPLAY_LINK_DELIM = "␒"
"""What a delimiter is drawn as on screen: ␒, SYMBOL FOR DEVICE CONTROL TWO."""


def for_display(text: str) -> str:
    """Swap link delimiters for a printable stand-in, for showing a line.

    DC2 draws as nothing, so a perfectly good link reads on screen as random
    hex glued to an item name — which looks exactly like a corrupted macro and
    has been reported as one. Worse, an edit that eats a delimiter is
    invisible: the line still looks the same, and the next time the button
    fires it sprays the link body into the channel as text.

    Substituting a visible glyph costs nothing — the client's charset is
    single-byte, so ␒ cannot reach the game and cannot already be in a line
    that came from one.
    """
    return text.replace(LINK_DELIM, DISPLAY_LINK_DELIM)


def from_display(text: str) -> str:
    """Inverse of :func:`for_display`, for reading an edited line back.

    Typing a ␒ therefore *is* how you write a delimiter by hand, which is a
    feature: there is otherwise no way to enter one from a keyboard, and a
    link you cannot type is a link you cannot repair.
    """
    return text.replace(DISPLAY_LINK_DELIM, LINK_DELIM)


class Social(BaseModel):
    """One macro button: its grid slot, label, colour, and command lines."""

    model_config = ConfigDict(extra="ignore")

    page: int
    button: int
    name: str = ""
    color: int = DEFAULT_COLOR
    lines: list[str] = Field(default_factory=list)

    @property
    def slot(self) -> tuple[int, int]:
        return self.page, self.button

    @property
    def is_empty(self) -> bool:
        return not self.name.strip() and not any(line.strip() for line in self.lines)


class SocialGrid(BaseModel):
    """A character's socials plus the grid dimensions read off their file."""

    model_config = ConfigDict(extra="ignore")

    pages: int = DEFAULT_PAGES
    buttons_per_page: int = DEFAULT_BUTTONS
    lines_per_button: int = MAX_LINES
    page_origin: int = 1
    button_origin: int = 1
    socials: list[Social] = Field(default_factory=list)

    def at(self, page: int, button: int) -> Social | None:
        for social in self.socials:
            if social.page == page and social.button == button:
                return social
        return None

    def slots(self) -> Iterator[tuple[int, int]]:
        """Every valid slot, in page-then-button order."""
        for page in range(self.page_origin, self.page_origin + self.pages):
            for button in range(self.button_origin, self.button_origin + self.buttons_per_page):
                yield page, button

    def free_slots(self) -> Iterator[tuple[int, int]]:
        taken = {social.slot for social in self.socials}
        for slot in self.slots():
            if slot not in taken:
                yield slot

    def contains_slot(self, page: int, button: int) -> bool:
        return (
            self.page_origin <= page < self.page_origin + self.pages
            and self.button_origin <= button < self.button_origin + self.buttons_per_page
        )


class _Draft:
    """Mutable accumulator while scanning one slot's keys."""

    def __init__(self) -> None:
        self.name = ""
        self.color = DEFAULT_COLOR
        self.lines: dict[int, str] = {}


def _is_null(value: str) -> bool:
    return value.upper() == NULL_SENTINEL


def read_socials(path: Path) -> SocialGrid:
    """Parse a character ini's ``[Socials]`` section into a grid.

    A missing file or missing section yields an empty grid at the default
    dimensions, so callers always have something renderable. Never raises.
    """
    body = eqini.section_body(eqini.read_lines(path), SECTION)
    drafts: dict[tuple[int, int], _Draft] = {}
    max_page = max_button = max_line = 0
    min_page = min_button = None

    for line in body:
        pair = eqini.split_key_value(line)
        if pair is None:
            continue
        key, value = pair
        match = _KEY_RE.match(key)
        if match is None:
            continue
        page, button = int(match.group(1)), int(match.group(2))
        draft = drafts.setdefault((page, button), _Draft())
        max_page, max_button = max(max_page, page), max(max_button, button)
        min_page = page if min_page is None else min(min_page, page)
        min_button = button if min_button is None else min(min_button, button)

        field = (match.group(3) or "").lower()
        if field == "name":
            draft.name = "" if _is_null(value) else value
        elif field == "color":
            try:
                draft.color = int(value)
            except ValueError:
                draft.color = DEFAULT_COLOR
        else:
            index = int(match.group(4))
            max_line = max(max_line, index)
            draft.lines[index] = "" if _is_null(value) else value

    socials: list[Social] = []
    for (page, button), draft in drafts.items():
        ordered = [draft.lines[i] for i in sorted(draft.lines)]
        social = Social(page=page, button=button, name=draft.name, color=draft.color, lines=ordered)
        if not social.is_empty:
            socials.append(social)

    page_origin = 1 if min_page is None else min(min_page, 1)
    button_origin = 1 if min_button is None else min(min_button, 1)
    return SocialGrid(
        pages=max(DEFAULT_PAGES, max_page - page_origin + 1),
        buttons_per_page=max(DEFAULT_BUTTONS, max_button - button_origin + 1),
        lines_per_button=max(MAX_LINES, max_line),
        page_origin=page_origin,
        button_origin=button_origin,
        socials=normalize_socials(socials, max_lines=max(MAX_LINES, max_line)),
    )


def normalize_socials(socials: Iterable[Social], *, max_lines: int = MAX_LINES) -> list[Social]:
    """Strip lines, drop trailing blanks and empty macros, dedupe slots, sort.

    ``max_lines`` is the cap; callers that have read a real file pass its
    *discovered* line count so a client build allowing more than
    :data:`MAX_LINES` per button round-trips instead of being truncated.
    """
    by_slot: dict[tuple[int, int], Social] = {}
    for social in socials:
        lines = [line.strip() for line in social.lines]
        while lines and not lines[-1]:
            lines.pop()
        cleaned = Social(
            page=social.page,
            button=social.button,
            name=social.name.strip(),
            color=max(0, min(MAX_COLOR, social.color)),
            lines=lines[:max_lines],
        )
        if not cleaned.is_empty:
            by_slot[cleaned.slot] = cleaned  # last wins
    return sorted(by_slot.values(), key=lambda s: s.slot)


def _slot_keys(social: Social, *, present: set[str]) -> dict[str, str]:
    """The ini keys this social owns, canonical spelling, in write order.

    ``Name`` and ``Color`` are emitted only when the target file already
    carries that key for this slot, or when the value is worth stating (a
    non-empty name, a non-default colour). Writing a key the client never
    wrote would change files we merely read back.
    """
    prefix = f"Page{social.page}Button{social.button}"
    keys: dict[str, str] = {}
    name_key_ = f"{prefix}Name"
    color_key = f"{prefix}Color"
    if social.name or name_key_.lower() in present:
        keys[name_key_] = social.name
    if social.color != DEFAULT_COLOR or color_key.lower() in present:
        keys[color_key] = str(social.color)
    for index, line in enumerate(social.lines, start=1):
        keys[f"{prefix}Line{index}"] = line
    return keys


def _scan_body(body: list[str]) -> tuple[bool, int, set[str]]:
    """``(uses *NULL* padding, highest LineN seen, lowercased keys present)``."""
    null_style = False
    max_line = 0
    present: set[str] = set()
    for line in body:
        pair = eqini.split_key_value(line)
        if pair is None:
            continue
        key, value = pair
        match = _KEY_RE.match(key)
        if match is None:
            continue
        present.add(key.lower())
        if _is_null(value):
            null_style = True
        if match.group(4):
            max_line = max(max_line, int(match.group(4)))
    return null_style, max_line, present


def write_socials(path: Path, socials: Iterable[Social], *, clear_missing: bool = False) -> None:
    """Merge ``socials`` into ``path``'s ``[Socials]`` section, key by key.

    Only keys belonging to a written slot are touched; that slot's stale keys
    (a ``Line4`` left over from a longer macro) are dropped. Unknown keys,
    comments, blank lines, other slots, and every other section survive
    verbatim.

    With ``clear_missing`` the slots *absent* from ``socials`` are emptied too,
    mirroring whatever convention the file already uses: ``*NULL*`` padding if
    it has any, otherwise the keys are simply removed.
    """
    lines = eqini.read_lines(path)
    body = eqini.section_body(lines, SECTION)
    null_style, max_line, present = _scan_body(body)
    cleaned = normalize_socials(socials, max_lines=max(MAX_LINES, max_line))

    desired: dict[str, str] = {}
    for social in cleaned:
        desired.update(_slot_keys(social, present=present))
    owned = {(s.page, s.button) for s in cleaned}
    lowered = {key.lower(): key for key in desired}

    merged: list[str] = []
    for line in body:
        pair = eqini.split_key_value(line)
        match = _KEY_RE.match(pair[0]) if pair is not None else None
        if pair is None or match is None:
            merged.append(line)
            continue

        key = pair[0]
        slot = (int(match.group(1)), int(match.group(2)))
        if slot in owned:
            canonical = lowered.get(key.lower())
            if canonical is None or canonical not in desired:
                # Stale key for a slot we are rewriting, or a duplicate of one
                # we already emitted (EQ inis do repeat keys). Drop it.
                continue
            merged.append(f"{key}={desired.pop(canonical)}")
            continue
        if clear_missing:
            if null_style and (match.group(3) or "").lower() == "name":
                merged.append(f"{key}={NULL_SENTINEL}")
            continue
        merged.append(line)

    trailing_blanks: list[str] = []
    while merged and not merged[-1].strip():
        trailing_blanks.append(merged.pop())
    merged.extend(f"{key}={value}" for key, value in desired.items())
    merged.extend(trailing_blanks)

    eqini.write_lines(
        path,
        eqini.replace_section(lines, SECTION, merged),
        newline=eqini.detect_newline(path),
    )


def push_socials(
    files: Sequence[Path], socials: Iterable[Social], *, clear_missing: bool = False
) -> list[str]:
    """Write ``socials`` to every file (backup-first); returns error strings."""
    cleaned = normalize_socials(socials)
    errors: list[str] = []
    for path in files:
        try:
            eqini.backup_once(path, BACKUP_DIR_NAME)
            write_socials(path, cleaned, clear_missing=clear_missing)
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return errors


def copy_socials(
    source: Path,
    targets: Sequence[Path],
    *,
    pages: Sequence[int] | None = None,
    clear_missing: bool = True,
) -> list[str]:
    """Replicate ``source``'s socials onto ``targets``; returns error strings."""
    grid = read_socials(source)
    socials = grid.socials
    if pages is not None:
        wanted = set(pages)
        socials = [social for social in socials if social.page in wanted]
    return push_socials(targets, socials, clear_missing=clear_missing)


# -- Placement ---------------------------------------------------------------


class Placement(StrEnum):
    EXACT = "exact"
    FREE = "free"
    PAGE = "page"


class PlacementResult(BaseModel):
    placed: list[Social] = Field(default_factory=list)
    conflicts: list[Social] = Field(default_factory=list)
    unplaceable: list[Social] = Field(default_factory=list)
    #: ``PAGE`` only: the group landed intact on one free page and every
    #: macro kept the button it had. False means the fallback fill ran, and
    #: the caller owes the user that difference — "my macros moved" has to be
    #: explainable (#34).
    kept_layout: bool = False


def free_page_for(buttons: Sequence[int], existing: SocialGrid) -> int | None:
    """The first wholly empty page of ``existing`` that holds every button.

    "Wholly empty" is judged the way :meth:`SocialGrid.free_slots` judges a
    slot — no social occupies it — so an ini that stores blank slots and one
    that omits them answer the same. ``None`` when no page qualifies, either
    because every page is in use or because the target grid has fewer buttons
    per page than the group needs.
    """
    taken = {social.slot for social in existing.socials}
    for page in range(existing.page_origin, existing.page_origin + existing.pages):
        if not all(existing.contains_slot(page, button) for button in buttons):
            continue
        if any((page, button) in taken for button in _page_buttons(existing)):
            continue
        return page
    return None


def _page_buttons(existing: SocialGrid) -> range:
    return range(existing.button_origin, existing.button_origin + existing.buttons_per_page)


def _place_keeping_layout(incoming: Sequence[Social], existing: SocialGrid) -> int | None:
    """The page ``incoming`` can move to intact, or None if there isn't one.

    A group qualifies when it is **two or more macros that shared one page**:
    a single macro has no arrangement relative to anything, and giving it a
    whole empty page would cost a page to say nothing. Holes in the source
    page are part of the layout and are carried across; buttons must be
    distinct, or "keeping the relative positions" is not defined.
    """
    if len(incoming) < 2:
        return None
    if len({social.page for social in incoming}) != 1:
        return None
    buttons = [social.button for social in incoming]
    if len(set(buttons)) != len(buttons):
        return None
    return free_page_for(buttons, existing)


def place_socials(
    incoming: Sequence[Social], existing: SocialGrid, *, strategy: Placement
) -> PlacementResult:
    """Decide where ``incoming`` socials land in ``existing``'s grid.

    ``EXACT`` keeps each social's own slot and reports occupied targets as
    ``conflicts`` for the caller to resolve. ``FREE`` reassigns them to empty
    slots in order. ``PAGE`` moves a group that shared one page onto the first
    wholly empty page, **keeping every button where it was**, and falls back to
    ``FREE`` when there is no whole page to take. No strategy ever emits a slot
    outside the grid — anything that doesn't fit is returned in ``unplaceable``.

    ``PAGE`` exists because ``FREE`` is a flat sequential fill (#34): a pack
    whose macros occupied one coherent page — a pull rotation, say — gets
    smeared across whatever isolated holes the target grid has, and muscle
    memory is positional. The absolute slots can't always be kept; the
    *relative* arrangement usually can.
    """
    result = PlacementResult()
    if strategy is Placement.EXACT:
        for social in incoming:
            if not existing.contains_slot(social.page, social.button):
                result.unplaceable.append(social)
            elif existing.at(social.page, social.button) is not None:
                result.conflicts.append(social)
            else:
                result.placed.append(social)
        return result

    if strategy is Placement.PAGE:
        page = _place_keeping_layout(incoming, existing)
        if page is not None:
            result.kept_layout = True
            result.placed = [social.model_copy(update={"page": page}) for social in incoming]
            return result

    free = existing.free_slots()
    for social in incoming:
        slot = next(free, None)
        if slot is None:
            result.unplaceable.append(social)
            continue
        result.placed.append(social.model_copy(update={"page": slot[0], "button": slot[1]}))
    return result


# -- Duplicates --------------------------------------------------------------


class DuplicateKind(StrEnum):
    IDENTICAL = "identical"
    SAME_NAME = "same_name"
    SAME_BODY = "same_body"


class DuplicateGroup(BaseModel):
    kind: DuplicateKind
    socials: list[Social]


def name_key(social: Social) -> str:
    return social.name.strip().casefold()


def body_key(social: Social) -> tuple[str, ...]:
    return tuple(line.strip() for line in social.lines if line.strip())


def social_key(social: Social) -> tuple[str, tuple[str, ...]]:
    return name_key(social), body_key(social)


def find_duplicates(socials: Iterable[Social]) -> list[DuplicateGroup]:
    """Group socials that share a name *or* an identical command body.

    Reports only — nothing here ever clears a slot. Empty macros are never
    duplicates, and each slot appears in at most one group: identical matches
    win over same-name, which win over same-body, so a grid badge is
    unambiguous.
    """
    candidates = [social for social in socials if not social.is_empty]
    claimed: set[tuple[int, int]] = set()
    groups: list[DuplicateGroup] = []

    def collect(kind: DuplicateKind, key) -> None:
        buckets: dict[object, list[Social]] = {}
        for social in candidates:
            if social.slot in claimed:
                continue
            value = key(social)
            if not value:
                continue
            buckets.setdefault(value, []).append(social)
        for members in buckets.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda s: s.slot)
            claimed.update(social.slot for social in members)
            groups.append(DuplicateGroup(kind=kind, socials=members))

    collect(DuplicateKind.IDENTICAL, social_key)
    collect(DuplicateKind.SAME_NAME, name_key)
    collect(DuplicateKind.SAME_BODY, body_key)
    groups.sort(key=lambda group: group.socials[0].slot)
    return groups
