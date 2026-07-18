"""Process-wide "we are quitting" flag shared by the overlay windows.

Window ``closeEvent`` persists ``shown=False`` so a user-closed window stays
closed next launch. But on macOS Cmd+Q / Dock-quit, Qt closes every window via
``closeAllWindows()`` BEFORE ``aboutToQuit`` fires — so without this flag every
window would clobber its ``shown`` to False on quit and the next launch would
open nothing. The tray Quit handler sets this flag before ``quit()``; windows
check it (alongside ``QCoreApplication.closingDown()``) to skip that clobber.

Plain module globals — no Qt import, so anything may read the flag cheaply.
"""

from __future__ import annotations

_quitting = False


def mark_quitting() -> None:
    """Record that an application quit is in progress."""
    global _quitting
    _quitting = True


def is_quitting() -> bool:
    """True once :func:`mark_quitting` has been called."""
    return _quitting


def reset() -> None:
    """Clear the flag (test helper; the real process only ever quits once)."""
    global _quitting
    _quitting = False
