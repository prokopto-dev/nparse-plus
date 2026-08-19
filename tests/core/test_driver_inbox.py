"""The driver-thread command inbox: registration changes from other threads.

Everything here is driven by hand — submit, then run exactly one iteration —
so nothing waits on the 100 ms poll. ``accepting_commands`` puts the driver in
the state ``start()`` leaves it in without the loop running, and the test
itself stands in for the driver thread by calling ``_iterate``. The shutdown
race at the bottom is the exception: it needs the real ``start``/``stop``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from queue import Empty

import pytest

from nparseplus.core import driver as driver_module
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
def accepting_commands(driver: LogDriver) -> Iterator[None]:
    """The state ``start()`` leaves the driver in, without the poll loop.

    The loop is what would otherwise drain on its own schedule; the test
    drives ``_iterate`` instead, which is what makes every assertion here
    deterministic.
    """
    with driver._command_lock:
        driver._accepting_commands = True
    try:
        yield
    finally:
        with driver._command_lock:
            driver._accepting_commands = False


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

    with accepting_commands(driver):
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

    with accepting_commands(driver):
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

    with accepting_commands(driver), caplog.at_level("ERROR"):
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

    with accepting_commands(driver):
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
    assert driver.on_tick == [tick]

    driver.remove_tick(tick)
    assert driver.on_tick == []


class _SlowInbox:
    """The real inbox with ``put`` parked, so a submit can straddle ``stop``."""

    def __init__(self, inner, entered: threading.Event, release: threading.Event) -> None:
        self._inner = inner
        self._entered = entered
        self._release = release

    def put(self, item) -> None:
        self._entered.set()
        assert self._release.wait(2), "the test never released the enqueue"
        self._inner.put(item)

    def get_nowait(self):
        return self._inner.get_nowait()

    def empty(self) -> bool:
        return self._inner.empty()


def test_a_submit_in_flight_as_the_driver_exits_is_waited_for(tmp_path: Path) -> None:
    """The loop cannot shut the gate while a submit is halfway through it.

    The enqueue is parked mid-``put`` — the submitter is holding the gate
    lock — so the exiting driver has already run its final drain on an empty
    inbox and is blocked on that lock. Released, the command lands *after*
    that drain: the close has to re-check, and the driver has to run it,
    because by the time the gate shuts nothing else may touch the chain.
    """
    driver, _pipeline, _log = _make_driver(tmp_path)
    ran: list[str] = []
    entered, release = threading.Event(), threading.Event()
    driver._inbox = _SlowInbox(driver._inbox, entered, release)
    driver.start()

    submitter = threading.Thread(
        target=lambda: driver.submit_to_driver(
            lambda: ran.append(threading.current_thread().name), label="late registration"
        ),
        name="submitter",
    )
    submitter.start()
    assert entered.wait(2), "the submit never reached the enqueue"

    stopper = threading.Thread(target=driver.stop, name="stopper")
    stopper.start()
    release.set()  # the enqueue lands, behind the exiting loop's last drain
    submitter.join(timeout=2)
    stopper.join(timeout=2)

    assert ran == ["log-driver"]  # applied where the chain is owned
    assert driver._accepting_commands is False
    with pytest.raises(Empty):
        driver._inbox.get_nowait()  # nothing for a later start() to apply


def test_a_stalled_driver_keeps_its_commands_instead_of_handing_them_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``stop`` gives up on the join — and must NOT run the queue itself.

    Commands mutate the parser chain and the tick list, so a driver wedged in
    a slow tick is still the thread that owns them: it can resume and process
    a line while the stopping (GUI) thread is halfway through removing a
    parser. Being the only dequeuer is not being the only runner. So the
    queue stays put, the gate stays open, and the loop applies the work on
    its way out.
    """
    monkeypatch.setattr(driver_module, "STOP_JOIN_TIMEOUT_S", 0.05)
    driver, _pipeline, _log = _make_driver(tmp_path)
    ran: list[str] = []
    entered, release = threading.Event(), threading.Event()

    def wedged(now: datetime) -> None:
        entered.set()
        release.wait(5)

    driver.on_tick.append(wedged)
    driver.start()
    assert entered.wait(2), "the driver never reached the tick"

    driver.submit_to_driver(lambda: ran.append(threading.current_thread().name), label="stranded")
    with caplog.at_level("ERROR"):
        driver.stop()  # the join gives up; the loop is still in the tick

    assert ran == []  # not on this thread, beside a live driver
    assert driver._accepting_commands is True  # still the driver's to take
    assert "did not stop" in caplog.text

    release.set()  # the tick returns, the loop notices _stop and unwinds
    assert driver._thread is not None
    driver._thread.join(timeout=2)

    assert ran == ["log-driver"]  # applied where the chain is owned
    assert driver._accepting_commands is False  # shut by the thread, on its way out


def test_teardown_after_a_stalled_driver_finally_exits_is_not_stranded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """App quit, worst case: ``backend.stop`` gives up, then the loop exits.

    ``plugin_host.shutdown`` runs *after* ``backend.stop`` and unwinds its
    parsers and ticks through this seam. If the gate outlived the thread,
    those commands would queue with nobody left to run them — stranded until
    some later ``start()`` applied them out of nowhere. The thread shuts the
    gate as it goes, so they run on their caller instead.
    """
    monkeypatch.setattr(driver_module, "STOP_JOIN_TIMEOUT_S", 0.05)
    driver, _pipeline, _log = _make_driver(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def wedged(now: datetime) -> None:
        entered.set()
        release.wait(5)

    driver.on_tick.append(wedged)
    driver.start()
    assert entered.wait(2)

    driver.stop()  # the join gives up and returns; the loop is still in there
    release.set()  # ...and only now does it unwind, closing the gate itself
    assert driver._thread is not None
    driver._thread.join(timeout=2)

    ran: list[str] = []
    off_thread(
        lambda: driver.submit_to_driver(
            lambda: ran.append(threading.current_thread().name), label="plugin teardown"
        )
    )

    assert ran == ["submitter"]  # ran on its caller: no driver thread is left
    with pytest.raises(Empty):
        driver._inbox.get_nowait()


def test_after_stop_a_command_runs_on_its_caller(tmp_path: Path) -> None:
    """The gate is shut: no queue to sit in, so the caller runs it itself."""
    driver, _pipeline, _log = _make_driver(tmp_path)
    driver.start()
    driver.stop()
    ran: list[str] = []

    off_thread(lambda: driver.submit_to_driver(lambda: ran.append("now"), label="post-stop"))

    assert ran == ["now"]


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

    with accepting_commands(driver):
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
