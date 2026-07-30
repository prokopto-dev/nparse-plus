# Submitting a plugin

Listing is optional. Users can always install your release zip through
nParse+ → Settings → Plugins → *Install from URL*. Listing here adds one-click
Browse installs, update notifications, and a reviewed sha256.

Everything is a pull request against this repository. There is no form, no
account and no API.

## Before you start

Your plugin needs a **published GitHub release with a downloadable zip**. The
[plugin repo template](https://github.com/prokopto-dev/nparseplus-plugin-template)
does this for you: tag `vX.Y.Z` (matching `PluginMeta.version`) and its
`release.yml` validates the plugin, builds the zip in the layout the nParse+
installer requires, computes the sha256, and publishes the release.

That workflow also writes **`registry-entry.json`** — attached to the release
and reproduced in the release body — which is *exactly* the object this
registry wants. You should not have to write any JSON by hand or compute any
hash yourself.

Read the [developer guide](https://prokopto-dev.github.io/nparse-plus/plugins/developing/)
first if you have not built the plugin yet.

## Adding a new plugin

1. **Pick your id.** `PluginMeta.id` matches `^[a-z][a-z0-9_-]{1,39}$`, must
   not already appear in `index.json` or `owners.json`, and is permanent — it
   is your plugin's identity in every installed copy. Check both files before
   you tag a release.

2. **Release it**, then grab `registry-entry.json` from the release assets (or
   copy the JSON block out of the release body).

3. **Fork this repository** and edit two files:

   - `index.json` — paste your entry, unchanged, into the `plugins` array.
     Keep the array sorted by `id`; do not touch anyone else's entry.
   - `owners.json` — add your id to `owners`, mapped to a list containing your
     GitHub handle (no leading `@`):

     ```json
     "owners": {
       "your-plugin-id": ["your-github-handle"]
     }
     ```

     List co-maintainers here too — any listed handle may submit updates.

4. **Open the pull request.** Title it `add <your-plugin-id> vX.Y.Z`. In the
   body, link the release and say in a sentence or two what the plugin does
   and what it touches (files it writes, network it uses). A submission should
   change **only** `index.json` and `owners.json` — CI flags anything else for
   the reviewer.

5. **CI runs**, then a maintainer reviews. Curation means they will read your
   source; be ready for questions. On merge, GitHub Pages republishes the
   index within a minute or two and the app picks it up on the next Browse.

## Updating an existing plugin

Same flow, shorter: tag the new release, then open a PR that replaces your
entry's `latest` object with the new `registry-entry.json` (new `version`, new
`url`, new `sha256`). Title it `update <your-plugin-id> to vX.Y.Z`. Leave
`owners.json` alone unless maintainership is actually changing.

Every version update is re-reviewed. That is the point: because the hash pins
the reviewed bytes, changing what users receive *requires* changing the index,
which requires another human look.

## Transferring or delisting

- **Adding a co-maintainer / handing over:** a PR from a current owner that
  edits the `owners.json` entry. CI accepts a handover plus an index update in
  the same PR, as long as a current owner authored it.
- **Delisting:** a PR removing the entry from `index.json`. The `owners.json`
  claim stays — ids are never recycled, so a delisted id cannot be reused by
  someone else to ship an update to your former users.

## What CI checks

`.github/workflows/validate-index.yml`, on every PR:

| Check | Failure means |
| --- | --- |
| JSON Schema (`schema/index-v1.schema.json`) | The entry is malformed or has a field the app cannot read. |
| Plugin id format and uniqueness | Bad id, or the id is already taken. |
| `https://` release URLs (including after redirects) | Plain-http downloads are refused. |
| sha256 is 64 **lowercase** hex characters | Uppercase or truncated hash. |
| Every listed id has an `owners.json` entry | A new plugin needs its ownership line in the same PR. |
| The PR author owns every entry they add or change | Someone else owns that id. |
| The artifact is downloaded and re-hashed | The zip at that URL is not the one you hashed — re-release, or you pasted a stale entry. |

The download check is best-effort: if the artifact is unreachable from CI the
job records a notice instead of failing, and the reviewer verifies by hand.

A green run does not mean it will be merged, and CI is not the security
boundary — the human review is. Reviewers, see the trust model in
[README.md](README.md).

## Getting help

Ask in an issue on this repository, or in
[prokopto-dev/nparse-plus](https://github.com/prokopto-dev/nparse-plus) for
anything about the SDK or the app itself.
