"""VisionFix apply/revert against a fake EQ install (tmp_path)."""

import zipfile
from pathlib import Path

import pytest

from nparseplus.core.visionfix import (
    BACKUP_DIR_NAME,
    apply_visionfix,
    backup_exists,
    default_zip_path,
    preflight,
    revert_visionfix,
)


@pytest.fixture
def eq_dir(tmp_path: Path) -> Path:
    install = tmp_path / "EverQuest"
    install.mkdir()
    (install / "eqgame.exe").write_bytes(b"MZ fake game binary")
    (install / "uifiles").mkdir()
    shaders = install / "RenderEffects" / "MPL"
    shaders.mkdir(parents=True)
    (shaders / "FinalBlend.fxo").write_bytes(b"ORIGINAL SHADER BYTES")
    return install


@pytest.fixture
def fix_zip(tmp_path: Path) -> Path:
    path = tmp_path / "visionfix.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("RenderEffects/MPL/FinalBlend.fxo", b"FIXED SHADER BYTES")
        zf.writestr("RenderEffects/MPL/NewEffect.fxo", b"NEW FILE FROM FIX")
        zf.writestr("Resources/Sky/sky1.dds", b"FIXED SKY")
    return path


def test_preflight_rejects_non_installs(tmp_path: Path, eq_dir: Path) -> None:
    assert preflight(None) is not None
    assert preflight(tmp_path / "missing") is not None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "eqgame.exe" in preflight(empty)
    assert preflight(eq_dir) is None


def test_apply_backs_up_then_overwrites(eq_dir: Path, fix_zip: Path) -> None:
    written = apply_visionfix(eq_dir, fix_zip)
    assert written == 3
    target = eq_dir / "RenderEffects" / "MPL" / "FinalBlend.fxo"
    assert target.read_bytes() == b"FIXED SHADER BYTES"
    backup = eq_dir / BACKUP_DIR_NAME / "RenderEffects" / "MPL" / "FinalBlend.fxo"
    assert backup.read_bytes() == b"ORIGINAL SHADER BYTES"  # byte-identical snapshot
    # Files the fix ADDED have no backup (nothing was overwritten).
    assert not (eq_dir / BACKUP_DIR_NAME / "RenderEffects" / "MPL" / "NewEffect.fxo").exists()
    assert (eq_dir / "Resources" / "Sky" / "sky1.dds").read_bytes() == b"FIXED SKY"
    assert backup_exists(eq_dir)


def test_reapply_preserves_original_backup(eq_dir: Path, fix_zip: Path) -> None:
    apply_visionfix(eq_dir, fix_zip)
    apply_visionfix(eq_dir, fix_zip)  # second run: backup would now be the fix
    backup = eq_dir / BACKUP_DIR_NAME / "RenderEffects" / "MPL" / "FinalBlend.fxo"
    assert backup.read_bytes() == b"ORIGINAL SHADER BYTES"


def test_revert_restores_byte_identical(eq_dir: Path, fix_zip: Path) -> None:
    apply_visionfix(eq_dir, fix_zip)
    restored = revert_visionfix(eq_dir)
    assert restored == 1
    target = eq_dir / "RenderEffects" / "MPL" / "FinalBlend.fxo"
    assert target.read_bytes() == b"ORIGINAL SHADER BYTES"
    assert backup_exists(eq_dir)  # revert is repeatable
    assert revert_visionfix(eq_dir) == 1


def test_revert_without_backup_raises(eq_dir: Path) -> None:
    with pytest.raises(ValueError):
        revert_visionfix(eq_dir)


def test_apply_rejects_bad_dir(tmp_path: Path, fix_zip: Path) -> None:
    with pytest.raises(ValueError):
        apply_visionfix(tmp_path, fix_zip)


def test_shipped_zip_exists_and_looks_right() -> None:
    path = default_zip_path()
    assert path.is_file()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert any(n.startswith("RenderEffects/") for n in names)
    assert len(names) > 100  # the real pack is ~272 files
