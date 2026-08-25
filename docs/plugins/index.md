# Plugins

Since 1.18, nParse+ can load **plugins**: optional add-ons written in Python
that add their own overlay windows, react to log events, poll web services,
and contribute settings pages. Example ideas — a merchant window that tracks
what you're selling with live PigParse prices, a DKP tracker fed by your
guild's server, an auction bid watcher.

!!! danger "Plugins are third-party code"
    A plugin runs with the full permissions of nParse+ on your computer.
    nParse+ cannot verify what a plugin does — only install plugins from
    authors you trust. See [Security & trust](security.md).

## Step 1: turn plugins on

The whole add-on subsystem is **off by default** and stays off until you ask
for it. Open *nParse+ Settings* from the tray, go to **Advanced > Add-ons
(plugins)**, tick **Enable plugins (add-ons)**, click *Apply && Save*, and
**restart nParse+**.

Until you do that there is no Plugins settings page and no *Open Plugins
Folder* tray item — nothing plugin-related is even imported. The switch is
`settings.plugins.enabled`, read through `plugins_enabled()` in
`config/settings.py`; `pluginbootstrap.py` is the only place the machinery
loads from, and `create_app` calls it only when that returns true.

Why it ships off: add-ons are third-party code running with the same access
to your computer as nParse+ itself, and nParse+ needs none of them — maps,
timers, triggers, DPS and sharing are all built in. A default-on
plugin loader would be an attack surface every user carries for a feature
most users never use.

This one switch is the only part that needs a restart, and it needs one
precisely because "off" means the machinery is never imported: turning it on
has to import, ask consent for and start everything discovered, and turning
it off has to prove none of it is left. Individual add-ons need no restart —
install, enable and disable all take effect immediately (the exception is
updating a plugin *in place*, which cannot re-import safely mid-session).

## Step 2: install a plugin

After the restart, **Settings > Plugins** appears. Three ways in:

- *Install from file…* — choose a plugin `.zip` (or a single `.py` file).
- *Install from URL…* — paste an `https://` link to a plugin `.zip`
  (for example a GitHub release asset).
- *Browse registry…* — the curated [registry](registry.md).

The installer checks the archive is safe to extract, validates that the
plugin loads, and shows any advisory findings before finishing.

!!! warning "Only registry installs verify a checksum"
    A *Browse registry…* install downloads the artifact and refuses it
    unless its bytes hash to the sha256 the reviewed index recorded.
    *Install from URL…* has no expected hash to check against — it enforces
    https (on every redirect hop) and a size cap, and nothing more. Whoever
    controls that URL controls what you install.

A newly installed plugin loads **straight away** — you'll be asked to
confirm enabling it as soon as the install finishes, and it starts running
when you approve it. (If it landed somewhere the plugins-folder sweep does
not pick up, the row says *Installed — restart to load* instead.)

**Manually** — drop the plugin (a `.py` file or a folder) into the plugins
directory: tray menu > *Open Plugins Folder*, or find it here:

| OS | Plugins directory |
| --- | --- |
| macOS | `~/Library/Application Support/nparseplus/plugins/` |
| Windows | `%LOCALAPPDATA%\nparseplus\nparseplus\plugins\` |
| Linux | `~/.config/nparseplus/plugins/` |
| Linux (Flatpak) | `~/.var/app/io.github.prokopto_dev.nparse_plus/config/nparseplus/plugins/` |

The doubled `nparseplus\nparseplus` on Windows is real, not a typo:
`config/paths.py` asks platformdirs for `user_config_dir("nparseplus")`
without an app author, and platformdirs then uses the app name for both path
segments.

## Managing plugins

Settings > Plugins lists every discovered plugin with its status:

- **Active** — running.
- **Ready** — approved and enabled, and due to activate. You normally see
  this only if the plugin was installed but activation hasn't run for it.
- **Awaiting consent** — nParse+ has never been told whether to trust this
  one. The dialog runs before the plugin does anything: right after you
  install it, or at the next launch for a file you dropped into the plugins
  folder yourself.
- **Disabled** — you turned it off (uncheck *Enabled*), or you declined
  consent; it stays installed but inert.
- **Incompatible** — built for a different SDK or app version; the status
  tooltip says exactly why. Ask the author for an updated build.
- **Error** — the plugin crashed while loading; the tooltip has the reason
  and details are in `nparseplus.log`.
- **Duplicate id** — a plugin loaded earlier already claimed this
  `meta.id`, so this one is ignored. Remove one of them.

Two annotations can be appended to any of those:

- *— update available (vX)* — the registry index fetched this session lists
  a newer version than the one installed.
- *— tick disabled (too slow)* — the log driver evicted this plugin's
  periodic callback for repeatedly overrunning its time budget. The plugin
  is still running; only its tick stopped. See
  [the tick budget](developing.md#the-tick-budget).

The *Source* column shows where each plugin came from: a URL plus the first
characters of its sha256, `Local file (…)`, or **Sideloaded** for anything
copied into the folder by hand — no recorded source, no checksum.

Enabling, disabling and uninstalling all take effect immediately — the
add-on starts or stops there and then, and its windows, tray entries and
settings page go with it. See
[what applies now, and what needs a restart](../settings/plugins.md#what-applies-now-and-what-needs-a-restart).

**Uninstalling forgets the plugin.** *Uninstall* moves the plugin's code
into a `trash/` folder inside the plugins directory rather than deleting it,
then removes its consent record from `settings.json` and moves its
`plugin-data/<id>/` directory into `trash/plugin-data/` too
(`PluginHost.forget`). Nothing is destroyed — but anything that later claims
the same plugin id, from any source, is treated as the stranger it is: it
asks for consent again and starts with empty storage.

Plugin windows behave like every other nParse+ overlay: drag to move,
resize from any edge, toggle from the tray menu, positions remembered, and
they participate in Window Layouts. Plugins may also add their own pages to
the Settings window.

An add-on can also draw **inside** the Event Overlay rather than in a window
of its own — a [region](overlay-regions.md), placed from tray → Position Event
Overlay exactly like the CH lanes and the timer bars. A region is
display-only: it never receives a click, so anything interactive is a window.

## Troubleshooting

- **A plugin broke my startup?** It shouldn't be able to — plugin failures
  are isolated and logged. But if something is badly wrong, launch with the
  environment variable `NPARSEPLUS_NO_PLUGINS=1` to skip all plugin loading,
  then disable or remove the culprit from the plugins folder. The variable
  is a veto only: it can force plugins off, but it can never turn them on
  for someone who never opted in.
- **Where are plugin errors logged?** `nparseplus.log` in the app's log
  directory (same place as `crash.log`); plugin lines are tagged
  `nparseplus.plugins.<id>`. That directory is
  `~/Library/Logs/nparseplus/` on macOS,
  `%LOCALAPPDATA%\nparseplus\nparseplus\Logs\` on Windows, and
  `~/.local/state/nparseplus/log/` on Linux (relocated under
  `~/.var/app/io.github.prokopto_dev.nparse_plus/` in the Flatpak sandbox).
- **Where does a plugin keep its data?** In `plugin-data/<id>/` next to the
  plugins directory — separate from `settings.json`, so a plugin's own state
  can never corrupt your app settings. It goes to the trash folder when you
  uninstall the plugin.
- **I updated a plugin and wasn't asked to approve it again.** That's
  expected: consent is recorded per plugin id, not per version. See
  [Security & trust](security.md#a-version-bump-does-not-re-ask).

## Building your own

See the [developer guide](developing.md) and the [API reference](api.md).
The examples shipped in the repository
([`examples/plugins/`](https://github.com/prokopto-dev/nparse-plus/tree/master/examples/plugins))
are the fastest starting point.
