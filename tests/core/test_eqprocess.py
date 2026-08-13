"""core.eqprocess — the best-effort "is the EQ client running" check."""

import subprocess

import pytest

from nparseplus.core import eqprocess


class _Result:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_eq_is_running_true_when_pgrep_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(0))
    assert eqprocess.eq_is_running() is True


def test_eq_is_running_false_when_pgrep_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(1))
    assert eqprocess.eq_is_running() is False


def test_eq_is_running_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise FileNotFoundError("no pgrep here")

    monkeypatch.setattr(subprocess, "run", boom)
    # A missing pgrep must never stop the user from saving.
    assert eqprocess.eq_is_running() is False


def test_eq_is_running_passes_the_expected_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def record(cmd, *args, **kwargs):
        seen.append(cmd)
        return _Result(1)

    monkeypatch.setattr(subprocess, "run", record)
    eqprocess.eq_is_running()
    assert seen == [["pgrep", "-if", "eqgame"]]


def test_eq_is_running_bounds_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    def record(cmd, *args, **kwargs):
        seen.append(kwargs["timeout"])
        return _Result(1)

    monkeypatch.setattr(subprocess, "run", record)
    eqprocess.eq_is_running()
    # Whoever asks waits this long in the worst case — a GUI thread hang or a
    # stalled log tail. A pgrep that slow has already failed.
    assert seen == [eqprocess.PROBE_TIMEOUT_S]
    assert eqprocess.PROBE_TIMEOUT_S <= 2.0
