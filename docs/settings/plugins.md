# Settings → Plugins

!!! warning "This page only exists if you asked for it"
    Add-ons are **off by default** and nParse+ needs none of them. The
    Plugins page appears only after you tick **Enable plugins (add-ons)** in
    [Settings → Advanced](advanced.md#add-ons-plugins) and restart. Until
    then nothing plugin-related is loaded, shown, or even imported.

The Plugins page is the manager: one row per add-on nParse+ found, the
buttons that install, remove, and reveal them, and — underneath — the list
of registries add-ons may be offered from. Background on what plugins are
and how far to trust them lives in [Plugins](../plugins/index.md) and
[Plugin security & trust](../plugins/security.md).

## The table

| Column | What it shows |
|---|---|
| **Enabled** | Tick to let the add-on run, untick to hold it back. Saved *and applied* immediately — the plugin starts or stops there and then, and the Status cell re-renders to prove it. Ticking a box is *not* consent — a plugin with no approval record still gets the first-load dialog. |
| **Name** | The add-on's own display name (its file or folder name if the metadata couldn't be read). |
| **Version** | The version the add-on declares. |
| **Status** | Where it is in the load sequence — see below. |
| **Performance** | What this add-on is costing the log thread — see [below](#the-performance-column). |
| **Location** | The file, folder, or `dist:entry-point` it was loaded from. |
| **Source** | Provenance: which registry vouched for those bytes, or where else they came from. |

### Status values

| Status | Meaning |
|---|---|
| **Active** | Loaded, approved, enabled, and `activate()` succeeded — it's running. |
| **Ready** | Approved and enabled, but not activated yet (you're seeing it mid-startup). |
| **Disabled** | You unticked it, or you answered **Keep disabled** at the consent dialog. |
| **Awaiting consent** | nParse+ has never asked you about this one. The approval dialog runs before the plugin does anything — right after an install, or at the next launch. |
| **Incompatible** | The version handshake failed — the add-on wants an SDK or an nParse+ version this build doesn't provide. Ask the author for a rebuild; see [Versioning](../plugins/versioning.md). |
| **Error** | It raised while loading, while its metadata was validated, or inside `activate()`. Hover the cell for the exception; `nparseplus.log` has the traceback. |
| **Duplicate id** | Another add-on already claimed the same plugin id. Only the first one loads — uninstall one of them. |
| **Installed — restart to load** | Something installed this session that could not be loaded in place — nParse+ only adopts a plugin the plugins-folder sweep would pick up. It loads at the next launch; an ordinary install starts running immediately. |

Two annotations can be appended to any of the above:

- **`— update available (v1.2.3)`** — a newer release than the one you have
  is listed, *and* it can load on this build (an update your nParse+ or SDK
  version would refuse is not offered at all). The **Update** column carries
  a button to take it. If the offer comes from a different source than the
  copy you have, the annotation names it —
  **`— update available (v1.2.3 from Some Registry)`** — because that is a
  different publisher of the same plugin id, not the same add-on. See
  [Updates prefer the registry you installed from](../plugins/registry.md#updates-prefer-the-registry-you-installed-from).
- **`— tick disabled (too slow)`** — the add-on registered a periodic tick
  and the log driver evicted it for repeatedly overrunning its budget. The
  plugin is still active (its parsers, event handlers, and windows all keep
  working) — only the periodic callback was dropped, for the rest of the
  session. Hover the cell for the timing. See
  [Troubleshooting](../troubleshooting.md#a-plugin-tick-was-disabled).

### Source values

| Source | Meaning |
|---|---|
| `nParse+ registry (built-in) · a1b2c3d4e5f6…` | Listed by that registry, which is also where the pinned sha256 came from. Hover for the registry URL, the artifact URL, and the full hash. |
| `https://… (a1b2c3d4e5f6…)` | Downloaded from that URL with **Install from URL…**; no registry vouched for it. The digest is the sha256 of the bytes that were installed. |
| `Local file (a1b2c3d4e5f6…)` | Installed from a file on this machine, with the sha256 of what was installed. |
| **Sideloaded** | Copied into the plugins folder by hand. nParse+ has no record of where it came from and no checksum for it. |

A registry install leads with the registry rather than the download host,
because the registry is who you chose to trust. The name shown is resolved
against your *current* registry list: if you have since removed that
registry the cell falls back to its host name and the tooltip adds *"this
registry is no longer configured"* — the record of what vouched for the
install is never rewritten.

The one exception is the built-in registry moving house. Plugins installed
before the catalogue moved to
<https://nparseplugins.prokopto.dev/index.json> recorded its previous URL,
and left alone they would all read as that "no longer configured" case, with
every update from the built-in registry demanding a source-change
confirmation between two names for the same catalogue. So those records are
re-pointed — **unless you list that old URL as a registry of your own**, in
which case they name something you actually have, and neither they nor your
row are touched.

Your **Plugin registries** list is never edited to tidy this up. If a row
holding that old URL turns up after the upgrade it was always in your
settings, hidden behind the built-in row it duplicated — it is an ordinary
third-party row now, and **Remove** works on it. Remove it and the next load
folds those old records into the built-in catalogue; keep it and everything
it vouched for keeps naming it.

## The buttons

**Check for updates** asks every ticked registry, plus the update feed of
each enabled add-on that declares one, what the newest release is. It runs
on a worker thread and annotates the table when it comes back; a registry
that cannot be reached is named above the table rather than blanking it.
Unless you have turned it off, the same check runs by itself about twelve
seconds after nParse+ starts, so the page usually already knows — see
[Automatic checks](#automatic-checks).

**Update** (one per row, in the **Update** column) replaces that add-on
in place. The download is verified against the sha256 the offering source
listed, the new code is validated before anything is swapped, and only then
does the old copy move to `plugins/trash/`. **Your consent record and the
add-on's stored data are kept** — this is the whole difference from
uninstalling and reinstalling, which forgets both by design. If the update
fails at any point, the version you had is still installed and still loads.

If the offer comes from a source other than the one that supplied your
copy, the button gains an ellipsis and asks first, naming both ends. That is
not the same add-on arriving with a new version number; it is a different
publisher of the same plugin id, and it may be entirely different code.

**Update all (n)** takes every update that needs no such decision, one at a
time. Updates from a different source are deliberately left out — the count
in the status line says how many and why. One failure does not stop the
rest; a single summary at the end lists what was updated and what was not.

**Browse registry…** fetches every ticked [registry](../plugins/registry.md)
at once and merges the listings into one table: name, version, author,
**Source**, and whether it can load here. Registry installs are the only
ones that are **sha256-pinned** — the index records the hash of the artifact
it listed and the installer refuses a download whose bytes don't match.
**Refresh** re-fetches without closing the dialog.

The button on each row reads:

| Button | Meaning |
|---|---|
| **Install** | Compatible, not installed. One click downloads it, verifies the pinned hash, and installs it. |
| **Update to v1.2.3** | You have an older version, from this same registry. One click replaces it in place — your consent and the add-on's stored data are kept. |
| **Update to v1.2.3…** | Same, but the offer comes from a *different* source than the copy you have. The ellipsis is the promise of a confirmation naming both ends before anything is downloaded. |
| **Installed** | You already have this plugin id at this version, from this registry (or from a file/URL with no registry recorded). Disabled. |
| **Installed (other source)** | You have this plugin id, a *different* registry vouched for your copy, and this listing has nothing newer. Disabled — the tooltip names both registries. |
| **Incompatible** | The listed release wants an SDK or nParse+ version this build doesn't provide. Disabled. |

The **Source** cell names the registry that served the row, with
`(third-party)` spelled out for anything but the built-in one. If two
registries list the same plugin id, both rows appear, each marked *— also
listed elsewhere* with a tooltip naming the others.

If a registry can't be reached the dialog says so above the table
("Could not reach 1 of 3 registries: …") and still shows everything the
others returned; only when *nothing* was returned does the table disappear,
with a reminder that you can still install from a file or a URL. If you have
unticked every registry it says so and points you back at the list below.

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
old approval and the old stored data. A running add-on is stopped and
unloaded as it goes — no restart.

**Open Plugins Folder** reveals the folder nParse+ scans, in your file
manager. The same entry is on the tray menu while add-ons are enabled. See
[First run](../getting-started/first-run.md#where-settings-live) for the
path on each platform.

## Plugin registries

Under the buttons is a second, short table — the registries **Browse
registry…** reads. One row per registry:

| Column | What it shows |
|---|---|
| **Enabled** | Ticked registries are fetched by Browse; unticked ones are ignored entirely. Saved immediately, and it takes effect the next time you press Browse — no restart. |
| **Name** | The display name you gave it, or its host if you gave none. The built-in row is *nParse+ registry (built-in)*. |
| **URL** | The `index.json` it fetches. |

**Add registry…** asks for an `https://` index URL, then an optional display
name, then confirms — and the confirmation is the point:

!!! danger "A registry decides which add-ons you are offered"
    A listing carries both the download URL *and* the sha256 the download is
    checked against, so adding a registry lets whoever runs it offer you any
    code at all, pre-verified. The hash proves a download matches what that
    registry says it should be; it is not a review. The dialog says so, and
    **defaults to Cancel** — nothing is saved unless you accept. Read
    [Using another registry](../plugins/registry.md#using-another-registry)
    before you add one.

    A URL that isn't https, or one already in the list, is refused with the
    reason. Scheme and host are lower-cased when stored, so the same
    registry can't sneak in twice under two spellings.

**Remove** takes the selected registry out of the list. Plugins already
installed from it stay installed (the Source column keeps naming it). The
built-in registry **cannot be removed** — the button greys out on that row,
and the app refuses again if you reach it another way — because there would
be no way back to it from this page. Untick it instead: that stops it being
offered while keeping the way back, and it also means a future release can
move the built-in catalogue without stranding you on an old URL.

## The performance column

Add-on event handlers, log parsers and periodic ticks all run **inline on
the log thread** — the same thread that tails your log, advances every
countdown and tracks your DPS. So an add-on that takes too long there is not
just slow itself; it stutters the whole app. The Performance column is the
number that lets you tell which one.

A row that has done some work reads something like:

```
28.1 ev/s · avg 1.2 µs · p95 1.8 µs · worst 42 µs · 0.1% of the driver thread
```

- **ev/s** — how many events the add-on's handlers are being given per
  second, averaged over the last fifteen seconds. It falls back to zero when
  the add-on goes quiet.
- **avg / p95 / worst** — how long one of its handlers takes. `avg` and
  `p95` are over the most recent few hundred calls, so they describe how it
  is behaving *now*; `worst` is the whole session and never resets, so a
  single bad stall is still there to be found later. Figures below a
  millisecond are shown in microseconds, because that is where a
  well-behaved add-on lives.
- **parser avg / tick avg** — the same, for add-ons that read log lines
  directly or run on a timer.
- **% of the driver thread** — roughly what share of the log thread's life
  this add-on has spent using. Well-behaved add-ons sit near zero.
- **errors / dropped** — only appear when there are some. "Dropped" means
  the tick watchdog evicted the add-on's periodic callback for repeatedly
  overrunning its budget; the Status cell says so too.

Hover the cell for the long form: call counts, p50/p95/p99 per channel, and
what windows the figures cover.

**Measure add-on performance** (ticked by default) is the switch. Untick it
and the column reads *not collecting*: handler and parser timing stops
immediately, and what it costs while on — a fraction of a microsecond per
callback — stops with it. Timing is only ever applied to add-on callbacks;
nParse+'s own handlers, parsers and ticks are never measured, so a setup
with no add-ons pays nothing for this either way.

The figures are per session and are not saved. Disabling an add-on and
enabling it again starts its numbers over, because that is a new run.

## Automatic checks

**Check for plugin updates shortly after launch** (ticked by default) polls
about twelve seconds after nParse+ starts, on a worker thread, so the
Plugins page can tell you what is out of date without you going looking. It
contacts:

- every registry you have ticked, and
- the update feed of each **enabled** add-on that declares one.

That second one is worth knowing about: a self-published feed is a URL the
add-on's author chose, so a request goes to a server of their choosing on
every launch. Feeds of add-ons you disabled or declined are never contacted
— declining an add-on declines its feed with it. Nothing here is contacted
at all if no add-ons are installed.

The check is quiet: no popup, no tray notification. It fails soft — a
registry being down leaves the previous answer in place and is reported the
next time you open the page. Untick the box to stop it entirely and use
**Check for updates** by hand instead.

## What applies now, and what needs a restart

**Enabling, disabling, installing and uninstalling all take effect
immediately.** Tick a box and the add-on starts there and then; untick it
and it stops. The Status cell re-renders to show you which it did.

"Stops" means all of it. A disabled add-on loses its event subscriptions,
its log parsers and its periodic tick, and everything it put on screen goes
with them: its windows are closed and destroyed, its tray entries go, its
in-game `show_`/`hide_`/`toggle_` chat commands stop resolving, its own
settings page leaves the sidebar,
its row leaves [Settings → Windows](windows.md#plugin-windows), and any
timers it added are taken off the Timers window. Ticking the box again
rebuilds every one of those.

What a toggle does *not* touch is your side of it: the approval you gave and
the add-on's `plugin-data/<id>` folder both survive being disabled — only
uninstalling forgets those — and the window's saved size, opacity and
on-top setting stay in `settings.json`, so it comes back where you left it.

**Measure add-on performance** is live too — untick it and timing stops
on the next callback, with no restart and no re-registration.

Two things still need a relaunch, and both say so where you do them:

- **Enable plugins (add-ons)**, the master switch in
  [Settings → Advanced](advanced.md#add-ons-plugins). This one is
  restart-only *by design*: with add-ons off, none of the machinery is even
  imported — that is what "off" means here — so turning it on would have to
  import, ask consent for and start everything discovered at once, and
  turning it off would have to prove none of it is left. A restart does both
  properly.
- **Updating an add-on you already have.** A Python module is imported once
  per session and cannot be swapped out safely mid-flight: re-importing
  replaces only the top-level module, leaving its submodules stale and the
  running objects holding the old module's globals. So the new files are put
  in place immediately and the new *code* starts at the next launch. (A
  fresh install has never been imported this session, which is exactly why
  it can load on the spot.)

If an add-on is bad enough to break startup, start nParse+ once with
`NPARSEPLUS_NO_PLUGINS=1` — see
[Troubleshooting](../troubleshooting.md#plugins-add-ons).
