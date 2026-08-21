# Plugin registry

The registry is the curated catalogue behind Settings > Plugins >
*Browse registry…*: one reviewed `index.json`, served by the live registry
server at <https://nparseplugins.prokopto.dev/index.json> — one Go binary and
a SQLite database, [`prokopto-dev/nparse-plugin-regserve`](https://github.com/prokopto-dev/nparse-plugin-regserve).
It began as a static file published from GitHub Pages and edited by pull
request, and moved so that publishing can be a step in a plugin's own release
pipeline instead of a second repository and a wait. What did **not** move is
the part that carries the trust — and on the question that matters it got
*stronger*: a listing pins a sha256 and the installer refuses bytes that do
not match it, and that digest is now one the registry
[computed itself](#the-digest-is-one-the-registry-computed) from the artifact
it downloaded, rather than a number an author submitted and a human agreed
looked right.

That built-in catalogue is the one nParse+ ships with, but it is not the
only one it will read: you can add registries of your own, and Browse
merges every enabled one into a single table. That is a real trust
decision, and [Using another registry](#using-another-registry) below is
the canonical explanation of what you are agreeing to.

!!! note "Status"
    The registry is **live**, and so is publishing into it. The app fetches
    [`index.json`](https://nparseplugins.prokopto.dev/index.json),
    *Browse registry…* works, and an author publishes a release with one
    authenticated `POST` from their own pipeline — see
    [Publishing a plugin](#publishing-a-plugin).

    Nothing in the client changed when the catalogue moved off GitHub Pages
    ([#130](https://github.com/prokopto-dev/nparse-plus/issues/130)). It is
    the same schema-1 document fetched with the same single unconditional
    GET, and the server keeps that path outside its versioned API precisely
    so it never moves.

    This page is the specification the index and the app both implement. The
    JSON Schema the served index is checked against is generated from the
    app's own parser and committed at
    [`templates/registry-repo/schema/`](https://github.com/prokopto-dev/nparse-plus/tree/master/templates/registry-repo)
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
  hash is the security boundary. For the built-in catalogue that value was
  [computed by the registry itself](#the-digest-is-one-the-registry-computed);
  a registry you add makes its own promises, and the index cannot tell you
  which. (This is what separates a registry install
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

    Nor can you tell from an index *where its digests came from*. The
    built-in registry computes every one of them itself, from the artifact it
    downloaded; another registry is free to publish whatever number an author
    handed it, and the document looks identical either way.

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
and destroyed both. The new code loads at the next launch — the one thing on
that page that still waits for one, because a module is imported once per
session and cannot be swapped out under the objects already using it.
Installing, enabling and disabling all apply immediately.

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
no registry you have: the Source cell falls back to a bare host name, Browse
offers *Installed (other source)* instead of an Update button, and taking the
update demands a confirmation naming two registries that are the same
catalogue. So a record naming that URL is re-pointed at the new one. The
catalogue moved; the publisher did not, and the move is not a trust hop.

**Unless you list that URL yourself.** The rule is your own registry list,
re-checked on every load: while the old index is a row in **Plugin
registries**, every record naming it is true as written and nothing is
touched — not the row, not the records. That is what makes it safe to add the
old index deliberately now that it is an ordinary third-party URL. Only when
no row holds it is a record re-pointed, and then there is nothing else it
could have meant: back when that URL was the built-in registry, nParse+
refused to add it as a registry of your own and folded any stored copy into
the built-in row, so an install could not have come through a copy.

nParse+ does not edit your registry list to make this tidier. If a row
holding the old URL appears after the upgrade, it was always in your settings,
hidden behind the built-in row it duplicated; it is an ordinary third-party
row now and **Remove** works on it. Take it out and the next load folds the
records it was holding in place into the built-in catalogue. The single row
nParse+ does drop is one it created in that same load out of the
long-deprecated `plugins.registry_url` override — an artifact of folding a
field in, never a list entry you built.

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
        "min_app_version": "1.18.0",
        "release_notes": "Fixes the price cache going stale after a zone."
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
lists the current published release, not a version history.

`release_notes` is optional, and it is **plain text** — what changed, for a
person to read. It is not Markdown and not HTML: the registry promises only
that the field is *not markup* (valid UTF-8, no control characters but
newline, at most 2048 bytes) rather than filtering what an author wrote, and
that promise is what means no client has to carry a sanitiser or a renderer.
nParse+ shows it in a plain-text widget, so `**bold**` arrives as literal
asterisks — deliberately. Anything that renders the field as markup is doing
what the registry's
[ADR-0013](https://github.com/prokopto-dev/nparse-plugin-regserve/blob/main/docs/adr/0013-release-notes-are-plain-text-with-a-hard-cap.md)
says not to. (nParse+ also accepts it spelled `notes`, which is what the
publish API calls the same field.)

Unknown keys are ignored, which is what makes a new field like that one safe
to add: an app too old to know about it reads the rest of the index normally.

`nparseplus.core.plugins.registry` is the source of truth for this format;
`tools/gen_registry_schema.py` generates
`templates/registry-repo/schema/index-v1.schema.json` from those pydantic
models, and the registry server vendors that file verbatim and diffs the
document it renders against it — so what the server serves and what the app
parses cannot drift.

## Id ownership

Ids are first-come and permanent, and that is a **database row**, not a
review convention and no longer a file. You claim an id once, from a
signed-in session; the claim registers the plugin and makes you its owner,
and every later publish under that id has to come from an owner of it.

Ownership rows are never deleted. Delisting removes the listing and leaves
the claim standing, so a delisted id cannot be reused by someone else to
ship an "update" to your former users. Handing a plugin over is
add-the-new-owner, then remove the old one — in that order, so the plugin is
never briefly ownerless.

Claiming an id gets you a row and an owner grant. It does **not** get you a
listing: a plugin appears in the index only once a release of it has been
published, and the first release of a new id always goes to a human.

That guarantee is **per registry**. An ownership row binds an id in *that*
registry's catalogue and nowhere else — another registry can list your id
pointing at its own artifact, which is exactly why Browse shows both rows and
why the app refuses to treat one as an update to the other
([above](#what-browse-does-with-several-registries)).

## Publishing a plugin

Publishing is a step in your own release pipeline: one authenticated `POST`
per release, from the workflow that built the artifact. There is no second
repository to fork and nothing to paste anywhere.

1. **Sign in** at <https://nparseplugins.prokopto.dev/> with GitHub — the
   registry's only identity provider — and mint a personal access token
   scoped to publishing. The token is shown once.
2. **Claim your plugin id**, once, from that signed-in session (a token
   cannot do it; claiming an id is a decision a person makes while signed
   in). The id is first-come, permanent and never recycled — it identifies
   your plugin in every installed copy on every user's machine.
3. **Tag your release.** The
   [plugin repo template](developing.md#starting-from-the-repo-template)'s
   workflow refuses a tag that disagrees with `meta.version`, builds the zip
   in the layout the installer expects, attaches it to a GitHub release, and
   prints its sha256.
4. **`POST` the release** to `/api/v1/plugins/{id}/releases` with your token
   and an `Idempotency-Key` header, carrying the artifact URL, the sha256
   your build computed, your `requires_sdk` specifier, an optional minimum
   app version, and optional plain-text release notes. Re-running the
   workflow with the same key returns the original result instead of
   publishing a second time — which is the behaviour you want from a job
   somebody will inevitably re-run.

The artifact stays on your GitHub release; the registry stores its URL and a
hash. A version update is the same four steps minus the first two.

!!! info "Step 4 is still a request you write yourself"
    A reusable publish-on-tag workflow for plugin repositories is the
    registry server's next piece of work, and the template will be wired to
    it when it lands. Until then the `POST` is yours to add — this page and
    the registry's own API documentation are the contract, and nothing about
    it changes when the reusable workflow arrives.

### The digest is one the registry computed

The old arrangement's safety came from the merge: a maintainer read the
listing, and because the sha256 pinned the reviewed bytes, changing what
users received *required another review*. It is worth saying plainly what
replaced that, rather than letting it quietly lapse — because on the one
question a hash can answer, what replaced it is **stronger**.

**The registry does not store the hash you submit.** It downloads the
artifact from the URL you gave it, hashes the bytes it actually received, and
publishes *that* value. Yours is compared against it and then discarded. So
the digest in the index is not a claim somebody made about an artifact; it is
a measurement of one, taken by the machine that published it. "A human
confirmed this JSON matches that release" was the old promise, and it was
only ever as good as the afternoon the human was having.

The comparison is the point. If the two differ, something between your build
and the registry's fetch changed the bytes — a re-uploaded release asset, a
stolen token, a hijacked URL — and that release does not go live: it goes to
review with the reason recorded. So does a release whose artifact could not
be fetched at all. "We could not check" and "we checked and it was fine" are
never allowed to produce the same answer
([ADR-0008](https://github.com/prokopto-dev/nparse-plugin-regserve/blob/main/docs/adr/0008-server-rehashes-every-artifact.md)).

That fetch treats the URL as hostile input throughout: `https` re-asserted on
every redirect hop, a hop cap, a size cap enforced *during* the read, a
timeout, and a dialer that refuses private, loopback, link-local and
cloud-metadata addresses. The bytes are hashed as they stream and thrown
away — never extracted, never written anywhere, never executed. That last
part is not new: the registry has never run a submitted artifact, because
validating a plugin means importing it and calling `activate()`, and doing
that to unreviewed code on the registry's own infrastructure would be a worse
trade than the check is worth. A reviewer runs it locally, in a sandbox.

What the digest cannot do is vouch for the registry. It proves the registry
measured those bytes, not that the registry is honest — which is why index
signing is still on the list below, and why adding a *third-party* registry
is [a decision of its own](#using-another-registry).

### What still waits for a human

A **brand-new plugin id always goes to review.** Nothing bypasses that — not
trust, not automation, not an owner who has published fifty times before. The
first appearance of an id is where curation is cheapest and where
impersonation is caught, and it is the review worth keeping.

After that, a version bump from an owner the maintainers have marked trusted
publishes on its own, once the artifact has been fetched and hashed clean.
Trust is a tier a maintainer raises by hand and can revoke; it starts at the
floor for every new account and is **never** raised automatically, because a
counter of successful publishes is a counter an attacker can run up.

Some releases go to review regardless of trust
([ADR-0007](https://github.com/prokopto-dev/nparse-plugin-regserve/blob/main/docs/adr/0007-review-new-ids-trust-gates-updates.md)):

| The release is held when | Because |
| --- | --- |
| The artifact is hosted somewhere this plugin's previous releases were not | The download URL moving is what taking over a plugin's distribution looks like. |
| Its size differs from the previous release by more than a set proportion | A plugin that suddenly triples is worth a look, whoever published it. |
| Its version is not greater than the current `latest` | Republishing over a version is how you change what an existing listing means. |
| The artifact could not be fetched, or its bytes did not match the hash you submitted | Unverified is not a success, and a mismatch is the exact event the re-hash exists to catch. |

Each of those is a named, testable condition rather than a reviewer's
intuition, so the same submission gets the same answer twice.

And the honest cost of the bargain, since a trust document that only lists
its wins is not one: **a trusted owner can ship unreviewed code to their
existing users**, and so can anyone who steals a trusted owner's token. That
is what was traded for patch releases that do not wait on a person. What
bounds it is the blast radius — one plugin, one id, users who already chose
that author — plus an audit trail, and your own
[consent prompt](security.md), which no publish route can skip.

## Roadmap

- **A reusable publish-on-tag workflow** for plugin repositories, so step 4
  above is a line in your release job instead of a request you wrote
  yourself. It is the registry server's next piece of work, and the plugin
  repo template will be wired to it when it lands.
- Optional index signing (minisign/ed25519, public key shipped in the app)
  if the trust model ever needs to survive a compromise of the host serving
  the index. That is the one gap the server-computed digest above cannot
  close, since the server is what computes it.
