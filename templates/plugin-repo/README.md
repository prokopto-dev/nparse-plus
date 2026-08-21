# my-nparse-plugin

A plugin for [nParse+](https://github.com/prokopto-dev/nparse-plus), started
from the official plugin template.

> **Using this template?** Work through the checklist below, then delete
> this blockquote.
>
> 1. Rename the `my_nparse_plugin/` package directory to your plugin's name
>    (underscores), and update `PLUGIN_DIR` in both workflow files under
>    `.github/workflows/`.
> 2. In `my_nparse_plugin/__init__.py`, set your `PluginMeta`: unique `id`
>    (lowercase, digits, `-`/`_` — this is your identity everywhere),
>    `name`, `author`, `description`, `homepage`.
> 3. Update `pyproject.toml` (`name`, `description`, authors) and this
>    README's title/description.
> 4. Write your plugin (see the docs links below), keep the tests passing.

## What it does

Say `hello template` in game → a 20-second demo timer + a spoken greeting,
and a small overlay window shows how many times it fired. Replace all of it.

## Develop

```bash
pip install -e .                 # nparseplus-sdk, from PyPI
pip install -e ".[dev]"          # ...plus pytest and nParse+ itself, for full checks
pytest                           # unit tests against FakePluginContext
nparseplus-plugin validate my_nparse_plugin
```

Your plugin must work with the **SDK alone** — that is what `ci.yml`
installs, and what a `nparseplus-plugin validate` run in a bare environment
sees. The lazy host re-exports (`nparseplus_sdk.events`,
`nparseplus_sdk.timers`) only resolve when nParse+ itself is importable, so
guard them with `try/except ImportError` and register your windows *before*
the guard, the way `activate()` in the template does.

To try it live: copy (or symlink) `my_nparse_plugin/` into your nParse+
plugins folder (tray > *Open Plugins Folder*) and restart the app.

Docs you'll want:

- Developer guide: <https://prokopto-dev.github.io/nparse-plus/plugins/developing/>
- API reference: <https://prokopto-dev.github.io/nparse-plus/plugins/api/>
- Versioning rules: <https://prokopto-dev.github.io/nparse-plus/plugins/versioning/>

## Release

1. Bump `meta.version` in `my_nparse_plugin/__init__.py`.
2. Tag it: `git tag v<version> && git push --tags` (the tag must equal
   `meta.version` — the release workflow enforces this).
3. The `release.yml` workflow validates the plugin, zips it in the layout
   the nParse+ installer expects, computes its sha256, and publishes a
   GitHub release with the zip attached. The release body carries the
   artifact's URL and digest — the two things a registry publish needs.

## Publish to the plugin registry

Users can always install your release zip via nParse+ →
Settings > Plugins > *Install from URL* — but listing it in the registry
gives them one-click Browse installs and update notifications.

The registry is a server, not a repository you send a pull request to: you
publish a release yourself, from your own pipeline.

1. Sign in at <https://nparseplugins.prokopto.dev/> with GitHub and mint a
   personal access token scoped to publishing.
2. Claim your plugin id once, from that signed-in session. Ids are
   first-come and permanent.
3. `POST` each release to `/api/v1/plugins/<id>/releases` with that token and
   an `Idempotency-Key` header, carrying the artifact URL, its sha256, and
   the SDK specifier from your `PluginMeta` — the three values the release
   body above prints.

Your artifact stays on your GitHub release. The digest you send is a
**cross-check, not the published value**: the registry downloads the zip and
hashes the bytes itself, and a mismatch sends the release to review instead
of live. Your plugin's first release always waits for a human; later version
bumps from a trusted owner publish on their own.

This workflow deliberately stops at the release. A reusable publish-on-tag
workflow to call from step 3 is the registry's next piece of work — until it
lands, add the request to your own job rather than waiting.

See <https://prokopto-dev.github.io/nparse-plus/plugins/registry/>.
