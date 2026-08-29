# Developing plugins

nParse+ plugins are written against **`nparseplus-sdk`** — a small,
separately versioned Python package that defines the stable contract. The
app bundles one SDK version; your plugin declares the range it supports and
incompatible combinations are refused cleanly.

## Installing the SDK

```bash
# the SDK alone: types, base classes, the validate CLI
pip install nparseplus-sdk

# ...or from a checkout of the app repo
pip install ./sdk

# for full type checking and for running the real app from source, also:
pip install git+https://github.com/prokopto-dev/nparse-plus
```

In a `pyproject.toml`, that first form is:

```toml
dependencies = [
  "nparseplus-sdk>=1.0,<2",
]
```

The app itself is not a PyPI package — that last line stays a git install,
and you only need it for type checking and live runs.

## Zero to running

1. **Turn plugins on in the app.** *nParse+ Settings > Advanced > Add-ons
   (plugins) >* tick **Enable plugins (add-ons)**, *Apply && Save*, then
   restart nParse+. Add-ons are off by default; before this step there is no
   Plugins page and no *Open Plugins Folder* tray item.
2. **Install the SDK** into a virtualenv (above) so you get the types and
   the `nparseplus-plugin` CLI.
3. **Write the plugin.** Start by copying
   [`examples/plugins/hello_timer.py`](https://github.com/prokopto-dev/nparse-plus/blob/master/examples/plugins/hello_timer.py)
   — one file, one subscription, a timer row and TTS.
4. **Validate it:** `nparseplus-plugin validate hello_timer.py`. This
   imports your module and calls `activate()` against a fake context, so it
   catches most mistakes without launching the app.
5. **Drop it into the plugins folder.** Tray > *Open Plugins Folder* (or the
   [paths table](index.md#step-2-install-a-plugin)), and copy the `.py` file
   or the package directory in.
6. **Restart nParse+.** A consent dialog appears naming your plugin; choose
   *Enable plugin*.
7. **Check it loaded:** Settings > Plugins should show it as **Active**. If
   not, the status and its tooltip say why, and `nparseplus.log` has the
   traceback.

## Plugin anatomy

A plugin is one `.py` file or one package directory exposing a module-level
`create_plugin()` factory:

```python
from nparseplus_sdk import NParsePlugin, PluginContext, PluginMeta

class MyPlugin(NParsePlugin):
    meta = PluginMeta(
        id="my-plugin",              # ^[a-z][a-z0-9_-]{1,39}$ — your identity
        name="My Plugin",
        version="1.0.0",
        requires_sdk=">=1.0,<2",     # PEP 440 range vs the app's bundled SDK
        min_app_version="1.18.0",    # optional
        author="You",
    )

    def activate(self, ctx: PluginContext) -> None:
        ...   # register everything here

    def deactivate(self) -> None:
        ...   # optional; runs when the user disables you, and at shutdown

def create_plugin() -> MyPlugin:
    return MyPlugin()
```

Everything a plugin may touch arrives through the
[`PluginContext`](api.md#plugincontext) handed to `activate`:

| Capability | Call | Notes |
| --- | --- | --- |
| React to events | `ctx.subscribe(EventClass, fn)` | classes from `nparseplus_sdk.events` |
| Parse novel log lines | `ctx.add_parser(parser)` | runs **after** every built-in |
| Periodic work | `ctx.add_tick(fn)` | ~100 ms, driver thread, **250 ms budget** |
| Network fetches | `ctx.submit(fetch, apply)` | never block a tick/handler |
| PigParse REST | `ctx.pigparse` | e.g. `item_prices(server, names)` |
| Timer rows | `ctx.timers` + `nparseplus_sdk.timers` | Timers window sections |
| Text-to-speech | `ctx.speaker.speak("text")` | the app's shared voice |
| Persistent data | `ctx.storage` | JSON dict + a private data dir |
| Overlay windows | `ctx.add_window(PluginWindowSpec(...))` | see below |
| Settings pages | `ctx.add_settings_page(PluginSettingsPageSpec(...))` | |
| Logging | `ctx.logger` | lands in `nparseplus.log` |

## The threading contract (read this one section)

- `activate(ctx)` runs once on the **GUI thread**, and `deactivate()` runs
  there too. At startup that is before the log driver starts; when the user
  ticks your add-on's box mid-session it is while the driver is already
  running. Either way your registrations are race-free: the host lands
  parsers and ticks at a driver-loop boundary, never inside a line. Still
  **never block here**, and do your work in your callbacks rather than
  reaching for driver-thread state during `activate()`.
- Subscriptions, parsers, and ticks run on the app's single **driver
  thread**. That is the only thread where `ctx.timers` and event handling
  are safe — which is exactly where your callbacks run, so mutate freely
  inside them and nowhere else.
- **Never do network I/O in a handler or tick.** Use
  `ctx.submit(fetch, apply)`: `fetch()` runs on a worker thread and
  `apply(result)` is delivered back onto the driver thread.
- Windows and settings pages run on the **GUI thread**. Read plugin state
  from a QTimer poll of a snapshot (see the merchant example) or connect to
  `wctx.bridge.event_received` for pushed events.

### The tick budget

One thread does log tailing, the parser chain, every timer countdown, the
DPS fight tracker and the sharing inbox. A tick that takes 250 ms therefore
costs the *whole app* 250 ms — two and a half missed poll intervals and a
visible stutter in every countdown. So the driver supervises plugin ticks
(`core/driver.py`):

- Each `ctx.add_tick` callback is timed on every run.
- A run over **`TICK_BUDGET_S` = 250 ms** counts as a breach. A run under it
  resets the counter — only *consecutive* breaches matter, so one GC pause
  or cold import costs you nothing.
- **Two consecutive breaches and the tick is removed for the rest of the
  session.** The plugin stays active — its subscriptions, parsers and
  windows keep working — but that callback never runs again, the eviction is
  logged at ERROR, and Settings > Plugins annotates your row *tick disabled
  (too slow)*.

Write ticks accordingly: they should be a cheap "is anything due?" check
that hands real work to `ctx.submit`. If you need to do something expensive
on a schedule, do it in the `fetch` closure, not in the tick. The merchant
example's `_tick` is the shape to copy — it compares timestamps, then
returns or submits.

### Your numbers are on screen

Your handlers, parsers and ticks are timed while the app runs, and
[Settings → Plugins](../settings/plugins.md#the-performance-column) shows
the result in a Performance column per add-on: events per second, average /
p95 / worst callback duration, and roughly what share of the log thread you
are using. It costs a fraction of a microsecond per callback and the user
can switch it off, but assume it is on and that someone is looking at your
row.

Nothing is throttled on the strength of those numbers — only the tick
watchdog above ever removes anything — but they are the fastest way to find
out whether the handler you just wrote is a problem. Subscribe to
`LineEvent` and you will see it immediately: that one fires for **every**
line of the log, so an expensive handler on it is the most reliable way to
make nParse+ stutter.

## Events vs parsers

The app's parser chain is first-match-wins, and the built-ins already
consume everything EverQuest normally logs — including **all chat lines**
(say/tell/auction/…), which become typed `CommsEvent`s. So:

- to react to chat or anything the app already understands, **subscribe**
  (`nparseplus_sdk.events.CommsEvent`, `LineEvent` is the always-fires
  firehose, plus ~40 more);
- reserve `ctx.add_parser` for log lines no built-in claims (custom
  server messages, novel formats).

## Windows

Declare windows during `activate`; the app materializes them, adds a tray
toggle and an in-game chat command (`toggle_<id>_<key>`), and persists
geometry/opacity/on-top per window automatically:

```python
from nparseplus_sdk import PluginWindowSpec

ctx.add_window(PluginWindowSpec(key="main", title="My Window",
                                factory=make_window))
```

The factory runs on the GUI thread with a
[`PluginWindowContext`](api.md#window-settings-page-specs). Subclass
`nparseplus_sdk.ui.PluginWindow` to get the full overlay recipe — frameless,
drag to move, resize from any edge, quit safety.

Doing so also earns your window a row in the user's
[Settings → Windows](../settings/windows.md#plugin-windows) grid, labelled
`<your plugin name> — <window title>`, so they get opacity and always-on-top
with no work on your part. Two consequences: keep `title` short and human,
because it is what they read there; and note the opacity slider has a 10%
floor (a window must stay findable), so a `default_state` below `0.1` is
raised to it the first time they hit Save. A factory that returns a plain
widget instead of a `PluginWindow` still opens and still gets its tray entry,
but has no overlay state to expose, so it gets no row.

**Keep Qt out of your plugin's top-level module.** nParse+ imports that
module to read your metadata, and so do `nparseplus-plugin validate` and
your unit tests, which may run without PySide6 (and `nparseplus_sdk.ui`
resolves `PluginWindow` from the *host*, so it only works inside the app).
Both shipped examples do this by putting the Qt code in a separate module
and importing it lazily from the factory:

```python
# my_plugin/__init__.py — no Qt imports at this level
def _make_window(self, wctx):
    from .window import MyWindow      # imported only when the app builds it
    return MyWindow(wctx, self)
```

```python
# my_plugin/window.py — Qt at module top level is fine here
from PySide6.QtWidgets import QLabel, QVBoxLayout
from nparseplus_sdk.ui import PluginWindow

class MyWindow(PluginWindow):
    def __init__(self, wctx, plugin):
        super().__init__(wctx)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("hello", self))
        self.setLayout(layout)
        self.restore_visibility()   # honor the saved shown state — call last
```

For a single-file plugin, do the same imports inside the factory function
itself. Either way, the rule is the same: nothing Qt at import time.

### Making it look like nParse+

Since SDK 1.4 a `PluginWindow` arrives **skinned**: the active skin's plate and
glass are painted behind it and its labels wear the overlay type treatment, so
an add-on that writes no styling at all still matches the app under all three
skins.

To style more than that, read `nparseplus_sdk.skin` — never hardcoded hex, and
never `nparseplus.ui.skins`:

```python
from nparseplus_sdk import skin

class MyWindow(PluginWindow):
    def skin_stylesheet(self) -> str:
        app = skin.current()
        return f"#Total {{ {app.typography(skin.NUMERIC_TEXT, color=app.heading)} }}"
```

`skin_stylesheet()` is re-read on every skin, font-size and frame-opacity
change — the user can switch skins from the tray mid-fight — so never cache the
snapshot. For work a stylesheet cannot do, override `apply_skin()` and call
`super().apply_skin()` first. Sizes are multipliers of the
user's font size, never px. And one rule carries the whole page: **the palette
owns value, the skin owns hue** — paint grounds and text from `app.text` /
`app.surface`, and use `app.accent` only as an accent, or your window is gold
on gold under Velious. Full guide: [Appearance & skins](appearance.md).

## Overlay regions

Since SDK 1.5 a plugin can also claim a **region inside the Event Overlay** —
the surface that draws the CH lanes, the alert headline and the timer bars on
the game — instead of opening a window of its own:

```python
from nparseplus_sdk import OverlayRegionSpec

ctx.add_overlay_region(OverlayRegionSpec(
    key="kills", title="Kills", factory=make_region,
    has_content=lambda: bool(my_rows),
))
```

**A region never receives a click** — not a press, a hover, a wheel or a key.
The overlay is transparent for input and Qt has no per-child exemption, so
that is permanent by design. Text, images and status panels; if your add-on
needs input, use `ctx.add_window` above. Same lazy-import rule as a window:
`nparseplus_sdk.ui.PluginOverlayRegion` resolves from the host, so build it
inside the factory.

Declare **both** `requires_sdk=">=1.5,<2"` and `min_app_version="2.30.0-beta.1"`:
the method lives in the host, and an SDK range alone
[does not promise a host that implements it](versioning.md#the-sdk-range-alone-is-not-a-promise-about-the-host).
Keep the `-beta.1` — regions debut in a beta build, and pinning the stable
`2.30.0` instead would refuse every host that has the feature
([why](versioning.md#a-capability-that-debuts-in-a-prerelease)).
Full guide: [Event overlay regions](overlay-regions.md).

## Consent, from your side

- **Your plugin is inert until the user answers.** nParse+ shows a dialog
  with your `name`, `version`, `author` and install location — right after
  the install finishes, or at the first launch after someone drops your
  files into the plugins folder by hand. `activate()` is not called until
  the user accepts. Fill in `description` and `author` — that dialog is the
  only pitch you get.
- **A decline is remembered.** Declining records an approved-but-disabled
  entry, so the user is never re-asked. Your plugin shows as *Disabled* and
  they have to enable it in Settings > Plugins by hand.
- **A version bump does not re-prompt.** Consent keys on `meta.id`, so an
  update installs and runs without another dialog. Treat that as a
  responsibility, not a convenience — see
  [Security & trust](security.md#a-version-bump-does-not-re-ask).
- **Changing your `meta.id` costs you every user's consent and their stored
  data.** Pick it once.
- **Your distribution root name is part of your identity too.** That is the
  package directory (or the `.py` filename) inside your zip. An update is
  installed over the copy already on disk, and nParse+ refuses an "update"
  whose root would land somewhere else — otherwise it would install a second
  copy beside the first, both claiming your id. Renaming `my-plugin/` to
  `my_plugin/` between releases therefore strands your users on the
  uninstall-and-reinstall path, which loses their stored data. Pick that
  once as well.

## Shipping updates without a registry

If your plugin is listed in a registry, updates come for free: the app polls
every registry the user has ticked and offers a newer *compatible* release.
For a plugin distributed any other way — a link on a forum, a release page,
a zip you hand out — set `update_url` to an https index document and the app
will poll it for your plugin alone:

```python
meta = PluginMeta(
    id="my-plugin",
    name="My Plugin",
    version="1.2.0",
    update_url="https://you.example/my-plugin/index.json",
)
```

The document is the ordinary [registry index format](registry.md#index-format-schema-1) —
usually a one-entry file you regenerate on each release:

```json
{
  "schema_version": 1,
  "plugins": [{
    "id": "my-plugin",
    "name": "My Plugin",
    "latest": {
      "version": "1.3.0",
      "url": "https://you.example/my-plugin/my-plugin-1.3.0.zip",
      "sha256": "…64 hex chars…",
      "requires_sdk": ">=1.0,<2"
    }
  }]
}
```

Rules worth knowing before you rely on it:

- **Your feed can only offer your own id.** Any listing whose `id` is not the
  id of the plugin that declared the feed is discarded. A feed is an update
  channel for one plugin, not a registry.
- **It never appears in Browse.** Users discover plugins through registries;
  a feed only updates something already installed.
- **A registry install wins.** If the user got your plugin from a registry,
  that registry's offer is preferred and your feed's offer counts as a
  different source — which the app makes the user confirm.
- **The sha256 is your own claim.** It proves the download matches what you
  published; it does not mean anyone reviewed it. The app says as much next
  to the offer.
- **`update_url` must be https, or your plugin will not load.** That is
  deliberate — `nparseplus-plugin validate` catches it before your users do.
- The user can switch the whole thing off with **Settings > Plugins > Check
  for plugin updates**, and it is never polled while your plugin is disabled
  or its consent was declined.

Version comparison is PEP 440 on `meta.version` vs the feed's `latest.version`,
strictly greater — so keep bumping it.

## Testing your plugin locally

Installing, enabling, disabling and uninstalling all take effect
**immediately** — no restart. What does *not* is **editing your source**:
your module is imported once per session, so a code change needs a relaunch.
(Same reason updating an installed plugin in place keeps its restart notice:
re-importing would replace only the top-level module, leaving your
submodules stale and the old objects holding the old globals.)

The practical loop, fastest first:

1. **Unit tests against `FakePluginContext`** — no app, no Qt, no restart.
   This is where the bulk of your iteration should happen.
2. **`nparseplus-plugin validate <path>`** — imports and activates your
   plugin the way the app does.
3. **In the real app.** Either copy the plugin into the plugins folder, or
   **symlink it** so you edit in place:

    ```bash
    # macOS / Linux
    ln -s ~/code/my-plugin/my_plugin \
      ~/Library/Application\ Support/nparseplus/plugins/my_plugin
    ```

    ```powershell
    # Windows (developer mode or an elevated shell)
    New-Item -ItemType SymbolicLink `
      -Path "$env:LOCALAPPDATA\nparseplus\nparseplus\plugins\my_plugin" `
      -Target "C:\code\my-plugin\my_plugin"
    ```

    Then **restart nParse+ after every change.** (Symlinks are rejected
    *inside* an installed zip archive; a symlink you create yourself in the
    plugins folder is just a directory entry and loads normally.)

To exercise a plugin without the game running, point nParse+ at a scratch
log directory and append timestamped lines to an
`eqlog_<Name>_<server>.txt` file there — see the repo's `CLAUDE.md` for the
line format.

## Debugging

- **`ctx.logger` is a child of the app's logger tree**, named
  `nparseplus.plugins.<your-id>`. Everything it emits lands in
  `nparseplus.log` in the app's log directory —
  `~/Library/Logs/nparseplus/` (macOS),
  `%LOCALAPPDATA%\nparseplus\nparseplus\Logs\` (Windows),
  `~/.local/state/nparseplus/log/` (Linux). Read that file, not stderr:
  a frozen build has no visible console, so `print()` goes nowhere.
- **Exceptions are caught, not swallowed.** Every callback the host wires up
  is guarded per plugin and the traceback is logged with your plugin id.
  A raise in `activate()` flips your plugin to **Error** in Settings >
  Plugins and unwinds whatever it had already registered.
- **`NPARSEPLUS_NO_PLUGINS=1` skips all plugin loading.** Use it to confirm
  a startup problem is yours, and to bisect by moving plugins in and out of
  the folder between runs.
- **Slow-tick evictions** are logged at ERROR with the measured duration and
  shown on your row in Settings > Plugins.

## Validate, test, package

**Validate** — the SDK installs a CLI that loads your plugin exactly like
the app does, checks metadata and version compatibility, activates it
against a fake context, and runs the advisory static scan:

```bash
nparseplus-plugin validate my_plugin.py
nparseplus-plugin validate my_plugin_pkg/ --app-version 1.18.0 --json
```

Exit status is 0 only when there are no errors; advisory warnings never fail
it.

**Test** — `nparseplus_sdk.testing.FakePluginContext` records everything
your plugin registers and lets tests drive it without the app:

```python
from nparseplus_sdk.testing import FakePluginContext

def test_activation():
    ctx = FakePluginContext()
    create_plugin().activate(ctx)
    assert len(ctx.windows) == 1
    ctx.run_submitted()          # execute queued (fetch, apply) pairs
```

`ctx.publish(event)` drives your subscriptions (exact-type match, like the
real bus), and `ctx.speaker.spoken` / `ctx.pigparse.calls` record what you
asked for.

**Package** — zip your plugin so the archive contains exactly one top-level
entry: the package folder (with `__init__.py`) or the single `.py` file.
That zip is what users feed to Settings > Plugins > *Install from file/URL*.
Multi-file plugins must be packages using **relative imports** — either form
works (`from .helper import x` or `from . import helper`). Plugins are
imported under the private `nparseplus_user_plugins.*` namespace via
`spec_from_file_location`, never via `sys.path`; absolute imports of your own
modules (`import helper`) will not resolve.

## Third-party dependencies: there aren't any

**Your plugin may not depend on anything from PyPI.** Every end-user build
is a frozen PyInstaller bundle: there is no `pip`, no site-packages on
`sys.path`, and no way for the app to install anything at runtime. If your
plugin imports a package the app does not already bundle, it will work in
your development checkout and fail for every real user.

Your options:

- **Vendor it.** Copy pure-Python source into your package and import it
  relatively (`from .vendored.thing import x`). Mind the licence.
- **Use what the app already ships.** As of 1.18 the app's own runtime
  dependencies are `PySide6`, `pydantic`, `httpx`, `platformdirs`,
  `packaging`, `colorhash`, `pathvalidate`, `websocket-client`, `certifi`
  and `nparseplus-sdk` (`[project.dependencies]` in the app's
  `pyproject.toml`). They are importable inside the frozen app today —
  but they are the *app's* dependencies, not part of the SDK contract, and
  can be dropped or replaced in any release without a major bump. Depending
  on `httpx` and `pydantic` is fairly safe (the plugin machinery itself uses
  both); depending on `colorhash` is a bet.
- **Standard library**: fine, with one caveat — PyInstaller bundles the
  stdlib modules it can trace, plus a handful the spec names explicitly, and
  the spec excludes `tkinter` outright. Common modules are present; if you
  reach for something exotic, verify it against an actual frozen build
  rather than your dev environment.
- **C extensions are impossible.** A compiled wheel cannot be added to a
  frozen bundle from the plugins folder. Pure Python only.

## What the SDK deliberately does not let you do

Knowing the walls up front saves you designing into one:

- **No hot reload of your code.** Install, enable, disable and uninstall are
  live; a source edit (or an in-place update of an installed plugin) still
  needs a relaunch, because a module is imported once per session.
- **No inter-plugin dependencies.** Plugins can't import, discover, or call
  each other; load order is not a contract. If two of your plugins need to
  cooperate, make them one plugin.
- **No publishing to the bus.** `ctx.subscribe` is read-only access to the
  event stream — there is no `publish`. You cannot synthesize app events for
  built-in handlers to consume.
- **No replacing or reordering built-in parsers.** `ctx.add_parser` appends
  to the end of a first-match-wins chain, so your parser only ever sees
  lines no built-in claimed. You cannot intercept, pre-empt, or unregister
  a built-in.
- **No tray items of your own.** You get one tray toggle per window you
  declare, automatically. There is no API to add arbitrary tray entries or
  menu items.
- **No threads you own.** Use `ctx.submit(fetch, apply)`. A thread you start
  yourself has no safe way to touch `ctx.timers` or the bus, won't be joined
  at shutdown, and won't be unwound if your `activate` fails.
- **No async.** Every callback in the contract is a plain synchronous
  function. There is no event loop to await on.
- **No settings of your own in `settings.json`.** The app's `Settings` model
  drops unknown keys; persist through `ctx.storage`.
- **No access to the host beyond `PluginContext`.** There is no supported
  route to `Backend`, and reaching for one through private attributes will
  break without notice.

**Distributing via pip (optional):** for users who run nParse+ from source,
you can also publish your plugin as a normal package exposing the
entry point group `nparseplus.plugins`:

```toml
[project.entry-points."nparseplus.plugins"]
my-plugin = "my_plugin:create_plugin"
```

The frozen app cannot see pip-installed packages — the plugins directory is
the mechanism that works for everyone, so ship the zip either way.

## Starting from the repo template

The fastest path to a publishable plugin is the repository template
(currently at
[`templates/plugin-repo/`](https://github.com/prokopto-dev/nparse-plus/tree/master/templates/plugin-repo)
in the app repo; moving to its own "Use this template" repository). It
ships a working starter plugin, unit tests on `FakePluginContext`, a CI
workflow that runs `nparseplus-plugin validate` on every push, and a
release workflow that — on a `vX.Y.Z` tag matching your `meta.version` —
builds the installable zip, computes its sha256, and publishes a GitHub
release carrying both. Listing it is a separate authenticated `POST` to the
[registry](registry.md#publishing-a-plugin) from your own pipeline. See also
the [versioning rules](versioning.md).

## Learn from the examples

[`examples/plugins/`](https://github.com/prokopto-dev/nparse-plus/tree/master/examples/plugins)
in the repository:

- **`hello_timer.py`** — minimal: one event subscription, a timer row, TTS.
- **`merchant_prices/`** — the full API: auction tracking, storage,
  throttled PigParse price polling, an overlay window, a settings page. Its
  `window.py` is also the reference for
  [skinning a window](appearance.md) from `nparseplus_sdk.skin`.
- **`kill_ticker.py`** — an [event overlay region](overlay-regions.md): a
  display-only list drawn inside the overlay, fed from the Qt bridge, that
  brings the overlay on screen when it has something to say.
