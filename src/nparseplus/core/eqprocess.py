"""Is the EverQuest client running? (Qt-free)

Anything that writes into the EQ install directory wants to know, because the
client rewrites a character's ``.ini`` wholesale when you camp or log out —
an edit made while it is running is silently discarded. Callers warn on this;
none of them block on it.

Best-effort by design: any failure reports "not running" rather than raising,
so a missing ``pgrep`` never stops the user from saving. That does mean the
check is a no-op on Windows, where ``pgrep`` does not exist — see the tracking
issue referenced from the Macro Editor docs.

**This spawns a process** (17.6 ms mean on an idle Mac) and must not be
called on the driver thread — ``core.socialsync`` runs it through a
``core.background.BackgroundJob`` for exactly that reason.
"""

from __future__ import annotations

import subprocess

PROCESS_PATTERN = "eqgame"

# A `pgrep` that has not answered in a couple of seconds is not going to.
# The old 5 s ceiling was the worst case for whichever thread asked, which
# is a hang on the GUI thread and a stalled log tail on the driver's.
PROBE_TIMEOUT_S = 2.0


def eq_is_running() -> bool:
    """True when a process matching ``eqgame`` is running; False if unknown."""
    try:
        result = subprocess.run(
            ["pgrep", "-if", PROCESS_PATTERN],
            capture_output=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0
