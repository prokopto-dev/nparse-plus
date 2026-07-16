"""Qt-free typing surface for the PigParse REST client.

Core handlers depend on this Protocol (and a submit function that runs the
call on the net worker thread), never on ``nparseplus.net`` directly. The
real implementation is ``net.pigparse_api.PigParseApiClient``; composition
passes ``api=None, submit=None`` when sharing is off, which turns every
send site into a no-op.

``SubmitFn`` is ``NetWorker.submit``: ``submit(fetch, apply)`` runs
``fetch()`` on the worker thread and delivers ``apply(result)`` back on the
driver thread via the sharing coordinator's inbox.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

SubmitFn = Callable[..., None]


class PigParseApi(Protocol):
    def item_prices(self, server: int, names: list[str]) -> Sequence[Any]: ...
    def item_wiki(self, name: str, zone: str) -> str | None: ...
    def players_by_names(self, names: list[str], server: int) -> Sequence[Any]: ...
    def upsert_players(self, players: list[Any], server: int) -> None: ...
    def send_npc_activity(
        self,
        *,
        name: str,
        zone: str,
        server: int,
        is_death: bool = False,
        is_engaged: bool = False,
        loc_x: float | None = None,
        loc_y: float | None = None,
    ) -> None: ...
    def send_quake(self, server: int) -> None: ...
    def boat_seen(self, *, start_point: str, boat: int, server: int) -> None: ...
    def boat_activity(self, server: int) -> Sequence[Any]: ...
    def roll_timers(self, server: int) -> Sequence[Any]: ...
