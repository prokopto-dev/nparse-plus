"""core.eqprocess — the best-effort "is the EQ client running" check.

Both branches are exercised on every platform by pinning ``sys.platform``:
CI runs this suite on Windows too, so a POSIX test that let the real platform
decide would take the Toolhelp path there and assert nothing about ``pgrep``.
The one test that does touch the real Windows API is skipped elsewhere.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from nparseplus.core import eqprocess


class _Result:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


@pytest.fixture
def posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the pgrep branch, whatever the runner actually is."""
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the Toolhelp branch, and fail loudly if anything spawns pgrep."""

    def no_subprocess(*args, **kwargs):
        raise AssertionError("the Windows branch must not spawn a process")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", no_subprocess)


def test_eq_is_running_true_when_pgrep_matches(posix, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(0))
    assert eqprocess.eq_is_running() is True


def test_eq_is_running_false_when_pgrep_finds_nothing(
    posix, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(1))
    assert eqprocess.eq_is_running() is False


def test_eq_is_running_swallows_failures(posix, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise FileNotFoundError("no pgrep here")

    monkeypatch.setattr(subprocess, "run", boom)
    # A missing pgrep must never stop the user from saving.
    assert eqprocess.eq_is_running() is False


def test_eq_is_running_passes_the_expected_pattern(posix, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def record(cmd, *args, **kwargs):
        seen.append(cmd)
        return _Result(1)

    monkeypatch.setattr(subprocess, "run", record)
    eqprocess.eq_is_running()
    assert seen == [["pgrep", "-if", "eqgame"]]


def test_eq_is_running_bounds_the_wait(posix, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_the_pattern_stays_loose_for_wine(posix) -> None:
    """``pgrep -f eqgame`` is what finds the client under wine/CrossOver.

    There the process is the wrapper and ``eqgame.exe`` is an argument, so
    tightening the pattern to the full file name would lose that case — which
    is most of the macOS/Linux P99 population.
    """
    assert eqprocess.PROCESS_PATTERN == "eqgame"


# --- Windows (#33) -------------------------------------------------------


def snapshot(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(eqprocess, "_windows_process_names", lambda: list(names))


def test_windows_sees_the_client(windows, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot(monkeypatch, "explorer.exe", "eqgame.exe", "svchost.exe")
    assert eqprocess.eq_is_running() is True


def test_windows_reports_a_closed_client(windows, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot(monkeypatch, "explorer.exe", "svchost.exe")
    assert eqprocess.eq_is_running() is False


def test_windows_matching_is_case_insensitive_and_loose(
    windows, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image name is what P99's Titanium client is launched as, but the
    same loose substring the POSIX branch needs is applied here."""
    snapshot(monkeypatch, "EQGame.exe")
    assert eqprocess.eq_is_running() is True


def test_windows_failures_are_still_false(windows, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise OSError("CreateToolhelp32Snapshot failed")

    monkeypatch.setattr(eqprocess, "_windows_process_names", boom)
    # Same best-effort contract as the POSIX branch: a probe that cannot
    # answer must not stop the user from saving. (#151 would let the UI say
    # which of the two it was.)
    assert eqprocess.eq_is_running() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Toolhelp32 is Windows-only")
def test_the_real_snapshot_lists_this_very_process() -> None:
    """The ctypes call itself, on the one runner that can run it.

    Asserting our own image name is in the list is what catches a
    PROCESSENTRY32W laid out wrongly: a bad field width does not raise, it
    silently yields garbage where the names should be.
    """
    names = {name.lower() for name in eqprocess._windows_process_names()}
    assert Path(sys.executable).name.lower() in names
    assert isinstance(eqprocess.eq_is_running(), bool)
