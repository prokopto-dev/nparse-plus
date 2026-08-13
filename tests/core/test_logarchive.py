"""Tests for the log archive sweep."""

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from nparseplus.core.background import run_inline
from nparseplus.core.logarchive import LogArchiveService, archive_oversized_logs
from nparseplus.core.logfile import LogTail, find_active_log


def _make_log(path: Path, size: int) -> None:
    path.write_bytes(b"x" * size)


def test_archives_only_oversized_logs(tmp_path: Path) -> None:
    big = tmp_path / "eqlog_Big_P1999Green.txt"
    small = tmp_path / "eqlog_Small_P1999Green.txt"
    _make_log(big, 2 * 1024 * 1024)
    _make_log(small, 1024)

    archived = archive_oversized_logs(tmp_path, threshold_mb=1)

    assert len(archived) == 1
    assert archived[0].parent == tmp_path / "archive"
    assert archived[0].name.startswith("eqlog_Big_P1999Green_")
    assert archived[0].stat().st_size == 2 * 1024 * 1024
    # The log itself stays where it is, emptied — see the module docstring:
    # moving it out from under a client that holds it open is what #87 was.
    assert big.exists()
    assert big.stat().st_size == 0
    assert small.stat().st_size == 1024


def test_an_emptied_log_keeps_its_mtime(tmp_path: Path) -> None:
    # Otherwise a stale character's truncated log becomes the most recently
    # modified one and find_active_log pulls the driver off the live log.
    stale = tmp_path / "eqlog_Stale_P1999Green.txt"
    live = tmp_path / "eqlog_Live_P1999Green.txt"
    _make_log(stale, 2 * 1024 * 1024)
    _make_log(live, 1024)
    os.utime(stale, (1_000_000, 1_000_000))

    archive_oversized_logs(tmp_path, threshold_mb=1)

    assert stale.stat().st_mtime == 1_000_000
    assert find_active_log(tmp_path) == live


def test_archiving_keeps_the_tail_alive_while_the_game_holds_the_log(tmp_path: Path) -> None:
    """#87: the repro — EQ under Wine holds the log open for append."""
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text("[Wed Jul 15 21:00:00 2026] Welcome to EverQuest!\n")
    game = log.open("a", buffering=1)
    try:
        tail = LogTail.attach(log)
        game.write("[Wed Jul 15 21:00:01 2026] You slash a lava defender.\n")
        game.write("x" * (2 * 1024 * 1024) + "\n")
        game.flush()
        assert tail.poll()  # the app is reading, as it would be

        assert len(archive_oversized_logs(tmp_path, threshold_mb=1)) == 1

        game.write("[Wed Jul 15 21:05:00 2026] Lord Nagafen hits YOU for 900 points.\n")
        game.flush()

        # Still one log to attach to, and it is still the one we are reading.
        assert find_active_log(tmp_path) == log
        assert [line for line in tail.poll() if "Nagafen" in line]
    finally:
        game.close()


def test_a_log_refilled_past_our_offset_still_reaches_the_tail(tmp_path: Path) -> None:
    """The sweep empties the log from its own thread, so the client can refill
    it past our read offset before the next 100 ms poll — at which point the
    file is no longer *smaller* than where we left off."""
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text("[Wed Jul 15 21:00:00 2026] You slash a lava defender.\n" * 21_000)
    game = log.open("a", buffering=1)
    try:
        tail = LogTail.attach(log)
        assert tail.poll() == []  # caught up, as the driver would be
        offset = tail.position

        assert len(archive_oversized_logs(tmp_path, threshold_mb=1)) == 1

        game.write("[Wed Jul 15 21:05:00 2026] Gorenaire begins to cast a spell.\n")
        game.write("[Wed Jul 15 21:05:01 2026] You slash Gorenaire.\n" * 30_000)
        game.flush()
        assert log.stat().st_size > offset  # regrown past us: no shrink to see

        lines = tail.poll()
    finally:
        game.close()

    # The first line after the sweep is the one a resume mid-file would eat.
    assert lines[0].endswith("Gorenaire begins to cast a spell.")
    assert len(lines) == 30_001


def test_a_log_refilled_to_exactly_our_offset_still_reaches_the_tail(tmp_path: Path) -> None:
    """The boundary of the case above: the refill can land on the offset
    exactly, where the size says nothing at all."""
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    line = "[Wed Jul 15 21:00:00 2026] You slash a lava defender.\n"
    log.write_text(line * 21_000)
    game = log.open("a", buffering=1)
    try:
        tail = LogTail.attach(log)
        assert tail.poll() == []
        offset = tail.position

        assert len(archive_oversized_logs(tmp_path, threshold_mb=1)) == 1

        refill = "[Wed Jul 15 21:05:00 2026] Gorenaire begins to cast a spell.\n"
        while len(refill) + len(line) < offset:
            refill += line
        refill += "x" * (offset - len(refill) - 1) + "\n"
        assert len(refill) == offset
        game.write(refill)
        game.flush()
        assert log.stat().st_size == offset  # exactly where we left off

        lines = tail.poll()
    finally:
        game.close()

    assert lines[0].endswith("Gorenaire begins to cast a spell.")


def test_lines_written_during_the_copy_still_reach_the_archive(tmp_path: Path) -> None:
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    _make_log(log, 2 * 1024 * 1024)
    game = log.open("a", buffering=1)
    try:
        real_copy = shutil.copyfileobj

        def copy_then_write(src, dst, *args, **kwargs):
            real_copy(src, dst, *args, **kwargs)
            game.write("[Wed Jul 15 21:05:00 2026] Gorenaire begins to cast a spell.\n")
            game.flush()

        with mock.patch.object(shutil, "copyfileobj", copy_then_write):
            archived = archive_oversized_logs(tmp_path, threshold_mb=1)
    finally:
        game.close()

    assert "Gorenaire" in archived[0].read_text()
    assert log.stat().st_size == 0


def test_a_log_we_cannot_write_is_skipped_whole(tmp_path: Path) -> None:
    # Stands in for the Windows share-mode refusal: nothing copied, nothing
    # emptied, no half-done archive left in the folder.
    if getattr(os, "geteuid", lambda: 1)() == 0:
        pytest.skip("root ignores the read-only bit")
    big = tmp_path / "eqlog_Big_P1999Green.txt"
    _make_log(big, 2 * 1024 * 1024)
    big.chmod(0o444)
    try:
        assert archive_oversized_logs(tmp_path, threshold_mb=1) == []
    finally:
        big.chmod(0o644)
    assert big.stat().st_size == 2 * 1024 * 1024
    assert list((tmp_path / "archive").glob("*")) == []


def test_zero_threshold_is_noop(tmp_path: Path) -> None:
    _make_log(tmp_path / "eqlog_A_P1999Green.txt", 4096)
    assert archive_oversized_logs(tmp_path, threshold_mb=0) == []


def test_service_respects_enabled_flag(tmp_path: Path) -> None:
    log = tmp_path / "eqlog_A_P1999Green.txt"
    _make_log(log, 2 * 1024 * 1024)
    service = LogArchiveService(
        get_log_dir=lambda: tmp_path,
        is_enabled=lambda: False,
        get_threshold_mb=lambda: 1,
        spawn=run_inline,
    )
    service.tick(datetime.now())
    assert log.stat().st_size == 2 * 1024 * 1024

    enabled_service = LogArchiveService(
        get_log_dir=lambda: tmp_path,
        is_enabled=lambda: True,
        get_threshold_mb=lambda: 1,
        spawn=run_inline,
    )
    enabled_service.tick(datetime.now())
    assert log.stat().st_size == 0
    # second tick inside the hourly window is a no-op (no error, no rescan)
    enabled_service.tick(datetime.now())


def test_the_sweep_runs_off_the_calling_thread(tmp_path: Path) -> None:
    # Copying a 100 MB log is not something to do on the thread that tails it.
    log = tmp_path / "eqlog_A_P1999Green.txt"
    _make_log(log, 2 * 1024 * 1024)
    service = LogArchiveService(
        get_log_dir=lambda: tmp_path,
        is_enabled=lambda: True,
        get_threshold_mb=lambda: 1,
    )
    service.tick(datetime.now())
    assert service._job.wait(timeout=10.0)
    assert log.stat().st_size == 0

    # And the tick that scheduled it did no file work itself.
    idents: list[int] = []
    watched = LogArchiveService(
        get_log_dir=lambda: idents.append(threading.get_ident()) or tmp_path,
        is_enabled=lambda: True,
        get_threshold_mb=lambda: 1,
        spawn=lambda _name, _work: None,  # never runs the sweep
    )
    watched.tick(datetime.now())
    assert idents == [threading.get_ident()]  # only the settings read
    assert log.stat().st_size == 0  # untouched by the tick
