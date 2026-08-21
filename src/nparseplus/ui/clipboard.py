"""The one way this app puts text on the system clipboard.

Two windows write to it — the Character Dumps window's "Copy review link"
and the DPS meter's fight parse (#78) — and both inject this rather than
calling it inline, so no test ever touches the machine's real clipboard.
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication


def system_clipboard_copy(text: str) -> bool:
    """Put ``text`` on the system clipboard. False if there isn't one.

    Injected into a window rather than called inline so tests never touch the
    real clipboard — on Windows that goes through OLE and hands data to the
    OS, which outlives the test and crashed a CI run when the GC later reaped
    it under the offscreen platform. Same reason ``open_browser`` is injected
    into the upload handler: global machine state does not belong in a unit
    test.
    """
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:  # pragma: no cover - no platform clipboard
        return False
    clipboard.setText(text)
    return True
