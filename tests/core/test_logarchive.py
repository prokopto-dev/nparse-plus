"""Tests for the log archive sweep."""

from datetime import datetime
from pathlib import Path

from nparseplus.core.logarchive import LogArchiveService, archive_oversized_logs


def _make_log(path: Path, size: int) -> None:
    path.write_bytes(b"x" * size)


def test_archives_only_oversized_logs(tmp_path: Path) -> None:
    big = tmp_path / "eqlog_Big_P1999Green.txt"
    small = tmp_path / "eqlog_Small_P1999Green.txt"
    _make_log(big, 2 * 1024 * 1024)
    _make_log(small, 1024)

    moved = archive_oversized_logs(tmp_path, threshold_mb=1)

    assert len(moved) == 1
    assert moved[0].parent == tmp_path / "archive"
    assert moved[0].name.startswith("eqlog_Big_P1999Green_")
    assert not big.exists()
    assert small.exists()


def test_zero_threshold_is_noop(tmp_path: Path) -> None:
    _make_log(tmp_path / "eqlog_A_P1999Green.txt", 4096)
    assert archive_oversized_logs(tmp_path, threshold_mb=0) == []


def test_service_respects_enabled_flag(tmp_path: Path) -> None:
    _make_log(tmp_path / "eqlog_A_P1999Green.txt", 2 * 1024 * 1024)
    service = LogArchiveService(
        get_log_dir=lambda: tmp_path,
        is_enabled=lambda: False,
        get_threshold_mb=lambda: 1,
    )
    service.tick(datetime.now())
    assert (tmp_path / "eqlog_A_P1999Green.txt").exists()

    enabled_service = LogArchiveService(
        get_log_dir=lambda: tmp_path,
        is_enabled=lambda: True,
        get_threshold_mb=lambda: 1,
    )
    enabled_service.tick(datetime.now())
    assert not (tmp_path / "eqlog_A_P1999Green.txt").exists()
    # second tick inside the hourly window is a no-op (no error, no rescan)
    enabled_service.tick(datetime.now())
