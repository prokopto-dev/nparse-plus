"""Timer row types, re-exported from the running nParse+ host.

``ctx.timers`` is the host ``TimersService``; the row models it accepts
(``TimerRow``, ``CounterRow``, ``SpellRow``, ``RollRow`` and the group-name
constants) are defined in ``nparseplus.core.timers``. Import them from here
inside driver-thread callbacks — lazily, like ``nparseplus_sdk.events``.
"""

from __future__ import annotations

from typing import Any

_HOST_HINT = (
    "nparseplus_sdk.timers re-exports the host app's timer row classes and "
    "needs nparseplus importable. Inside nParse+ this always works; for "
    "standalone development install the app from source: "
    "pip install git+https://github.com/prokopto-dev/nparse-plus"
)


def _host_timers() -> Any:
    try:
        from nparseplus.core import timers
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(_HOST_HINT) from exc
    return timers


def __getattr__(name: str) -> Any:
    timers = _host_timers()
    try:
        return getattr(timers, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted(n for n in dir(_host_timers()) if not n.startswith("_"))
