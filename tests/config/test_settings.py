"""Tests for nparseplus.config.settings persistence and helpers."""

import json
import threading
import time
from pathlib import Path

from nparseplus.config.settings import (
    DebouncedSaver,
    PlayerInfo,
    Settings,
    WindowState,
    get_player,
    load_settings,
    save_settings,
)
from nparseplus.core.triggers.model import Trigger, TriggerTimer


def test_defaults_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded == original


def test_populated_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    original.general.font_size = 14
    original.sharing.mode = "nparse"
    original.windows["maps"] = WindowState(geometry=(10, 20, 300, 400), opacity=0.8, shown=True)
    original.players.append(PlayerInfo(name="Xantik", server="green", level=54))
    original.triggers.append(
        Trigger(
            trigger_name="Journeyman Boots",
            search_text="Your feet feel quick\\.",
            timer=TriggerTimer(timer_name="JBoots", minutes=18),
        )
    )
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded == original
    assert loaded.windows["maps"].geometry == (10, 20, 300, 400)
    assert loaded.triggers[0].trigger_id == original.triggers[0].trigger_id


def test_window_state_dict_handling(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.windows["spells"] = WindowState(clickthrough=True, always_on_top=False)
    settings.windows["dps"] = WindowState()  # geometry stays None
    save_settings(settings, path)
    loaded = load_settings(path)
    assert set(loaded.windows) == {"spells", "dps"}
    assert loaded.windows["spells"].clickthrough is True
    assert loaded.windows["spells"].always_on_top is False
    assert loaded.windows["dps"].geometry is None
    # Unknown window keys are the norm: the dict accepts any name.
    loaded.windows["brand_new_overlay"] = WindowState(shown=True)
    save_settings(loaded, path)
    assert load_settings(path).windows["brand_new_overlay"].shown is True


def test_atomic_write_leaves_valid_json_and_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    settings = Settings()
    settings.maps.last_zone = "gfaydark"
    save_settings(settings, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["maps"]["last_zone"] == "gfaydark"
    assert data["schema_version"] == 1
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


def test_load_missing_file_returns_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # ensure no stray legacy config in CWD is picked up
    loaded = load_settings(tmp_path / "does-not-exist.json")
    assert loaded == Settings()


def test_load_corrupt_file_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_get_player_find_or_create() -> None:
    settings = Settings()
    first = get_player(settings, "Xantik", "green")
    assert settings.players == [first]
    again = get_player(settings, "Xantik", "green")
    assert again is first
    other_server = get_player(settings, "Xantik", "blue")
    assert other_server is not first
    assert len(settings.players) == 2


def test_debounced_saver_coalesces_bursts() -> None:
    calls: list[float] = []
    done = threading.Event()

    def record() -> None:
        calls.append(time.monotonic())
        done.set()

    saver = DebouncedSaver(record, delay=0.05)
    for _ in range(10):
        saver.request_save()
    assert calls == []  # nothing until the delay elapses
    assert done.wait(timeout=2.0)
    time.sleep(0.1)  # allow any (incorrect) extra timers to fire
    assert len(calls) == 1


def test_debounced_saver_flush_runs_pending_save_immediately() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=60.0)
    saver.request_save()
    saver.flush()
    assert calls == [1]
    saver.flush()  # nothing pending: no double save
    assert calls == [1]


def test_debounced_saver_cancel_discards_pending_save() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=0.05)
    saver.request_save()
    saver.cancel()
    time.sleep(0.15)
    assert calls == []


def test_debounced_saver_thread_safety() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=0.02)

    def hammer() -> None:
        for _ in range(50):
            saver.request_save()

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    saver.flush()
    time.sleep(0.1)
    # 400 requests must coalesce into a handful of saves at most (usually 1).
    assert 1 <= len(calls) <= 5
