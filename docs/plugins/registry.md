# Plugin registry

The registry is the curated catalogue behind Settings > Plugins >
*Browse registry…*: one reviewed `index.json`, published as a static file
from the `prokopto-dev/nparseplus-plugins` repository via GitHub Pages.
There is deliberately **no server** — submission is a pull request, review
is a human merge, and trust is a pinned hash.

!!! note "Status"
    The registry is **live**: the app fetches
    [`index.json`](https://prokopto-dev.github.io/nparseplus-plugins/index.json)
    and *Browse registry…* works. The catalogue itself is still empty —
    nothing has been submitted yet — so the dialog lists no plugins until
    the first entry is merged. Yours can be it; see
    [Submitting a plugin](#submitting-a-plugin).

    This page is the specification the index and the app both implement.
    The repository's contents are mirrored in the app repo at
    [`templates/registry-repo/`](https://github.com/prokopto-dev/nparse-plus/tree/master/templates/registry-repo),
    which is where the JSON Schema is generated (see below).

## How the app consumes it

- **Browse** fetches `index.json` (from
  `https://prokopto-dev.github.io/nparseplus-plugins/index.json`, or the
  `plugins.registry_url` override in `settings.json`), pre-filters entries
  through the same SDK/app compatibility handshake the loader uses, and
  offers one-click installs.
- **Registry installs are sha256-pinned**: the app downloads the release zip
  and refuses it — before extraction, before any code runs — unless its bytes
  hash to the value the reviewed index recorded. The URL is transport; the
  hash is the security boundary. (This is what separates a registry install
  from *Install from URL…*, which has no expected hash — see
  [Security & trust](security.md#where-the-checksum-applies-and-where-it-doesnt).)
- **Provenance + updates**: registry installs record their source URL and
  hash; once an index has been fetched, the plugins table marks installed
  plugins whose listed version is newer ("update available (vX)").
- Consent is unchanged: a registry install still gets the first-load
  dialog. Curation reduces risk; it does not replace your trust decision.

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
the curation step.

Delisting removes the entry from `index.json` but leaves the `owners.json`
claim: ids are never recycled, so a delisted id cannot be reused by someone
else to ship an "update" to your former users.

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
   at your source) and merges; GitHub Pages republishes the index.

Version updates are the same PR flow: bump `latest` (new version, new URL,
new sha256). Because the hash pins the reviewed bytes, an author cannot
swap the artifact behind an already-listed URL — changing the artifact
means changing the index, which means another review.

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

- Optional index signing (minisign/ed25519, public key shipped in the app)
  if the trust model ever needs to survive a GitHub Pages compromise.
- Automated submission: a workflow in plugin repos that opens the index PR
  on each release.
