"""Typed bus events, re-exported from the running nParse+ host.

The event classes (``LineEvent``, ``YouZonedEvent``, comms events, …) are
defined by the host app in ``nparseplus.core.events``; the bus dispatches by
exact class, so plugins must subscribe with the host's own classes. This
module forwards attribute access to the host lazily, which always works
in-process. Outside the host (e.g. plain unit tests), install ``nparseplus``
from source to make it importable.
"""

from __future__ import annotations

from typing import Any

_HOST_HINT = (
    "nparseplus_sdk.events re-exports the host app's event classes and needs "
    "nparseplus importable. Inside nParse+ this always works; for standalone "
    "development install the app from source: "
    "pip install git+https://github.com/prokopto-dev/nparse-plus"
)


def _host_events() -> Any:
    try:
        from nparseplus.core import events
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(_HOST_HINT) from exc
    return events


def __getattr__(name: str) -> Any:
    events = _host_events()
    try:
        return getattr(events, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted(n for n in dir(_host_events()) if not n.startswith("_"))
