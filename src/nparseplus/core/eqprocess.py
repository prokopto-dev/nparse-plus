"""Is the EverQuest client running? (Qt-free)

Anything that writes into the EQ install directory wants to know, because the
client rewrites a character's ``.ini`` wholesale when you camp or log out —
an edit made while it is running is silently discarded. Callers warn on this;
none of them block on it.

Best-effort by design: any failure reports "not running" rather than raising,
so a missing ``pgrep`` never stops the user from saving. That does mean the
check is a no-op on Windows, where ``pgrep`` does not exist — see the tracking
issue referenced from the Macro Editor docs.
"""

from __future__ import annotations

import subprocess

PROCESS_PATTERN = "eqgame"


def eq_is_running() -> bool:
    """True when a process matching ``eqgame`` is running; False if unknown."""
    try:
        result = subprocess.run(
            ["pgrep", "-if", PROCESS_PATTERN], capture_output=True, timeout=5, check=False
        )
    except Exception:
        return False
    return result.returncode == 0
