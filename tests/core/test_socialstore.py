"""core.socialstore — the per-character socials mirror and provenance."""

import json
from datetime import datetime
from pathlib import Path

from nparseplus.core.socials import Social, SocialGrid
from nparseplus.core.socialstore import (
    SCHEMA_VERSION,
    SocialOrigin,
    digest,
    forget_slots,
    load_store,
    mark_written,
    new_store,
    restore_payload,
    save_store,
    store_path,
    sync_from_game,
)

NOW = datetime(2026, 7, 29, 12, 0, 0)
LATER = datetime(2026, 7, 29, 13, 0, 0)


def _grid(*socials: Social) -> SocialGrid:
    return SocialGrid(socials=list(socials))


def _assist(name: str = "Assist", lines: list[str] | None = None) -> Social:
    return Social(page=1, button=1, name=name, lines=lines or ["/assist"])


# -- Persistence -------------------------------------------------------------


def test_store_path_sanitizes_the_filename(tmp_path: Path) -> None:
    assert store_path(tmp_path, "Xantik", "P1999Green").name == "Xantik_P1999Green.json"
    assert store_path(tmp_path, "../evil", "P1999Green").name == "evil_P1999Green.json"


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = store_path(tmp_path, "Xantik", "P1999Green")
    store = new_store("Xantik", "P1999Green", now=NOW)
    sync_from_game(store, _grid(_assist()), now=NOW)
    save_store(path, store)

    loaded = load_store(path)
    assert loaded is not None
    assert loaded.character == "Xantik"
    assert [record.slot for record in loaded.records] == [(1, 1)]
    assert not list(path.parent.glob("*.tmp"))  # atomic write left no debris


def test_load_store_is_forgiving(tmp_path: Path) -> None:
    assert load_store(tmp_path / "missing.json") is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert load_store(corrupt) is None

    wrong_shape = tmp_path / "list.json"
    wrong_shape.write_text("[1, 2, 3]")
    assert load_store(wrong_shape) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"records": [{"social": {"page": "nope"}}]}))
    assert load_store(invalid) is None


def test_load_store_ignores_a_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1, "records": []}))
    assert load_store(path) is None


# -- Sync --------------------------------------------------------------------


def test_sync_adds_unseen_slots_as_game_origin() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    report = sync_from_game(store, _grid(_assist()), now=NOW)
    assert report.added == [(1, 1)]
    assert store.origin_at(1, 1) is SocialOrigin.GAME


def test_sync_keeps_local_origin_when_the_slot_still_matches_what_we_wrote() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    social = _assist()
    mark_written(store, [social], origin=SocialOrigin.LOCAL, now=NOW)

    report = sync_from_game(store, _grid(social), now=LATER)
    assert report.unchanged == [(1, 1)]
    assert store.origin_at(1, 1) is SocialOrigin.LOCAL


def test_sync_flips_to_game_when_the_slot_changed_behind_us() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    mark_written(store, [_assist()], origin=SocialOrigin.LOCAL, now=NOW)

    edited = _assist(lines=["/assist", "/pet attack"])
    report = sync_from_game(store, _grid(edited), now=LATER)
    assert report.changed == [(1, 1)]
    assert store.origin_at(1, 1) is SocialOrigin.GAME
    assert store.at(1, 1).written_digest == ""


def test_sync_marks_vanished_slots_as_lost_without_dropping_them() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    mark_written(store, [_assist()], origin=SocialOrigin.LOCAL, now=NOW)

    report = sync_from_game(store, _grid(), now=LATER)
    assert report.lost == [(1, 1)]
    assert [record.slot for record in store.lost()] == [(1, 1)]
    # Still recoverable.
    assert [s.name for s in restore_payload(store)] == ["Assist"]


def test_sync_restores_the_in_file_flag_when_a_slot_comes_back() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    social = _assist()
    mark_written(store, [social], origin=SocialOrigin.LOCAL, now=NOW)
    sync_from_game(store, _grid(), now=LATER)
    assert store.lost()

    sync_from_game(store, _grid(social), now=LATER)
    assert store.lost() == []
    assert store.origin_at(1, 1) is SocialOrigin.LOCAL


def test_sync_records_a_snapshot_of_the_section() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    sync_from_game(store, _grid(_assist()), now=NOW)
    assert "Page1Button1Name=Assist" in store.snapshot
    assert "Page1Button1Line1=/assist" in store.snapshot


# -- Marking writes ----------------------------------------------------------


def test_mark_written_stamps_digest_origin_and_time() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    social = _assist()
    mark_written(store, [social], origin=SocialOrigin.IMPORTED, now=NOW, source_label="Beeta")

    record = store.at(1, 1)
    assert record.origin is SocialOrigin.IMPORTED
    assert record.source_label == "Beeta"
    assert record.written_digest == digest(social)
    assert record.updated_at == NOW


def test_mark_written_can_override_origin_per_slot() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    edited = _assist()
    from_pack = Social(page=2, button=1, name="Pack", lines=["/pack"])
    mark_written(
        store,
        [edited, from_pack],
        origin=SocialOrigin.IMPORTED,
        now=NOW,
        origins={(1, 1): SocialOrigin.LOCAL},
    )
    assert store.origin_at(1, 1) is SocialOrigin.LOCAL
    assert store.origin_at(2, 1) is SocialOrigin.IMPORTED


def test_forget_slots_drops_cleared_records() -> None:
    store = new_store("Xantik", "P1999Green", now=NOW)
    mark_written(store, [_assist()], origin=SocialOrigin.LOCAL, now=NOW)
    forget_slots(store, [(1, 1)], now=LATER)
    assert store.records == []


def test_digest_ignores_incidental_whitespace() -> None:
    assert digest(_assist(lines=["/assist"])) == digest(_assist(lines=["  /assist  "]))
    assert digest(_assist()) != digest(_assist(name="Other"))
