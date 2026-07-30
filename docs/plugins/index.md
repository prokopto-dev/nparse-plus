# Plugins

Since 1.15, nParse+ can load **plugins**: optional add-ons written in Python
that add their own overlay windows, react to log events, poll web services,
and contribute settings pages. Example ideas — a merchant window that tracks
what you're selling with live PigParse prices, a DKP tracker fed by your
guild's server, an auction bid watcher.

Plugins are **off by default**: nothing loads until you install one, and the
first time a new plugin is seen you're asked whether to enable it.

!!! danger "Plugins are third-party code"
    A plugin runs with the full permissions of nParse+ on your computer.
    nParse+ cannot verify what a plugin does — only install plugins from
    authors you trust. See [Security & trust](security.md).

## Installing a plugin

Two ways:

**Settings > Plugins** (recommended) — open *nParse+ Settings* from the tray
and pick the *Plugins* page:

- *Install from file…* — choose a plugin `.zip` (or a single `.py` file).
- *Install from URL…* — paste an `https://` link to a plugin `.zip`
  (for example a GitHub release asset).

The installer checks the archive is safe to extract, validates that the
plugin loads, and shows any advisory findings before finishing. Newly
installed plugins load **the next time nParse+ starts**, and you'll be asked
to confirm enabling them then.

**Manually** — drop the plugin (a `.py` file or a folder) into the plugins
directory: tray menu > *Open Plugins Folder*, or find it here:

| OS | Plugins directory |
| --- | --- |
| macOS | `~/Library/Application Support/nparseplus/plugins/` |
| Windows | `%LOCALAPPDATA%\nparseplus\plugins\` |
| Linux | `~/.config/nparseplus/plugins/` |
| Linux (Flatpak) | `~/.var/app/io.github.prokopto_dev.nparse_plus/config/nparseplus/plugins/` |

## Managing plugins

Settings > Plugins lists every discovered plugin with its status:

- **Active** — running.
- **Awaiting consent** — new; you'll be asked on next launch (or was asked
  and not answered).
- **Disabled** — you turned it off (uncheck *Enabled*); it stays installed.
- **Incompatible** — built for a different SDK or app version; the status
  tooltip says exactly why. Ask the author for an updated build.
- **Error** — the plugin crashed while loading; the tooltip has the reason
  and details are in `nparseplus.log`.

Enable/disable changes and uninstalls take effect the next time nParse+
starts. *Uninstall* moves the plugin into a `trash/` folder inside the
plugins directory rather than deleting it.

Plugin windows behave like every other nParse+ overlay: drag to move,
resize from any edge, toggle from the tray menu, positions remembered, and
they participate in Window Layouts. Plugins may also add their own pages to
the Settings window.

## Troubleshooting

- **A plugin broke my startup?** It shouldn't be able to — plugin failures
  are isolated and logged. But if something is badly wrong, launch with the
  environment variable `NPARSEPLUS_NO_PLUGINS=1` to skip all plugin loading,
  then disable or remove the culprit from the plugins folder.
- **Where are plugin errors logged?** `nparseplus.log` in the app's log
  directory (same place as `crash.log`); plugin lines are tagged
  `nparseplus.plugins.<id>`.
- **Where does a plugin keep its data?** In `plugin-data/<id>/` next to the
  plugins directory — separate from `settings.json`, so removing a plugin
  never touches your app settings.

## Building your own

See the [developer guide](developing.md) and the [API reference](api.md).
The examples shipped in the repository
([`examples/plugins/`](https://github.com/prokopto-dev/nparse-plus/tree/master/examples/plugins))
are the fastest starting point.
