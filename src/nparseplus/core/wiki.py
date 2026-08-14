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
    from nparseplus.net.p99wiki import WikiLookupResult


class WikiLookup(Protocol):
    def lookup(self, title: str, *, with_image: bool = False) -> WikiLookupResult:
        """One page, carrying why it answered nothing when it did.

        The handler needs the reason: "no such mob page" and "could not reach
        the wiki" render differently, and collapsing them made a connection
        failure look like a feature that had not shipped (#116).
        """
        ...
