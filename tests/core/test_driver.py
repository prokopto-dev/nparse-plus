"""Unit tests for LogDriver's character-switch detection and tick supervision.

``_maybe_switch_log`` is synchronous, so it can be exercised directly without
starting the driver's worker thread. This guards the character-switch path
(CLAUDE.md notes it has regressed before) which previously had no tests.
The tick tests drive ``_run_supervised_ticks`` the same way — synchronously,
with a monkeypatched clock, so nothing has to actually sleep.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from nparseplus.core import driver as driver_module
from nparseplus.core.bus import EventBus
from nparseplus.core.driver import (
    LOG_SWITCH_CHECK_S,
    TICK_BREACH_LIMIT,
    TICK_BUDGET_S,
    LogDriver,
)
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


# --- tick supervision -------------------------------------------------------


class FakeClock:
    """Stands in for the driver module's ``time``: a hand-cranked clock.

    Ticks advance it themselves, so "this callback took 400 ms" costs the
    test nothing. ``monotonic`` stays real — only tick timing is faked.
    """

    monotonic = staticmethod(time.monotonic)

    def __init__(self) -> None:
        self.now = 0.0
        self.perf_counter_calls = 0

    def perf_counter(self) -> float:
        self.perf_counter_calls += 1
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(driver_module, "time", fake)
    return fake


def run_ticks(driver: LogDriver, iterations: int) -> None:
    """Drive the loop's tick section directly, as _run does."""
    for _ in range(iterations):
        driver._run_supervised_ticks(datetime.now())


def test_fast_supervised_tick_survives_many_iterations(tmp_path: Path, clock: FakeClock) -> None:
    driver, _player, _events = _make_driver(tmp_path)
    ran: list[datetime] = []

    def fast(now: datetime) -> None:
        clock.advance(0.001)
        ran.append(now)

    driver.add_supervised_tick(fast, label="plugin fast")

    run_ticks(driver, 200)

    assert len(ran) == 200
    assert len(driver.on_tick) == 1


def test_slow_supervised_tick_is_dropped_and_the_loop_continues(
    tmp_path: Path, clock: FakeClock
) -> None:
    driver, _player, _events = _make_driver(tmp_path)
    slow_runs: list[datetime] = []
    neighbour_runs: list[datetime] = []
    dropped: list[str] = []

    def slow(now: datetime) -> None:
        slow_runs.append(now)
        clock.advance(TICK_BUDGET_S * 2)

    def neighbour(now: datetime) -> None:
        neighbour_runs.append(now)

    driver.add_supervised_tick(slow, label="plugin hog", on_dropped=dropped.append)
    driver.add_supervised_tick(neighbour, label="plugin polite")

    run_ticks(driver, 10)

    # Dropped the moment it hit the consecutive-breach limit, and never again.
    assert len(slow_runs) == TICK_BREACH_LIMIT
    assert slow not in driver.on_tick
    assert dropped and "removed" in dropped[0]
    # Everything else in the loop is untouched.
    assert len(neighbour_runs) == 10
    assert driver.on_tick == [neighbour]


def test_isolated_slow_runs_do_not_drop_a_tick(tmp_path: Path, clock: FakeClock) -> None:
    """One-off breaches (a GC pause, a cold import) must not evict a plugin."""
    driver, _player, _events = _make_driver(tmp_path)
    runs = 0
    dropped: list[str] = []

    def occasionally_slow(now: datetime) -> None:
        nonlocal runs
        runs += 1
        clock.advance(TICK_BUDGET_S * 2 if runs % 2 else 0.001)

    driver.add_supervised_tick(occasionally_slow, label="plugin blippy", on_dropped=dropped.append)

    run_ticks(driver, 40)

    assert runs == 40
    assert dropped == []
    assert occasionally_slow in driver.on_tick


def test_builtin_ticks_are_never_dropped(tmp_path: Path, clock: FakeClock) -> None:
    """App-owned ticks (timers, sharing) are ours: bounded means broken."""
    driver, _player, _events = _make_driver(tmp_path)
    runs: list[datetime] = []

    def builtin(now: datetime) -> None:
        runs.append(now)
        clock.advance(TICK_BUDGET_S * 10)

    driver.on_tick.append(builtin)  # how composition.py registers them
    # A supervised tick alongside, so the timing path is actually in play.
    driver.add_supervised_tick(lambda now: None, label="plugin fast")

    run_ticks(driver, 20)

    assert len(runs) == 20
    assert builtin in driver.on_tick


def test_remove_tick_deregisters_supervision(tmp_path: Path, clock: FakeClock) -> None:
    driver, _player, _events = _make_driver(tmp_path)

    def tick(now: datetime) -> None:
        clock.advance(TICK_BUDGET_S * 2)

    driver.add_supervised_tick(tick, label="plugin gone")
    driver.remove_tick(tick)
    assert driver.on_tick == [] and driver._supervised == {}
    driver.remove_tick(tick)  # idempotent: unwind after an automatic drop


def test_no_supervised_ticks_never_reads_the_clock(tmp_path: Path, clock: FakeClock) -> None:
    """The no-plugin case must not pay for the instrumentation at all."""
    driver, _player, _events = _make_driver(tmp_path)
    ran = threading.Event()
    driver.on_tick.append(lambda now: ran.set())

    driver.start()
    try:
        assert ran.wait(2), "driver never ticked"
    finally:
        driver.stop()

    assert clock.perf_counter_calls == 0


# -- the log-archive handoff (#87) -------------------------------------------


def test_note_log_rotated_reads_the_tail_from_the_top(tmp_path: Path) -> None:
    """core.logarchive tells us on the tick, right after it empties the log.

    Detection cannot cover this alone: a client that refills the emptied log
    to the tail's offset before the next poll is neither smaller nor — EQ
    repeats identical lines — different at that offset.
    """
    body = b"[Wed Jul 15 21:00:00 2026] You slash a lava defender.\n" * 200
    _write_log(tmp_path, "eqlog_Alice_P1999Green.txt", mtime=1000, body=body)
    driver, _player, _events = _make_driver(tmp_path)
    driver._maybe_switch_log()
    log = tmp_path / "eqlog_Alice_P1999Green.txt"
    assert driver._tail is not None and driver._tail.position == len(body)

    driver.note_log_rotated(log)

    assert driver._tail.position == 0


def test_note_log_rotated_ignores_a_log_we_are_not_tailing(tmp_path: Path) -> None:
    _write_log(tmp_path, "eqlog_Alice_P1999Green.txt", mtime=1000, body=b"x" * 64)
    driver, _player, _events = _make_driver(tmp_path)
    driver._maybe_switch_log()
    at = driver._tail.position

    driver.note_log_rotated(tmp_path / "eqlog_Someone_P1999Green.txt")

    assert driver._tail.position == at


def test_note_log_rotated_matches_however_the_path_was_spelled(tmp_path: Path) -> None:
    _write_log(tmp_path, "eqlog_Alice_P1999Green.txt", mtime=1000, body=b"x" * 64)
    driver, _player, _events = _make_driver(tmp_path)
    driver._maybe_switch_log()

    # The archiver builds its path from the settings, the driver from its own
    # log_dir; the same file can reach them spelled differently.
    driver.note_log_rotated(tmp_path / "." / "eqlog_Alice_P1999Green.txt")

    assert driver._tail.position == 0


def test_note_log_rotated_before_any_tail_is_harmless(tmp_path: Path) -> None:
    driver, _player, _events = _make_driver(tmp_path)
    driver.note_log_rotated(tmp_path / "eqlog_Alice_P1999Green.txt")  # no tail yet
