"""Character dump library — stored ``/outputfile`` inventory and spellbook
snapshots, per character (Qt-free).

The EQ client overwrites ``<Character>-Inventory.txt`` every time you run
``/outputfile inventory``, and the same for the spellbook, so the game keeps
exactly one copy of each and no history at all. This package takes copies:
one library, keyed by **character and kind**, so every character carries its
own current inventory *and* its own current spellbook, with the previous few
versions behind them.

Layout of the package:

``models``
    :class:`CharacterDump` and the entry types, plus digesting and diffing.
``parse``
    Recognizing the two file shapes and reading them (and writing them back).
``store``
    :class:`DumpLibrary`, the directory of snapshots.
``watcher``
    :class:`DumpWatcher`, the driver-tick auto-importer, and the only thing
    here that publishes bus events.

**For plugin authors.** Two events fire whenever the library takes something
in — ``CharacterDumpImportedEvent`` (first snapshot for a character+kind) and
``CharacterDumpUpdatedEvent`` (a tracked dump changed, with an ``added`` /
``removed`` name diff). Both are ordinary bus events, so::

    from nparseplus_sdk import events

    def activate(self, ctx):
        ctx.subscribe(events.CharacterDumpUpdatedEvent, self.on_dump)

    def on_dump(self, event):
        ctx.logger.info("%s gained %s", event.character, ", ".join(event.added))

To read the contents rather than the notification, open the library
yourself — it is plain JSON under
``nparseplus.config.paths.dumps_dir()``::

    from nparseplus.core.dumps import DumpKind, DumpLibrary
    dump = DumpLibrary(dumps_dir()).load_latest("Prokopton", DumpKind.SPELLBOOK)
"""

from __future__ import annotations

from nparseplus.core.dumps.models import (
    SCHEMA_VERSION,
    CharacterDump,
    DumpDiff,
    DumpKind,
    InventoryEntry,
    SpellbookEntry,
    content_digest,
    diff_dumps,
)
from nparseplus.core.dumps.parse import (
    build_dump,
    dump_target,
    inventory_entries,
    parse_spellbook_text,
    read_dump_file,
    render_dump_text,
    sniff_kind,
)
from nparseplus.core.dumps.store import (
    DEFAULT_KEEP,
    DumpLibrary,
    SnapshotRef,
)
from nparseplus.core.dumps.watcher import (
    SCAN_INTERVAL_SECONDS,
    DumpWatcher,
    ScanResult,
)

__all__ = [
    "DEFAULT_KEEP",
    "SCAN_INTERVAL_SECONDS",
    "SCHEMA_VERSION",
    "CharacterDump",
    "DumpDiff",
    "DumpKind",
    "DumpLibrary",
    "DumpWatcher",
    "InventoryEntry",
    "ScanResult",
    "SnapshotRef",
    "SpellbookEntry",
    "build_dump",
    "content_digest",
    "diff_dumps",
    "dump_target",
    "inventory_entries",
    "parse_spellbook_text",
    "read_dump_file",
    "render_dump_text",
    "sniff_kind",
]
