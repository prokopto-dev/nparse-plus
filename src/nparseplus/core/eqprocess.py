"""Is the EverQuest client running? (Qt-free)

Anything that writes into the EQ install directory wants to know, because the
client rewrites a character's ``.ini`` wholesale when you camp or log out —
an edit made while it is running is silently discarded. Callers warn on this;
none of them block on it.

Best-effort by design: any failure reports "not running" rather than raising,
so a broken probe never stops the user from saving.

**Two probes, chosen by platform.** POSIX shells out to ``pgrep``; Windows
walks a Toolhelp process snapshot through ``ctypes`` (#33). Until that second
branch existed the answer was hard ``False`` on Windows — ``pgrep`` is not
there, the blanket ``except`` swallowed the ``FileNotFoundError``, and the
warning never appeared on the platform most P99 players are on.

``ctypes`` rather than ``tasklist`` for the Windows branch: no process to
spawn (so no console flash in a windowed PyInstaller build, and no
``creationflags`` to remember), no localized stdout to parse — ``tasklist``
prints a translated "No tasks are running" line and exits 0 either way, so
its exit code answers nothing — and it costs microseconds rather than tens
of milliseconds.

**The POSIX probe spawns a process** (17.6 ms mean on an idle Mac) and must
not be called on the driver thread — ``core.socialsync`` runs it through a
``core.background.BackgroundJob`` for exactly that reason. The Windows one is
far cheaper, but the callers are shared, so the rule is the rule.

``PROCESS_PATTERN`` stays a loose substring on purpose. On POSIX ``pgrep -f``
matches the whole command line, which is what finds ``eqgame.exe`` running
under wine/CrossOver (where the process is the wrapper and the client is an
argument); on Windows it is matched against the image name. Tightening it to
``eqgame.exe`` would break the wine case.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

PROCESS_PATTERN = "eqgame"

# A `pgrep` that has not answered in a couple of seconds is not going to.
# The old 5 s ceiling was the worst case for whichever thread asked, which
# is a hang on the GUI thread and a stalled log tail on the driver's.
PROBE_TIMEOUT_S = 2.0

# Toolhelp32 (tlhelp32.h). Named here rather than inline so the ctypes call
# below reads like the Win32 documentation it is transcribing.
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260


def eq_is_running() -> bool:
    """True when a process matching ``eqgame`` is running; False if unknown.

    Never raises: "cannot tell" is reported as "not running", which is the
    contract every caller is written against. Telling those two apart would
    be better (the UI could say so) and is deliberately not done here — the
    return type is published SDK surface via ``ctx.eq_is_running()``, so it
    is a contract change rather than a bug fix. Tracked on #151.
    """
    try:
        if sys.platform == "win32":
            return _windows_probe()
        return _posix_probe()
    except Exception:
        logger.debug("EQ process probe failed", exc_info=True)
        return False


def _posix_probe() -> bool:
    """macOS/Linux, including a client running under wine or CrossOver."""
    result = subprocess.run(
        ["pgrep", "-if", PROCESS_PATTERN],
        capture_output=True,
        timeout=PROBE_TIMEOUT_S,
        check=False,
    )
    return result.returncode == 0


def _windows_probe() -> bool:
    needle = PROCESS_PATTERN.lower()
    return any(needle in name.lower() for name in _windows_process_names())


def _windows_process_names() -> list[str]:
    """Every running process's image name, from a Toolhelp32 snapshot.

    Collected into a list rather than yielded: the snapshot is an OS handle,
    and a generator abandoned by a short-circuiting ``any()`` would leave the
    moment it is closed up to the garbage collector.
    """
    if sys.platform != "win32":  # pragma: no cover - guards the ctypes below
        raise OSError("the Toolhelp snapshot API is Windows-only")

    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            # ULONG_PTR: pointer-width, so c_size_t rather than a DWORD —
            # getting this wrong misaligns every field after it, and the
            # symptom would be garbage names rather than an error.
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * MAX_PATH),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):  # INVALID_HANDLE_VALUE
        raise ctypes.WinError(ctypes.get_last_error())
    names: list[str] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            names.append(entry.szExeFile)
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names
