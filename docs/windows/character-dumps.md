# Character Dumps

The EQ client can write two snapshots of a character to a text file:

```
/outputfile inventory
/outputfile spellbook
```

It drops them in your EverQuest folder as `<Character>-Inventory.txt` and
`<Character>-Spellbook.txt` — and **overwrites them every time**. The game
keeps exactly one copy of each and no history at all, so the dump you took
before that raid is gone the moment you take another.

The Character Dumps window is a library of those files. Every character keeps
its own current inventory *and* its own current spellbook, with the previous
few versions behind each one.

![The Character Dumps window](../assets/screenshots/window--character-dumps.png)

Open it from the tray → **Character Dumps**, or type `toggle_dumps` in game.
It needs the **EQ install directory** set in
[Settings → General](../settings/general.md) — that is the only place dumps
live.

## Layout

- **Left** — a tree of characters. Under each is an **Inventory** and a
  **Spellbook** branch, when that character has one. The branch row *is* the
  current snapshot and shows when it was taken; older snapshots hang beneath
  it, newest first.
- **Right** — the selected snapshot's contents, with a filter box. An
  inventory lists Location / Item / Count / ID; a spellbook lists Level /
  Spell in book page order. Underneath, a line saying what changed since the
  snapshot before it.
- **Bottom** — how many snapshots are held and what the last scan did.

Empty inventory slots are dropped on import. The client writes a row for
every slot whether or not anything is in it, and keeping them would swamp
every comparison.

## The toggles

| Control | What it does |
|---|---|
| **Auto-import** | Watch the EQ folder and pull in dumps for a character and type this library has never seen. Also the master switch — off means no scanning at all. |
| **Auto-update** | When a dump already in the library changes, store the new version as another snapshot. Off keeps the first import and ignores later `/outputfile` runs. |
| **Keep** | How many snapshots to hold per character, per type. Older ones are pruned as new ones land. |

Both toggles are on by default. Unlike the pigparse.org upload — which is off
until you opt in, because it sends your character to a website — this only
reads files the game already wrote and copies them into nParse+'s own data
folder.

Re-running `/outputfile` without having changed anything does **not** pile up
snapshots: an unchanged dump is recognised by content and dropped.

## Buttons

| Button | What it does |
|---|---|
| **Import now** | Rescan the EQ folder immediately. Works regardless of the toggles. |
| **Import file…** | Take in a dump from anywhere — a backup, another machine, a friend's. |
| **Export…** | Write the selected snapshot back out in the client's own format, so other P99 tools can read it. |
| **Delete** | Remove the selected snapshot. With a character row selected, forget that character entirely. |

## Where the files live

Snapshots are plain JSON under nParse+'s data directory:

```
<data dir>/dumps/<Character>/<inventory|spellbook>/<when>-<digest>.json
```

`<data dir>` is `~/Library/Application Support/nparseplus` on macOS,
`%LOCALAPPDATA%\nparseplus` on Windows, and `~/.local/share/nparseplus` on
Linux. Deleting the folder loses the history and nothing else.

## Relationship to the pigparse.org inventory upload

[Settings → Sharing](../settings/sharing.md) has an **Upload inventory
dumps** option that publishes your inventory to your pigparse.org character
page. It is a separate, opt-in feature that needs a Discord login — but it is
fed by the same scan, so **auto-import has to be on** for it to notice a
dump. Only dumps you take during the current session are uploaded; ones
already sitting in the folder at launch are collected into the library but
not published.

## For plugin authors

The library publishes two bus events, so an add-on can react to a dump
without polling anything:

| Event | When |
|---|---|
| `CharacterDumpImportedEvent` | The first snapshot for a character and type. |
| `CharacterDumpUpdatedEvent` | A tracked dump changed. Carries `added` / `removed` entry names. |

```python
from nparseplus_sdk import events

class MyPlugin:
    def activate(self, ctx):
        self.ctx = ctx
        ctx.subscribe(events.CharacterDumpUpdatedEvent, self.on_dump)

    def on_dump(self, event):
        if event.kind == "spellbook" and event.added:
            self.ctx.logger.info("%s learned %s", event.character, ", ".join(event.added))
```

Both events carry `character`, `kind`, `captured_at`, `entry_count`,
`digest`, and `path` — the stored snapshot, not the game's file. To read the
contents, open the library:

```python
from nparseplus.config.paths import dumps_dir
from nparseplus.core.dumps import DumpKind, DumpLibrary

book = DumpLibrary(dumps_dir()).load_latest("Prokopton", DumpKind.SPELLBOOK)
```

See [Developing plugins](../plugins/developing.md) for the rest of the
plugin contract.
