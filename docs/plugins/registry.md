# Plugin registry

The registry is the curated catalogue behind Settings > Plugins >
*Browse registry…*: one reviewed `index.json`, published as a static file
from the `prokopto-dev/nparseplus-plugins` repository via GitHub Pages.
There is deliberately **no server** — submission is a pull request, review
is a human merge, and trust is a pinned hash.

!!! note "Status"
    The registry repository is not live yet. Until it is, the Browse dialog
    shows "Registry unavailable" and everything else (file/URL installs,
    the plugins folder) works normally. This page is the specification the
    registry repo will implement.

## How the app consumes it

- **Browse** fetches `index.json` (from
  `https://prokopto-dev.github.io/nparseplus-plugins/index.json`, or the
  `plugins.registry_url` override in `settings.json`), pre-filters entries
  through the same SDK/app compatibility handshake the loader uses, and
  offers one-click installs.
- **Installs are sha256-pinned**: the app downloads the release zip and
  refuses it — before extraction, before any code runs — unless its bytes
  hash to the value the reviewed index recorded. The URL is transport; the
  hash is the security boundary.
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
        "min_app_version": "1.15.0"
      }
    }
  ]
}
```

Rules: `id` matches `^[a-z][a-z0-9_-]{1,39}$` and is unique
(first-come, enforced at review); `url` is https and should be a GitHub
release asset of the plugin's own repo; `sha256` is the hash of exactly
that artifact; `schema_version` newer than the app understands makes the
app say "update nParse+" rather than misread the index.

## Submitting a plugin

1. Build your release with the
   [plugin repo template](developing.md#starting-from-the-repo-template) —
   its release workflow attaches the zip and prints the exact registry
   entry JSON (with the sha256 already computed) in the release body.
2. Open a PR against `prokopto-dev/nparseplus-plugins` adding or updating
   your entry in `index.json`.
3. Registry CI must verify before review: the JSON validates against the
   schema, the `id` is unique (or the PR author already owns it), the URL
   is https and downloadable, the artifact's sha256 matches the entry, and
   `nparseplus-plugin validate` passes on the extracted artifact (advisory
   scan surfaced for the reviewer).
4. A maintainer reviews (this is the curation step — expect them to look
   at your source) and merges; GitHub Pages republishes the index.

Version updates are the same PR flow: bump `latest` (new version, new URL,
new sha256). Because the hash pins the reviewed bytes, an author cannot
swap the artifact behind an already-listed URL — changing the artifact
means changing the index, which means another review.

## Roadmap

- Stand up the `nparseplus-plugins` repository implementing this spec
  (index + CI + Pages).
- Optional index signing (minisign/ed25519, public key shipped in the app)
  if the trust model ever needs to survive a GitHub Pages compromise.
- Automated submission: a workflow in plugin repos that opens the index PR
  on each release.
