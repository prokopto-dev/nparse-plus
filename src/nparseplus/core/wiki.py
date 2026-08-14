"""Qt-free typing surface for the P99 wiki client.

Core handlers depend on this Protocol (and a submit function that runs the
call on the net worker thread), never on ``nparseplus.net`` directly — the
same split as :mod:`nparseplus.core.pigparse`. The real implementation is
``net.p99wiki.P99WikiClient``; composition passes ``None`` when the mob-info
wiki lookup is turned off, which makes every lookup site a no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from nparseplus.net.p99wiki import WikiNpc


class WikiLookup(Protocol):
    def npc(self, title: str, *, with_image: bool = False) -> WikiNpc | None: ...
