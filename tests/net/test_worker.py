"""NetWorker tests (real thread, synchronized with events — no wall-clock)."""

import threading

from nparseplus.net.worker import ImmediateWorker, NetWorker


def test_worker_runs_fetch_and_delivers_apply() -> None:
    delivered: list = []
    applied: list = []
    done = threading.Event()

    worker = NetWorker(deliver=delivered.append)
    worker.start()

    def fetch() -> str:
        return "result"

    def apply(value: str) -> None:
        applied.append(value)
        done.set()

    worker.submit(fetch, apply)
    # The delivery closure arrives via `deliver`; run it like the driver
    # thread would.
    for _ in range(100):
        if delivered:
            break
        threading.Event().wait(0.01)
    assert delivered, "worker never delivered"
    delivered[0]()
    assert applied == ["result"]
    worker.stop()


def test_worker_failure_is_swallowed_and_loop_survives() -> None:
    delivered: list = []
    worker = NetWorker(deliver=delivered.append)
    worker.start()

    def boom() -> None:
        raise RuntimeError("scripted")

    worker.submit(boom, lambda _r: None)  # fails: no delivery
    worker.submit(lambda: 42, lambda r: None)
    for _ in range(100):
        if delivered:
            break
        threading.Event().wait(0.01)
    assert len(delivered) == 1  # only the successful task delivered
    worker.stop()


def test_worker_fetch_without_apply_delivers_nothing() -> None:
    delivered: list = []
    ran = threading.Event()
    worker = NetWorker(deliver=delivered.append)
    worker.start()
    worker.submit(ran.set)
    assert ran.wait(2.0)
    worker.stop()
    assert delivered == []


def test_immediate_worker_is_synchronous() -> None:
    applied: list = []
    worker = ImmediateWorker()
    worker.submit(lambda: 7, applied.append)
    assert applied == [7]
