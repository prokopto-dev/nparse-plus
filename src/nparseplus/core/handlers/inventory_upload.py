"""InventoryUploadHandler — push fresh inventory dumps to pigparse.org.

Port of EQTool's Services/InventoryWatcherService.cs, restructured: the C#
service is a FileSystemWatcher that both *notices* the dump and *uploads* it.
Here noticing is somebody else's job — :class:`~nparseplus.core.dumps.
DumpWatcher` polls the EQ directory once for the whole app — and this handler
is a plain bus subscriber that uploads what the library just took in. One
watcher tick over the EQ directory, one parse, two consumers.

That restructure changes three behaviors on purpose:

* **Stale dumps.** The old watcher primed its mtimes at startup so a dump
  from a previous session could not upload. The library deliberately does not
  prime (collecting what is already there is the point of a library), so the
  gate moves here and gets stated directly: only dumps *captured* after this
  session started are uploaded. Same intent, and truer — it keys on when the
  player took the dump rather than on which files happened to exist at launch.
* **Re-running /outputfile with nothing changed** no longer re-uploads: the
  library dedupes by content digest, so an identical dump raises no event.
* **Whose inventory it is.** The C# uploads under the *active* character; the
  event names the character the dump file belongs to, which is the same
  character in the normal case and correct in the case where it isn't. The
  server still comes from ActivePlayer — a P99 dump filename carries none.

Consequence worth knowing: uploads ride on the dump library noticing the
file, so ``dumps.auto_import`` (on by default) has to be on. Both settings
surfaces say so.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.dumps import CharacterDump, DumpKind, DumpLibrary
from nparseplus.core.events import (
    CharacterDumpEvent,
    CharacterDumpImportedEvent,
    CharacterDumpUpdatedEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.inventory import InventoryItem
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer


class InventoryUploadHandler(BaseHandler):
    """Uploads inventory snapshots to the pigparse.org character browser."""

    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        library: DumpLibrary,
        *,
        is_enabled: Callable[[], bool],
        get_token: Callable[[], str],
        session_start: datetime,
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        super().__init__(bus, player)
        self.library = library
        self._is_enabled = is_enabled
        self._get_token = get_token
        self._session_start = session_start
        self.api = api
        self.submit = submit
        bus.subscribe(CharacterDumpImportedEvent, self._on_dump)
        bus.subscribe(CharacterDumpUpdatedEvent, self._on_dump)

    def _on_dump(self, event: CharacterDumpEvent) -> None:
        if event.kind != str(DumpKind.INVENTORY):
            return
        if event.captured_at < self._session_start:
            return  # taken before we started; see the module docstring
        api, submit, token = self.api, self.submit, self._get_token()
        server = self.player.server
        if api is None or submit is None or not self._is_enabled() or not token or server is None:
            return
        character = event.character or self.player.name
        if not character:
            return
        dump = self.library.load_path(event.path)
        if dump is None or not dump.items:
            return

        items = _wire_items(dump)
        server_int = int(server)

        def fetch() -> None:
            api.upload_inventory(
                character_name=character,
                server=server_int,
                items=items,
                api_token=token,
            )

        submit(fetch, None)


def _wire_items(dump: CharacterDump) -> list[InventoryItem]:
    """The stored snapshot back in the shape the REST client sends."""
    return [
        InventoryItem(
            location=entry.location,
            name=entry.name,
            item_id=entry.item_id,
            count=entry.count,
            slots=entry.slots,
        )
        for entry in dump.items
    ]
