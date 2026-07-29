"""EQ client friends-list sync (Qt-free).

Port of EQTool's Friends tab (SettingsGeneral.xaml.cs): the EQ client keeps a
per-character ``[Friends]`` section in ``<Name>_<ServerSuffix>.ini`` files in
the install directory, with exactly 100 ``FriendN=`` slots padded by
``*NULL*``. Load merges every character's list on a server; Push writes one
merged list back to all of them.

Divergence from the C#: each ini is copied into ``friends_backup/`` beside it
before the first write (same backup-first pattern as ``core.visionfix``) —
EQTool writes the client files with no backup at all.

The generic read/splice/write plumbing lives in :mod:`nparseplus.core.eqini`;
only the 100-slot body generation is specific to this feature.
"""

from __future__ import annotations

from pathlib import Path

from nparseplus.core import eqini
from nparseplus.core.eqini import NULL_SENTINEL, SERVER_SUFFIXES

FRIEND_SLOTS = 100
BACKUP_DIR_NAME = "friends_backup"

__all__ = [
    "BACKUP_DIR_NAME",
    "FRIEND_SLOTS",
    "NULL_SENTINEL",
    "SERVER_SUFFIXES",
    "friend_ini_files",
    "merged_friends",
    "normalize_names",
    "push_friends",
    "read_friends",
    "write_friends",
]


def friend_ini_files(eq_dir: Path, suffix: str) -> list[Path]:
    """Character ini files for a server (``UI_*`` layout files excluded)."""
    return eqini.character_ini_files(eq_dir, suffix)


def read_friends(path: Path) -> list[str]:
    """Friend names from one ini's ``[Friends]`` section, in file order."""
    friends: list[str] = []
    for line in eqini.section_body(eqini.read_lines(path), "Friends"):
        pair = eqini.split_key_value(line)
        if pair is None:
            continue
        name = pair[1]
        if name and name.upper() != NULL_SENTINEL:
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
    lines = eqini.read_lines(path)
    slot_lines = [
        f"Friend{i}={names[i] if i < len(names) else NULL_SENTINEL}" for i in range(FRIEND_SLOTS)
    ]
    # newline="\n" keeps this bit-identical to the pre-eqini implementation:
    # every slot line is regenerated anyway, so normalizing a CRLF file to LF
    # costs nothing here. Socials, which edits keys in place, preserves the
    # file's own ending instead.
    eqini.write_lines(path, eqini.replace_section(lines, "Friends", slot_lines), newline="\n")


def push_friends(files: list[Path], names: list[str]) -> list[str]:
    """Write the merged list to every file (backup-first); returns errors."""
    cleaned = normalize_names(names)
    errors: list[str] = []
    for path in files:
        try:
            eqini.backup_once(path, BACKUP_DIR_NAME)
            write_friends(path, cleaned)
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return errors
