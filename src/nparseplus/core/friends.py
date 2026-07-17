"""EQ client friends-list sync (Qt-free).

Port of EQTool's Friends tab (SettingsGeneral.xaml.cs): the EQ client keeps a
per-character ``[Friends]`` section in ``<Name>_<ServerSuffix>.ini`` files in
the install directory, with exactly 100 ``FriendN=`` slots padded by
``*NULL*``. Load merges every character's list on a server; Push writes one
merged list back to all of them.

Divergence from the C#: each ini is copied into ``friends_backup/`` beside it
before the first write (same backup-first pattern as ``core.visionfix``) —
EQTool writes the client files with no backup at all.
"""

from __future__ import annotations

import shutil
from pathlib import Path

FRIEND_SLOTS = 100
NULL_SENTINEL = "*NULL*"
BACKUP_DIR_NAME = "friends_backup"

# Display name -> ini filename suffix. Red's files use the P1999PVP suffix.
SERVER_SUFFIXES = {
    "P1999Green": "P1999Green",
    "P1999Blue": "P1999Blue",
    "P1999Red": "P1999PVP",
    "Real-Test": "Real-Test",
}


def friend_ini_files(eq_dir: Path, suffix: str) -> list[Path]:
    """Character ini files for a server (``UI_*`` layout files excluded)."""
    if not eq_dir.is_dir():
        return []
    return sorted(
        path for path in eq_dir.glob(f"*_{suffix}.ini") if not path.name.upper().startswith("UI_")
    )


def read_friends(path: Path) -> list[str]:
    """Friend names from one ini's ``[Friends]`` section, in file order."""
    friends: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return friends
    in_section = False
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("["):
            in_section = trimmed.lower() == "[friends]"
            continue
        if in_section and trimmed:
            _, eq, value = trimmed.partition("=")
            name = value.strip()
            if eq and name and name.upper() != NULL_SENTINEL:
                friends.append(name)
    return friends


def merged_friends(files: list[Path]) -> list[str]:
    """Case-insensitive union of every file's friends, sorted."""
    seen: dict[str, str] = {}
    for path in files:
        for name in read_friends(path):
            seen.setdefault(name.lower(), name)
    return sorted(seen.values(), key=str.lower)


def normalize_names(names: list[str]) -> list[str]:
    """Push-side cleanup: strip, drop blanks/sentinels/dupes, sort, cap at 100."""
    seen: dict[str, str] = {}
    for raw in names:
        name = raw.strip()
        if name and name.upper() != NULL_SENTINEL:
            seen.setdefault(name.lower(), name)
    return sorted(seen.values(), key=str.lower)[:FRIEND_SLOTS]


def write_friends(path: Path, names: list[str]) -> None:
    """Replace (or append) the ``[Friends]`` section with 100 padded slots."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    section_start = -1
    section_end = len(lines)
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.lower() == "[friends]":
            section_start = i
        elif section_start >= 0 and i > section_start and trimmed.startswith("["):
            section_end = i
            break

    slot_lines = [
        f"Friend{i}={names[i] if i < len(names) else NULL_SENTINEL}" for i in range(FRIEND_SLOTS)
    ]

    if section_start >= 0:
        lines[section_start + 1 : section_end] = slot_lines
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[Friends]")
        lines.extend(slot_lines)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _backup(path: Path) -> None:
    backup = path.parent / BACKUP_DIR_NAME / path.name
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)


def push_friends(files: list[Path], names: list[str]) -> list[str]:
    """Write the merged list to every file (backup-first); returns errors."""
    cleaned = normalize_names(names)
    errors: list[str] = []
    for path in files:
        try:
            _backup(path)
            write_friends(path, cleaned)
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return errors
