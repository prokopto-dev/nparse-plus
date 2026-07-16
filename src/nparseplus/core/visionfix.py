"""Night Vision fix — apply/revert data/visionfix.zip over the EQ install.

Port of EQTool's FixNightVision (SettingsGeneral.xaml.cs:1207): the shipped
zip (RenderEffects/*.fxo shader replacements + Resources/* sky/water
textures) is extracted over the EQ directory. The C# just overwrites;
this port first copies every file that would be overwritten into a
``visionfix_backup/`` tree beside them, and Revert restores from it.

Pure file operations — works through a wine/CrossOver wrapper's
filesystem. The "is EQ running" check stays in the UI layer (warn-only).
"""

from __future__ import annotations

import shutil
import zipfile
from importlib import resources
from pathlib import Path

BACKUP_DIR_NAME = "visionfix_backup"


def default_zip_path() -> Path:
    """The bundled data/visionfix.zip (package data; frozen-app safe)."""
    return Path(str(resources.files("nparseplus") / "data" / "visionfix.zip"))


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


def backup_exists(eq_dir: Path) -> bool:
    return (Path(eq_dir) / BACKUP_DIR_NAME).is_dir()


def _zip_file_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [m for m in zf.infolist() if not m.is_dir()]


def apply_visionfix(eq_dir: Path, zip_path: Path | None = None) -> int:
    """Extract the fix over ``eq_dir``; returns the number of files written.

    Every file that would be overwritten is first copied (byte-identical,
    metadata preserved) into ``eq_dir/visionfix_backup/<relative path>`` —
    unless that backup already exists, so re-applying never clobbers the
    original snapshot.
    """
    reason = preflight(eq_dir)
    if reason is not None:
        raise ValueError(reason)
    eq_dir = Path(eq_dir)
    backup_root = eq_dir / BACKUP_DIR_NAME
    written = 0
    with zipfile.ZipFile(zip_path or default_zip_path()) as zf:
        for member in _zip_file_members(zf):
            relative = Path(member.filename)
            destination = eq_dir / relative
            if destination.is_file():
                backup = backup_root / relative
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written += 1
    return written


def revert_visionfix(eq_dir: Path) -> int:
    """Restore every backed-up file; returns the number restored.

    The backup tree is kept (revert is repeatable). Files the fix *added*
    (no pre-existing original) are not deleted — same net effect as the C#,
    which has no revert at all: the game only reads known filenames.
    """
    eq_dir = Path(eq_dir)
    backup_root = eq_dir / BACKUP_DIR_NAME
    if not backup_root.is_dir():
        raise ValueError("No visionfix backup found — nothing to revert.")
    restored = 0
    for backup in sorted(backup_root.rglob("*")):
        if not backup.is_file():
            continue
        destination = eq_dir / backup.relative_to(backup_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, destination)
        restored += 1
    return restored
