"""``tools/pyinstaller_source_pin.py`` against the real ``uv.lock``.

The Windows release job rebuilds the PyInstaller bootloader from source (#122)
and pins that install to what the lock already resolved. Both halves of the pin
have to exist in the lock or the job breaks at release time, which is the worst
moment to find out: a wheel-only pin means there is no source to build, and a
missing ``win_amd64`` wheel means the "did the rebuild actually happen?" check
has no baseline to compare against.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import pyinstaller_source_pin as pin  # noqa: E402

SHA256 = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture
def package() -> dict:
    return pin.load_package()


def test_the_lock_carries_a_pyinstaller_sdist(package: dict) -> None:
    version, url, digest = pin.sdist_pin(package)
    assert version
    assert url.endswith(".tar.gz")
    assert SHA256.match(digest)


def test_the_lock_carries_the_prebuilt_windows_wheel(package: dict) -> None:
    # The baseline the CI check compares the rebuilt bootloader against.
    url, digest = pin.wheel_pin(package, "win_amd64")
    assert url.endswith("-win_amd64.whl")
    assert SHA256.match(digest)


def test_requirements_is_a_hashed_single_pin(package: dict) -> None:
    text = pin.requirements_text(package)
    assert text.startswith("pyinstaller==")
    assert "--hash=sha256:" in text
    # --require-hashes rejects an unpinned requirement, so == is load-bearing.
    assert text.count("==") == 1


def test_a_missing_package_fails_loudly(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text('version = 1\n[[package]]\nname = "other"\nversion = "1.0"\n')
    with pytest.raises(SystemExit, match="not in"):
        pin.load_package(lock, "pyinstaller")


def test_a_wheel_only_pin_fails_loudly() -> None:
    with pytest.raises(SystemExit, match="no sdist"):
        pin.sdist_pin({"name": "pyinstaller", "version": "1.0", "wheels": []})


def test_an_unhashed_entry_fails_loudly() -> None:
    with pytest.raises(SystemExit, match="not sha256-pinned"):
        pin.sdist_pin({"name": "x", "version": "1.0", "sdist": {"url": "u", "hash": "md5:ab"}})


def test_cli_prints_the_wheel_pin(capsys: pytest.CaptureFixture[str]) -> None:
    assert pin.main(["wheel", "win_amd64"]) == 0
    url, digest = capsys.readouterr().out.split()
    assert url.endswith("-win_amd64.whl")
    assert SHA256.match(digest)
