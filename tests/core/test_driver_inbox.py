"""The driver-thread command inbox: registration changes from other threads.

Everything here is driven by hand — submit, then run exactly one iteration —
so nothing waits on the 100 ms poll. ``loop_running`` gives the driver a real
live thread so ``is_running()`` answers through the real predicate, without
the loop actually running; the test itself stands in for the driver thread by
calling ``_iterate``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.driver import LogDriver
from nparseplus.core.parsers.base import LineInfo, ParseContext
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer


class _RecordingPipeline:
    """Records the order lines were processed in, relative to commands."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.submit: Callable[..., None] | None = None
        self.on_line: Callable[[str], None] | None = None

    def set_command_sink(self, submit: Callable[..., None]) -> None:
        self.submit = submit

    def process(self, raw: str) -> None:
        self.log.append(f"line {raw}")
        if self.on_line is not None:
            self.on_line(raw)


class _FakeTail:
    """Hands the driver a fixed batch of lines on the next poll."""

    def __init__(self, lines: list[str]) -> None:
        self.path = Path("eqlog_Alice_P1999Green.txt")
        self._lines = list(lines)

    def poll(self) -> list[str]:
        lines, self._lines = self._lines, []
        return lines


def _make_driver(tmp_path: Path, pipeline: object | None = None):
    log: list[str] = []
    pipeline = pipeline if pipeline is not None else _RecordingPipeline(log)
    driver = LogDriver(tmp_path, pipeline, EventBus(), ActivePlayer())
    return driver, pipeline, log


@contextmanager
def loop_running(driver: LogDriver) -> Iterator[None]:
    """Make ``is_running()`` true without starting the poll loop.

    A real thread — the predicate is the real one — parked on an event. The
    test drives ``_iterate`` itself, which is what makes every assertion here
    deterministic.
    """
    release = threading.Event()
    thread = threading.Thread(target=release.wait, name="log-driver", daemon=True)
    thread.start()
    driver._thread = thread
    try:
        yield
    finally:
        release.set()
        thread.join(timeout=2)
        driver._thread = None


def off_thread(fn: Callable[[], None]) -> None:
    """Run ``fn`` to completion on a thread that is not the driver's."""
    caller = threading.Thread(target=fn, name="submitter")
    caller.start()
    caller.join(timeout=2)


def submit_from_another_thread(driver: LogDriver, fn: Callable[[], None], *, label: str) -> None:
    """Enqueue the way the GUI thread does: not from the driver thread."""
    off_thread(lambda: driver.submit_to_driver(fn, label=label))


def test_a_submitted_command_runs_on_the_driver_thread_exactly_once(tmp_path: Path) -> None:
    driver, _pipeline, _log = _make_driver(tmp_path)
    ran_on: list[threading.Thread] = []

    with loop_running(driver):
        submit_from_another_thread(
            driver, lambda: ran_on.append(threading.current_thread()), label="hot registration"
        )
        assert ran_on == []  # queued, not run by the caller

        driver._iterate()
        assert ran_on == [threading.current_thread()]

        driver._iterate()  # drained means gone
        assert len(ran_on) == 1


def test_commands_land_between_lines_never_inside_one(tmp_path: Path) -> None:
    """The drain point is the contract: a batch of lines is never split.

    The submit comes from the pipeline itself — a parser, or a plugin handler
    the bus called — which is the closest a command can get to landing
    mid-line.
    """
    driver, pipeline, log = _make_driver(tmp_path)
    driver._tail = _FakeTail(["one", "two", "three"])
    pipeline.on_line = lambda raw: (
        submit_from_another_thread(driver, lambda: log.append("command"), label="mid-batch")
        if raw == "one"
        else None
    )

    with loop_running(driver):
        driver._iterate()
        assert log == ["line one", "line two", "line three"]

        driver._tail = _FakeTail(["four"])
        driver._iterate()

    assert log == ["line one", "line two", "line three", "command", "line four"]


def test_a_raising_command_is_logged_and_the_loop_keeps_going(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    driver, _pipeline, _log = _make_driver(tmp_path)
    ran: list[str] = []

    def boom() -> None:
        raise RuntimeError("plugin registration blew up")

    with loop_running(driver), caplog.at_level("ERROR"):
        submit_from_another_thread(driver, boom, label="bad command")
        submit_from_another_thread(driver, lambda: ran.append("after"), label="good command")

        driver._iterate()  # must not raise

    assert ran == ["after"]  # the raise did not eat the rest of the inbox
    assert "bad command" in caplog.text
    assert "plugin registration blew up" in caplog.text


def test_tick_registration_is_routed_while_the_loop_runs(tmp_path: Path) -> None:
    driver, _pipeline, _log = _make_driver(tmp_path)
    ticked: list[datetime] = []

    def tick(now: datetime) -> None:
        ticked.append(now)

    with loop_running(driver):
        driver.add_supervised_tick(tick, label="plugin late")
        assert driver.on_tick == []  # not yet — the loop owns this list

        driver._iterate()
        assert driver.on_tick == [tick] and len(ticked) == 1

        driver.remove_tick(tick)
        assert driver.on_tick == [tick]

        driver._iterate()

    assert driver.on_tick == [] and driver._supervised == {}
    assert len(ticked) == 1  # removed before the second iteration's ticks ran


def test_registration_is_immediate_with_no_loop_running(tmp_path: Path) -> None:
    """Startup wiring and shutdown unwind stay synchronous: no thread to race."""
    driver, _pipeline, _log = _make_driver(tmp_path)

    def tick(now: datetime) -> None:  # pragma: no cover - never called
        pass

    driver.add_supervised_tick(tick, label="plugin at startup")
    assert driver.on_tick == [tick] and driver.is_running() is False

    driver.remove_tick(tick)
    assert driver.on_tick == []


class _NoopParser:
    """Structurally a LineParser; the chain never actually runs it here."""

    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:  # pragma: no cover
        return False


def _pipeline_with_driver(tmp_path: Path) -> tuple[LogPipeline, LogDriver]:
    bus = EventBus()
    pipeline = LogPipeline([], ParseContext(bus=bus, player=ActivePlayer()))
    driver = LogDriver(tmp_path, pipeline, bus, ActivePlayer())
    return pipeline, driver


def test_parser_changes_are_routed_through_the_driver(tmp_path: Path) -> None:
    pipeline, driver = _pipeline_with_driver(tmp_path)
    parser = _NoopParser()

    with loop_running(driver):
        off_thread(lambda: pipeline.append_parser(parser))
        assert pipeline._parsers == []

        driver._iterate()
        assert pipeline._parsers == [parser]

        pipeline.remove_parser(parser)
        assert pipeline._parsers == [parser]

        driver._iterate()

    assert pipeline._parsers == []


def test_a_pipeline_with_no_sink_mutates_immediately(tmp_path: Path) -> None:
    """Replay harnesses and the SDK's fakes drive no thread; nothing changes."""
    bus = EventBus()
    pipeline = LogPipeline([], ParseContext(bus=bus, player=ActivePlayer()))
    parser = _NoopParser()

    pipeline.append_parser(parser)
    assert pipeline._parsers == [parser]

    pipeline.remove_parser(parser)
    pipeline.remove_parser(parser)  # absent: still a no-op
    assert pipeline._parsers == []
