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


class PlacementResult(BaseModel):
    placed: list[Social] = Field(default_factory=list)
    conflicts: list[Social] = Field(default_factory=list)
    unplaceable: list[Social] = Field(default_factory=list)


def place_socials(
    incoming: Sequence[Social], existing: SocialGrid, *, strategy: Placement
) -> PlacementResult:
    """Decide where ``incoming`` socials land in ``existing``'s grid.

    ``EXACT`` keeps each social's own slot and reports occupied targets as
    ``conflicts`` for the caller to resolve. ``FREE`` reassigns them to empty
    slots in order. Neither ever emits a slot outside the grid — anything that
    doesn't fit is returned in ``unplaceable``.
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
