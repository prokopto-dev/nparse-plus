"""Shared plumbing for the EQ client's per-character ``.ini`` files (Qt-free).

The client keeps one ``<Name>_<ServerSuffix>.ini`` per character in the
install directory, holding several unrelated sections — ``[Defaults]``,
``[Friends]``, ``[Socials]``, ``[KeyMaps]``, … Features that edit one section
must leave every other byte alone, so they all share the same read → splice
one section → write cycle. That cycle lives here.

Deliberately **not** :mod:`configparser`: EQ ini files carry duplicate keys,
inconsistent casing, and comment styles that configparser would reorder or
drop, and it would rewrite the whole file rather than the one section we own.

The helpers are plumbing only — no policy. Callers generate their own section
body (``[Friends]`` regenerates 100 padded slots wholesale; ``[Socials]``
edits individual keys in place) and hand it to :func:`replace_section`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

NULL_SENTINEL = "*NULL*"

# Display name -> ini filename suffix. Red's files use the P1999PVP suffix.
SERVER_SUFFIXES = {
    "P1999Green": "P1999Green",
    "P1999Blue": "P1999Blue",
    "P1999Red": "P1999PVP",
    "Real-Test": "Real-Test",
}


def read_lines(path: Path) -> list[str]:
    """Every line of ``path``, newlines stripped; ``[]`` if it can't be read.

    ``errors="surrogateescape"`` rather than ``"replace"`` so bytes that are
    not valid UTF-8 (a cp1252 character in some unrelated section) survive a
    read/write round trip instead of being silently replaced.
    """
    try:
        return path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    except OSError:
        return []


def write_lines(path: Path, lines: list[str], *, newline: str = "\n") -> None:
    """Write ``lines`` back to ``path``, joined by ``newline`` with a trailing one."""
    path.write_text(
        newline.join(lines) + newline,
        encoding="utf-8",
        errors="surrogateescape",
        newline="",
    )


def detect_newline(path: Path) -> str:
    """``"\\r\\n"`` if the file uses CRLF anywhere, else ``"\\n"``."""
    try:
        with open(path, "rb") as handle:
            return "\r\n" if b"\r\n" in handle.read() else "\n"
    except OSError:
        return "\n"


def section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """``(header index, end index exclusive)`` of section ``name``, or None.

    The end is the next section header, or the end of the file for the last
    section.
    """
    header = f"[{name}]".lower()
    start = -1
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if start < 0:
            if trimmed.lower() == header:
                start = i
        elif trimmed.startswith("["):
            return start, i
    if start < 0:
        return None
    return start, len(lines)


def section_body(lines: list[str], name: str) -> list[str]:
    """The lines *inside* section ``name`` (header excluded); ``[]`` if absent."""
    bounds = section_bounds(lines, name)
    if bounds is None:
        return []
    start, end = bounds
    return lines[start + 1 : end]


def replace_section(lines: list[str], name: str, body: list[str]) -> list[str]:
    """``lines`` with section ``name``'s body replaced by ``body``.

    Appends the section (separated by a blank line) when it is missing. Every
    other section is untouched.
    """
    result = list(lines)
    bounds = section_bounds(result, name)
    if bounds is not None:
        start, end = bounds
        result[start + 1 : end] = body
        return result
    if result and result[-1].strip():
        result.append("")
    result.append(f"[{name}]")
    result.extend(body)
    return result


def split_key_value(line: str) -> tuple[str, str] | None:
    """``(key, value)`` for a ``Key=Value`` line, else None.

    None for blanks, comments (``;`` or ``#``), section headers, and any line
    without a key before the first ``=``.
    """
    trimmed = line.strip()
    if not trimmed or trimmed.startswith((";", "#", "[")):
        return None
    key, eq, value = trimmed.partition("=")
    key = key.strip()
    if not eq or not key:
        return None
    return key, value.strip()


def character_ini_files(eq_dir: Path, suffix: str) -> list[Path]:
    """Character ini files for a server (``UI_*`` layout files excluded)."""
    eq_dir = Path(eq_dir)
    if not eq_dir.is_dir():
        return []
    return sorted(
        path for path in eq_dir.glob(f"*_{suffix}.ini") if not path.name.upper().startswith("UI_")
    )


def character_name(path: Path, suffix: str) -> str:
    """``Xantik`` from ``Xantik_P1999Green.ini`` (stem returned unchanged if it
    doesn't carry the suffix)."""
    stem = Path(path).stem
    tail = f"_{suffix}"
    if stem.lower().endswith(tail.lower()):
        return stem[: -len(tail)]
    return stem


def backup_once(path: Path, dir_name: str) -> None:
    """Copy ``path`` into ``<parent>/<dir_name>/`` unless already backed up.

    Only the *first* backup is kept, so repeated writes never overwrite the
    pristine original with an already-modified copy.
    """
    path = Path(path)
    backup = path.parent / dir_name / path.name
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)


def preflight(eq_dir: Path | None) -> str | None:
    """None when ``eq_dir`` looks like an EQ install; else the reason."""
    if eq_dir is None:
        return "Set the EQ install directory first."
    eq_dir = Path(eq_dir)
    if not eq_dir.is_dir():
        return f"Not a directory: {eq_dir}"
    if not (eq_dir / "eqgame.exe").is_file():
        return "No eqgame.exe here — not an EQ install directory."
    if not (eq_dir / "uifiles").is_dir():
        return "No uifiles/ here — not an EQ install directory."
    return None
