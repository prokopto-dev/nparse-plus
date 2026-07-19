"""Unit tests for LogDriver's character-switch detection.

``_maybe_switch_log`` is synchronous, so it can be exercised directly without
starting the driver's worker thread. This guards the character-switch path
(CLAUDE.md notes it has regressed before) which previously had no tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from nparseplus.core.bus import EventBus
from nparseplus.core.driver import LOG_SWITCH_CHECK_S, LogDriver
from nparseplus.core.events import AfterPlayerChangedEvent, BeforePlayerChangedEvent
from nparseplus.core.player import ActivePlayer


class _StubPipeline:
    """_maybe_switch_log never touches the pipeline; this stands in for it."""

    def process(self, raw: str) -> None:  # pragma: no cover - unused here
        pass


def _write_log(directory: Path, name: str, mtime: float, body: bytes = b"") -> None:
    path = directory / name
    path.write_bytes(body)
    os.utime(path, (mtime, mtime))


def _make_driver(tmp_path: Path):
    bus = EventBus()
    events: list[object] = []
    bus.subscribe_all(events.append)
    player = ActivePlayer()
    driver = LogDriver(tmp_path, _StubPipeline(), bus, player)
    return driver, player, events


def test_attaches_to_the_newest_log(tmp_path: Path) -> None:
    _write_log(tmp_path, "eqlog_Alice_P1999Green.txt", mtime=1000)
    _write_log(tmp_path, "eqlog_Bob_P1999Green.txt", mtime=2000)
    driver, player, events = _make_driver(tmp_path)

    driver._maybe_switch_log()

    assert driver._tail is not None
    assert driver._tail.path.name == "eqlog_Bob_P1999Green.txt"
    assert player.name == "Bob"
    # First attach: the player was unconfigured, so only the After event fires.
    assert [type(e) for e in events] == [AfterPlayerChangedEvent]


def test_switch_emits_before_and_after_when_already_configured(tmp_path: Path) -> None:
    _write_log(tmp_path, "eqlog_Alice_P1999Green.txt", mtime=1000)
    driver, player, events = _make_driver(tmp_path)
    driver._maybe_switch_log()  # attach Alice
    events.clear()

    # A newer log appears; step past the throttle window so the switch runs.
    _write_log(tmp_path, "eqlog_Bob_P1999Green.txt", mtime=3000)
    driver._last_switch_check -= LOG_SWITCH_CHECK_S + 1
    driver._maybe_switch_log()

    assert driver._tail.path.name == "eqlog_Bob_P1999Green.txt"
    assert player.name == "Bob"
    assert [type(e) for e in events] == [BeforePlayerChangedEvent, AfterPlayerChangedEvent]


def test_switch_is_throttled_within_the_check_window(tmp_path: Path) -> None:
    _write_log(tmp_path, "eqlog_Alice_P1999Green.txt", mtime=1000)
    driver, player, events = _make_driver(tmp_path)
    driver._maybe_switch_log()  # attach Alice, stamps _last_switch_check
    events.clear()

    # A newer log appears immediately; without advancing past the throttle the
    # driver must NOT re-scan or switch yet.
    _write_log(tmp_path, "eqlog_Bob_P1999Green.txt", mtime=3000)
    driver._maybe_switch_log()

    assert driver._tail.path.name == "eqlog_Alice_P1999Green.txt"
    assert player.name == "Alice"
    assert events == []


def test_unparseable_filenames_are_ignored(tmp_path: Path) -> None:
    _write_log(tmp_path, "notalog.txt", mtime=5000)
    _write_log(tmp_path, "eqlog_Alice_P1999Green.txt", mtime=1000)
    driver, player, _ = _make_driver(tmp_path)

    driver._maybe_switch_log()

    assert driver._tail is not None
    assert driver._tail.path.name == "eqlog_Alice_P1999Green.txt"
    assert player.name == "Alice"


def test_no_logs_leaves_the_driver_unattached(tmp_path: Path) -> None:
    driver, player, events = _make_driver(tmp_path)

    driver._maybe_switch_log()

    assert driver._tail is None
    assert player.name == ""
    assert events == []
