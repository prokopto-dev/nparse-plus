# Settings → Plugins

!!! warning "This page only exists if you asked for it"
    Add-ons are **off by default** and nParse+ needs none of them. The
    Plugins page appears only after you tick **Enable plugins (add-ons)** in
    [Settings → Advanced](advanced.md#add-ons-plugins) and restart. Until
    then nothing plugin-related is loaded, shown, or even imported.

The Plugins page is the manager: one row per add-on nParse+ found, plus the
buttons that install, remove, and reveal them. Background on what plugins
are and how far to trust them lives in [Plugins](../plugins/index.md) and
[Plugin security & trust](../plugins/security.md).

## The table

| Column | What it shows |
|---|---|
| **Enabled** | Tick to let the add-on run, untick to hold it back. Saved immediately; takes effect the next time nParse+ starts. Ticking a box is *not* consent — a plugin with no approval record still gets the first-load dialog. |
| **Name** | The add-on's own display name (its file or folder name if the metadata couldn't be read). |
| **Version** | The version the add-on declares. |
| **Status** | Where it is in the load sequence — see below. |
| **Location** | The file, folder, or `dist:entry-point` it was loaded from. |
| **Source** | Provenance: where those bytes came from. |

### Status values

| Status | Meaning |
|---|---|
| **Active** | Loaded, approved, enabled, and `activate()` succeeded — it's running. |
| **Ready** | Approved and enabled, but not activated yet (you're seeing it mid-startup). |
| **Disabled** | You unticked it, or you answered **Keep disabled** at the consent dialog. |
| **Awaiting consent** | nParse+ has never asked you about this one. The approval dialog runs at the next launch, before the plugin does anything. |
| **Incompatible** | The version handshake failed — the add-on wants an SDK or an nParse+ version this build doesn't provide. Ask the author for a rebuild; see [Versioning](../plugins/versioning.md). |
| **Error** | It raised while loading, while its metadata was validated, or inside `activate()`. Hover the cell for the exception; `nparseplus.log` has the traceback. |
| **Duplicate id** | Another add-on already claimed the same plugin id. Only the first one loads — uninstall one of them. |
| **Installed — restart to load** | You installed it in this session. It isn't loaded yet. |

Two annotations can be appended to any of the above:

- **`— update available (v1.2.3)`** — the last **Browse registry…** fetch in
  this session listed a newer release than the one you have installed.
- **`— tick disabled (too slow)`** — the add-on registered a periodic tick
  and the log driver evicted it for repeatedly overrunning its budget. The
  plugin is still active (its parsers, event handlers, and windows all keep
  working) — only the periodic callback was dropped, for the rest of the
  session. Hover the cell for the timing. See
  [Troubleshooting](../troubleshooting.md#a-plugin-tick-was-disabled).

### Source values

| Source | Meaning |
|---|---|
| `https://… (a1b2c3d4e5f6…)` | Downloaded from that URL; the digest is the sha256 of the bytes that were installed. Hover for the full hash. |
| `Local file (a1b2c3d4e5f6…)` | Installed from a file on this machine, with the sha256 of what was installed. |
| **Sideloaded** | Copied into the plugins folder by hand. nParse+ has no record of where it came from and no checksum for it. |

## The buttons

**Browse registry…** opens the curated
[plugin registry](../plugins/registry.md) — a reviewed index published as a
static `index.json`. Each listing shows name, version, author, and whether
it can load here; the button per row reads **Install**, **Installed**, or
**Incompatible**. Registry installs are the only ones that are
**sha256-pinned**: the index records the hash of the reviewed artifact and
the installer refuses a download whose bytes don't match. If the fetch
fails (offline, or the registry isn't published yet) the dialog says
*Registry unavailable* and you can still install from a file or a URL.

**Install from file…** takes a `.zip` archive or a single `.py` file.
Archives are validated member-by-member before anything is extracted —
absolute paths, `..` traversal, symlinks, oversized archives (50 MiB) and
member floods are rejected — and must contain exactly one plugin: one
top-level package directory or one top-level `.py`.

**Install from URL…** downloads a plugin `.zip` over **https only**, and
re-asserts https on every redirect hop, so a link that bounces to plain
http is refused rather than silently fetched. That is transport security
and nothing more: unlike a registry install, **nothing pins the hash**, so
you get whatever is at that URL today. Prefer the registry when the plugin
is listed there.

!!! danger "Installing runs the plugin's code"
    Validation *imports and activates* the candidate to check that it
    really loads, so its module-level code executes at install time — the
    same trust boundary as running it. That is why the install runs on a
    worker thread (a plugin that hangs there can't freeze the window) and
    why the page repeats the warning next to the buttons. The advisory
    findings listed after a successful install are a static scan, not a
    security guarantee.

**Uninstall** works on the selected row, and only for add-ons inside the
plugins folder (a pip-installed entry point has to be uninstalled with
pip). Nothing is deleted: the code moves to `plugins/trash/` and the
plugin's private data moves to `plugins/trash/plugin-data/`. Its consent
record is forgotten with it, deliberately — anything that later claims the
same plugin id has to ask your permission again instead of inheriting the
old approval and the old stored data. Restart to unload it.

**Open Plugins Folder** reveals the folder nParse+ scans, in your file
manager. The same entry is on the tray menu while add-ons are enabled. See
[First run](../getting-started/first-run.md#where-settings-live) for the
path on each platform.

## Restart semantics

Everything on this page that changes *what runs* takes effect at the next
launch — enable, disable, install, uninstall. Activation registers bus
subscriptions, log parsers, and driver ticks that must all be in place
before the log driver thread starts, and plugin windows must exist when the
tray menu and window layouts are built, so there is no safe point to do it
live. Hot enable/disable is tracked as
[issue #45](https://github.com/prokopto-dev/nparse-plus/issues/45).

If an add-on is bad enough to break startup, start nParse+ once with
`NPARSEPLUS_NO_PLUGINS=1` — see
[Troubleshooting](../troubleshooting.md#plugins-add-ons).
