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
   GitHub release with the zip attached. The release body contains the
   ready-made **registry entry JSON**.

## Publish to the plugin registry

Users can always install your release zip via nParse+ →
Settings > Plugins > *Install from URL* — but listing it in the registry
gives them one-click Browse installs and update notifications:

1. Copy the registry entry JSON from your GitHub release body.
2. Open a pull request adding/updating your entry in the
   `prokopto-dev/nparseplus-plugins` index.
3. Registry CI re-validates your zip against the pinned sha256; a
   maintainer review merges it.

See <https://prokopto-dev.github.io/nparse-plus/plugins/registry/>.
