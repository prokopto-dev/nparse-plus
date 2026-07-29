"""config.paths — platformdirs-derived locations."""

from pathlib import Path

import pytest

from nparseplus.config import paths


@pytest.fixture
def fake_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "user_data_dir", lambda app: str(tmp_path / app))
    return tmp_path / paths.APP_NAME


def test_data_and_socials_dirs_are_derived(fake_data_root: Path) -> None:
    assert paths.data_dir() == fake_data_root
    assert paths.socials_dir() == fake_data_root / "socials"


def test_ensure_socials_dir_creates_it(fake_data_root: Path) -> None:
    created = paths.ensure_socials_dir()
    assert created == fake_data_root / "socials"
    assert created.is_dir()
    # Idempotent.
    assert paths.ensure_socials_dir() == created


def test_settings_path_sits_under_the_config_dir() -> None:
    assert paths.settings_path() == paths.config_dir() / paths.SETTINGS_FILENAME
