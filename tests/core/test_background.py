"""core.background — the one-at-a-time off-thread seam for driver ticks."""

import threading

from nparseplus.core.background import BackgroundJob, run_inline


def test_work_runs_on_another_thread() -> None:
    job = BackgroundJob("test-job")
    seen: list[int] = []

    assert job.submit(lambda: seen.append(threading.get_ident())) is True
    assert job.wait(timeout=5.0)
    assert seen and seen[0] != threading.get_ident()


def test_a_second_submit_is_refused_while_one_is_in_flight() -> None:
    job = BackgroundJob("test-job")
    release = threading.Event()
    runs = 0

    def work() -> None:
        nonlocal runs
        runs += 1
        release.wait(5.0)

    assert job.submit(work) is True
    assert job.running is True
    assert job.submit(work) is False  # not queued: dropped
    release.set()
    assert job.wait(timeout=5.0)

    assert runs == 1
    assert job.running is False
    assert job.submit(work) is True  # free again
    assert job.wait(timeout=5.0)


def test_failing_work_leaves_the_job_usable() -> None:
    job = BackgroundJob("test-job", spawn=run_inline)

    def boom() -> None:
        raise RuntimeError("no")

    assert job.submit(boom) is True  # swallowed, not raised into the tick
    assert job.running is False
    assert job.submit(lambda: None) is True


def test_a_spawn_that_fails_is_reported_not_raised() -> None:
    def refuse(_name: str, _work: object) -> None:
        raise RuntimeError("cannot start threads")

    job = BackgroundJob("test-job", spawn=refuse)
    assert job.submit(lambda: None) is False
    assert job.running is False


def test_run_inline_keeps_the_work_on_the_calling_thread() -> None:
    job = BackgroundJob("test-job", spawn=run_inline)
    seen: list[int] = []

    job.submit(lambda: seen.append(threading.get_ident()))
    assert seen == [threading.get_ident()]
