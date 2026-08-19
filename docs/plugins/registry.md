# Plugin registry

The registry is the curated catalogue behind Settings > Plugins >
*Browse registry…*: one reviewed `index.json`, served by the live registry
server at <https://nparseplugins.prokopto.dev/index.json> — one Go binary and
a SQLite database, [`prokopto-dev/nparse-plugin-regserve`](https://github.com/prokopto-dev/nparse-plugin-regserve).
It began as a static file published from GitHub Pages and edited by pull
request, and moved so that publishing can be a step in a plugin's own release
pipeline instead of a second repository and a wait. What did **not** move is
the part that carries the trust: a listing pins a sha256, and the installer
refuses bytes that do not match it.

That built-in catalogue is the one nParse+ ships with, but it is not the
only one it will read: you can add registries of your own, and Browse
merges every enabled one into a single table. That is a real trust
decision, and [Using another registry](#using-another-registry) below is
the canonical explanation of what you are agreeing to.

!!! note "Status"
    The registry is **live**: the app fetches
    [`index.json`](https://nparseplugins.prokopto.dev/index.json) and
    *Browse registry…* works.

    Nothing in the client changed when the catalogue moved off GitHub Pages
    ([#130](https://github.com/prokopto-dev/nparse-plus/issues/130)). It is
    the same schema-1 document fetched with the same single unconditional
    GET, and the server keeps that path outside its versioned API precisely
    so it never moves. Publishing *into* it straight from a plugin's release
    pipeline is designed and not yet built; until it is, a maintainer applies
    listings — see [Submitting a plugin](#submitting-a-plugin).

    This page is the specification the index and the app both implement. The
    JSON Schema the registry validates listings against is generated from the
    app's own parser and committed at
    [`templates/registry-repo/`](https://github.com/prokopto-dev/nparse-plus/tree/master/templates/registry-repo)
    (see below).

## How the app consumes it

- **Browse** fetches the index of every *enabled* registry — the built-in
  `https://nparseplugins.prokopto.dev/index.json` unless you
  untick it, plus anything you added — runs the same SDK/app compatibility
  handshake the loader uses over each listing, and offers one-click
  installs. The fetches run concurrently on a worker thread and each
  registry's outcome is reported separately (`fetch_indexes` in
  `core/plugins/registry.py`).
- **Registry installs are sha256-pinned**: the app downloads the release zip
  and refuses it — before extraction, before any code runs — unless its bytes
  hash to the value that registry's index recorded. The URL is transport; the
  hash is the security boundary. (This is what separates a registry install
  from *Install from URL…*, which has no expected hash — see
  [Security & trust](security.md#where-the-checksum-applies-and-where-it-doesnt).
  What the hash does *not* establish is
  [who was allowed to publish it](#using-another-registry).)
- **Provenance + updates**: a registry install records the download URL, the
  hash, **and which registry listed it** (`PluginEntry.registry_url`), and
  the Source column names that registry. The plugins table marks installed
  plugins with a newer *compatible* release available ("update available
  (vX)") — naming the source when the offer comes from a different one than
  the plugin was installed from — and offers an **Update** button that
  replaces the copy in place, keeping your consent and the add-on's stored
  data. See [Taking an update](#taking-an-update).
- Consent is unchanged: a registry install still gets the first-load
  dialog. Curation reduces risk; it does not replace your trust decision.

## Using another registry

**Settings > Plugins > Plugin registries** is a small table — Enabled,
Name, URL — with *Add registry…* and *Remove*. Every ticked row is fetched
by Browse; every unticked row is ignored entirely.

!!! danger "Adding a registry is a wider decision than installing a plugin"
    A registry entry supplies **both halves** of an install: the URL the
    artifact is fetched from *and* the sha256 that download is verified
    against. So the checksum proves the bytes you got are the bytes **that
    registry chose** — it is a tamper check between the registry and you,
    nothing more. It is not a review, it does not say the code is safe, and
    it certainly does not say a human other than the registry's operator
    ever looked.

    Adding a registry therefore hands its operator a standing ability to
    offer you arbitrary code, under any plugin name, arriving pre-verified
    and one click from installed. Trust a registry the way you would trust
    the authors whose plugins you install — because it decides who those
    authors are.

The app states this at the moment it matters: adding shows a confirmation
carrying both that warning and the plugin-consent warning, and it **defaults
to Cancel** (`ui/pluginregistries.py`). Nothing is written to `settings.json`
until you accept.

### Adding one

*Add registry…* asks for the https URL of an `index.json` and an optional
display name, then confirms. The URL is normalized before it is stored —
scheme and host are lower-cased, the path is left alone (paths are
case-sensitive) — so the same registry cannot be added twice under two
spellings, and non-https URLs are refused outright. A row with no name shows
its host.

### The built-in registry can be unticked, never removed

The built-in row is **not stored in your settings at all**. It is
synthesized on every read from the `DEFAULT_REGISTRY_URL` constant in the
app (`resolve_registries`), and only its checkbox persists, as
`plugins.default_registry_enabled`. Two consequences, both deliberate:

- *Remove* refuses on that row (the button is disabled, and the code refuses
  again if you get there another way). If it could be deleted there would be
  no way back to it from this UI.
- If a future release changes where the built-in catalogue lives, every user
  moves with it. Had the default been written into `settings.json` once,
  everyone would be pinned to whatever URL the release that first wrote it
  happened to use.

A user entry pointing at the built-in URL collapses into that one row rather
than appearing twice, which is what makes promoting a community registry to
the default a non-event.

### What Browse does with several registries

- **One merged table**, ordered built-in first, then your registries in the
  order you added them, with a **Source** column naming the registry that
  served each row. Anything that is not the built-in is spelled out as
  `(third-party)` in the text — not signalled with colour, so it survives a
  screenshot, a colour-blind reader, and a theme with its own opinions.
- **Collisions are shown, not resolved.** Plugin ids are unique *within* a
  registry, not across registries: two registries can both list
  `merchant-prices` and mean different code. Both rows appear, each tagged
  *also listed elsewhere* with a tooltip naming the other registries. The
  app will not pick for you.
- **If a plugin is already installed from a different registry**, that row's
  button reads **Installed (other source)** and is disabled, with a tooltip
  naming both registries. Swapping publishers of the same id is not a
  one-click action; uninstall the current copy first if you really want the
  other one.
- **One registry being down does not hide the rest.** Each fetch is reported
  separately, so an unreachable registry becomes a line above the table
  ("Could not reach 1 of 3 registries: …") while the ones that answered are
  listed underneath as usual. Concurrency matters here for more than speed: a
  dead registry ahead of a live one would otherwise cost its full timeout
  before the working one was even tried.
- **Refresh** re-fetches every enabled registry without closing the dialog
  (single-flight — a second click while a fetch is in progress does nothing).

### Updates prefer the registry you installed from

`best_update` picks the update to offer for an installed plugin. Only
*compatible* releases are candidates — offering an update the app would then
refuse to load is worse than offering none. Among those, **the registry the
plugin was installed from wins** if it has anything to offer at all, even if
another registry lists a higher version. Silently promoting a different
publisher's build of the same id would be a trust hop you never agreed to;
when the only offer *does* come from elsewhere, the status line says so by
name ("update available (v2.0.0 from …)") instead of quietly presenting it
as the same plugin, and taking it needs a confirmation naming both ends.

A plugin with **no** recorded registry — sideloaded, or installed from a
plain URL — has a wrinkle worth stating: any registry offer for it counts as
a source change, because nothing ever vouched for the copy you have. The one
exception is the plugin's own declared
[update feed](developing.md#shipping-updates-without-a-registry), which is
the only source such a copy has ever had, so its offers are not treated as a
hop. That is what makes a self-hosted add-on updatable in one click without
pretending a registry endorsed it.

### Taking an update

**Settings > Plugins** shows an **Update** button on any row with a newer
compatible release, and **Update all** for the ones that need no decision.
The download is verified against the listed sha256, the new code is
validated before anything moves, and the old copy goes to `plugins/trash/`
only once the new one is in place — a failure at any point leaves the
version you had installed and loading.

Consent and `plugin-data/<id>` survive an update, which is the entire point:
the old workaround (uninstall, reinstall) went through `PluginHost.forget`
and destroyed both. The new code loads at the next launch, like every other
change on that page.

Since an index carries only `latest`, there is no version history and no
"downgrade" — the previous copy in `plugins/trash/` is the only way back.

### Where it is stored

`plugins.registries` in `settings.json` holds your registries only — a list
of `{url, name, enabled}` — and `plugins.default_registry_enabled` holds the
built-in row's checkbox. The old single-registry override,
`plugins.registry_url`, is gone: a settings file that still carries one has
it folded into `plugins.registries` and cleared on load. Unusable entries
(non-https, empty, duplicate) are dropped silently rather than rejected,
because a settings document that fails to parse costs the user every other
setting they have ever configured.

A plugin installed **before the built-in registry moved to its own server**
recorded the old GitHub Pages URL as its provenance, and that URL now names
no configured registry at all: the Source cell would fall back to a bare host
name, Browse would offer *Installed (other source)* instead of an Update
button, and taking the update would demand a confirmation naming two
registries that are the same catalogue. So those records are re-pointed the
first time settings load after the move. The catalogue moved; the publisher
did not, and the move is not a trust hop.

The same load drops a *registry* row holding that old URL. Such a row could
only ever have been an inert duplicate of the built-in one — the app refused
to add a registry equal to the built-in URL, and a stored copy collapsed into
that row rather than appearing beside it, so it was never shown and never
separately fetched. Keeping it would un-collapse it into a third-party
registry you never added, pointing at a stale index.

Both halves run **once**, recorded by `plugins.registry_move_applied`, because
what that URL means changed with the move: it is now an ordinary index you
may add deliberately. Do that and it stays — the row survives every reload
and anything it vouches for keeps naming it. Settings written by the release
that moved the catalogue carry no marker, so a second test backs it up: a
provenance record naming the *new* URL can only have been written after the
move, and one is enough to leave the document alone. A registry row that
nothing is installed from is never touched either — there is nothing to
repair, so nothing is.

## Index format (schema 1)

```json
{
  "schema_version": 1,
  "plugins": [
    {
      "id": "merchant-prices",
      "name": "Merchant Prices",
      "description": "Tracks your WTS auctions with PigParse price history.",
      "author": "someone",
      "homepage": "https://github.com/someone/nparse-merchant-prices",
      "latest": {
        "version": "1.2.0",
        "url": "https://github.com/someone/nparse-merchant-prices/releases/download/v1.2.0/merchant_prices.zip",
        "sha256": "9f2c…64 hex chars…",
        "requires_sdk": ">=1.0,<2",
        "min_app_version": "1.18.0"
      }
    }
  ]
}
```

Rules: `id` matches `^[a-z][a-z0-9_-]{1,39}$`; `url` is https and should be a
GitHub release asset of the plugin's own repo; `sha256` is 64 **lowercase**
hex characters and is the hash of exactly that artifact; `schema_version`
newer than the app understands makes the app say "update nParse+" rather
than misread the index. Only `latest` is carried per plugin — the registry
lists the current reviewed release, not a version history.

`nparseplus.core.plugins.registry` is the source of truth for this format;
`tools/gen_registry_schema.py` generates the registry repo's
`schema/index-v1.schema.json` from those pydantic models, so the schema CI
validates against and the parser the app runs cannot drift.

## Id ownership

Ids are first-come and permanent, and that is **machine-checkable**, not
just a review convention. The registry repo carries an `owners.json`
alongside the index mapping each plugin id to a list of GitHub handles:

```json
{
  "owners": {
    "merchant-prices": ["someone"],
    "raid-tools": ["someone", "their-comaintainer"]
  }
}
```

Any listed handle may submit changes to that plugin's entry. CI requires
that every id in `index.json` has an owners entry, and that the PR author
owns every entry they add or change. Adding the `owners.json` line for a
brand-new plugin is a maintainer action in the same PR — that addition is
the curation step. On the server the same rule is a database row rather than
a file: ownership rows are never deleted, and the file above is what seeds
them.

Delisting removes the listing but leaves the ownership claim: ids are never
recycled, so a delisted id cannot be reused by someone else to ship an
"update" to your former users.

That guarantee is **per registry**. This repo's `owners.json` binds ids in
this index and nowhere else — another registry can list your id pointing at
its own artifact, which is exactly why Browse shows both rows and why the
app refuses to treat one as an update to the other
([above](#what-browse-does-with-several-registries)).

## Submitting a plugin

1. Build your release with the
   [plugin repo template](developing.md#starting-from-the-repo-template) —
   its release workflow attaches the zip and prints the exact registry
   entry JSON (with the sha256 already computed) in the release body and as
   a `registry-entry.json` release asset.
2. Open a PR against `prokopto-dev/nparseplus-plugins` adding or updating
   your entry in `index.json`, plus your id in `owners.json` if it's new.
   A submission should touch only those two files.
3. Registry CI checks the mechanical facts (below).
4. A maintainer reviews (this is the curation step — expect them to look
   at your source) and applies the merged entry to the live catalogue.

Version updates are the same flow: bump `latest` (new version, new URL, new
sha256). Because the hash pins the reviewed bytes, an author cannot swap the
artifact behind an already-listed URL — changing what users receive means
changing the listing, which means another review.

!!! info "This is the interim flow"
    The reason the registry became a server is that a patch release should be
    a step in *your* pipeline, not a pull request against someone else's
    repository: `POST` the new release with a scoped token, and the server
    downloads the artifact and hashes it **itself**, so an author-supplied
    hash is never stored. A brand-new plugin id still waits for a human — that
    is the review worth keeping — while version bumps from a trusted owner
    publish on their own. That half is specified and not yet built, which is
    why a person still applies your entry today.

### What CI checks

`.github/workflows/validate-index.yml` on every PR:

| Check | Failure means |
| --- | --- |
| JSON Schema (`schema/index-v1.schema.json`) | The entry is malformed or has a field the app cannot read. |
| Plugin id format and uniqueness | Bad id, or the id is already taken. |
| `https://` release URLs, including after redirects | A plain-http hop. |
| sha256 is 64 lowercase hex characters | Uppercase or truncated hash. |
| Every listed id has an `owners.json` entry | A new plugin needs its ownership line in the same PR. |
| The PR author owns every entry they add or change | Someone else owns that id. |
| Best-effort: the artifact is downloaded and re-hashed | The zip at that URL is not the one you hashed. If the artifact is unreachable from CI the job records a notice instead of failing, and the reviewer checks by hand. |

!!! info "CI does not run `nparseplus-plugin validate` — on purpose"
    Validating a plugin **imports it and calls `activate()`**. Doing that in
    registry CI would mean executing unreviewed code from a pull request on
    the registry's own infrastructure, on every submission, before any human
    has read it. That trade isn't worth it for a check the reviewer can run
    locally in a sandbox.

    So registry CI never extracts or executes a submitted artifact: it
    downloads the bytes, hashes them, and throws them away. The job also
    runs on `pull_request` (not `pull_request_target`), so fork PRs get a
    read-only token and no secrets — and since a PR can edit the workflow
    itself, a green check is a convenience filter, never the trust boundary.
    The human merge is.

## Roadmap

- **Publishing from your own release pipeline** — a scoped token, one
  `POST` per release, and the server fetching and re-hashing the artifact
  rather than believing a submitted digest. Tracked on the registry server's
  own roadmap.
- Optional index signing (minisign/ed25519, public key shipped in the app)
  if the trust model ever needs to survive a compromise of the host serving
  the index.
