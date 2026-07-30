"""JsonPluginStorage: round-trip, atomicity hygiene, corruption tolerance."""

from __future__ import annotations

from pathlib import Path

from nparseplus.core.plugins.storage import JsonPluginStorage


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    storage = JsonPluginStorage(tmp_path / "never-created")
    assert storage.load() == {}


def test_round_trip_and_no_tmp_leftover(tmp_path: Path) -> None:
    storage = JsonPluginStorage(tmp_path / "plug")
    storage.save({"items": ["Fine Steel Long Sword"], "interval": 300})
    assert storage.load() == {"items": ["Fine Steel Long Sword"], "interval": 300}
    leftovers = [p for p in (tmp_path / "plug").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_corrupt_file_loads_empty(tmp_path: Path) -> None:
    storage = JsonPluginStorage(tmp_path / "plug")
    storage.data_dir.mkdir(parents=True, exist_ok=True)
    storage.storage_path.write_text("{not json", encoding="utf-8")
    assert storage.load() == {}


def test_non_dict_payload_loads_empty(tmp_path: Path) -> None:
    storage = JsonPluginStorage(tmp_path / "plug")
    storage.data_dir.mkdir(parents=True, exist_ok=True)
    storage.storage_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert storage.load() == {}


def test_data_dir_created_on_access(tmp_path: Path) -> None:
    storage = JsonPluginStorage(tmp_path / "nested" / "plug")
    assert storage.data_dir.is_dir()
