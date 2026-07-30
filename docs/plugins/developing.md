# Developing plugins

nParse+ plugins are written against **`nparseplus-sdk`** — a small,
separately versioned Python package that defines the stable contract. The
app bundles one SDK version; your plugin declares the range it supports and
incompatible combinations are refused cleanly.

```bash
pip install nparseplus-sdk           # types, base classes, validate CLI
# for full validation + running the real app from source:
pip install git+https://github.com/prokopto-dev/nparse-plus
```

!!! note
    Until the SDK's PyPI release lands you can install it from a repo
    checkout: `pip install ./sdk`.

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
        min_app_version="1.15.0",    # optional
        author="You",
    )

    def activate(self, ctx: PluginContext) -> None:
        ...   # register everything here

    def deactivate(self) -> None:
        ...   # optional; runs at app shutdown

def create_plugin() -> MyPlugin:
    return MyPlugin()
```

Everything a plugin may touch arrives through the
[`PluginContext`](api.md#plugincontext) handed to `activate`:

| Capability | Call | Notes |
| --- | --- | --- |
| React to events | `ctx.subscribe(EventClass, fn)` | classes from `nparseplus_sdk.events` |
| Parse novel log lines | `ctx.add_parser(parser)` | runs **after** every built-in |
| Periodic work | `ctx.add_tick(fn)` | ~100 ms, driver thread |
| Network fetches | `ctx.submit(fetch, apply)` | never block a tick/handler |
| PigParse REST | `ctx.pigparse` | e.g. `item_prices(server, names)` |
| Timer rows | `ctx.timers` + `nparseplus_sdk.timers` | spell-timer window sections |
| Text-to-speech | `ctx.speaker.speak("text")` | the app's shared voice |
| Persistent data | `ctx.storage` | JSON dict + a private data dir |
| Overlay windows | `ctx.add_window(PluginWindowSpec(...))` | see below |
| Settings pages | `ctx.add_settings_page(PluginSettingsPageSpec(...))` | |
| Logging | `ctx.logger` | lands in `nparseplus.log` |

## The threading contract (read this one section)

- `activate(ctx)` runs once on the **GUI thread before the log driver
  starts** — registrations are race-free, but never block here.
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

The factory runs on the GUI thread with a `PluginWindowContext`. Subclass
`nparseplus_sdk.ui.PluginWindow` (import it **inside** the factory, not at
module top level, so Qt-free environments can still import your plugin) to
get the full overlay recipe — frameless, drag to move, resize from any
edge, quit safety:

```python
def make_window(wctx):
    from nparseplus_sdk.ui import PluginWindow
    from PySide6.QtWidgets import QLabel, QVBoxLayout

    class MyWindow(PluginWindow):
        def __init__(self, wctx):
            super().__init__(wctx)
            layout = QVBoxLayout()
            layout.addWidget(QLabel("hello", self))
            self.setLayout(layout)
            self.restore_visibility()   # honor the saved shown state

    return MyWindow(wctx)
```

## Validate, test, package

**Validate** — the SDK installs a CLI that loads your plugin exactly like
the app does, checks metadata and version compatibility, activates it
against a fake context, and runs the advisory static scan:

```bash
nparseplus-plugin validate my_plugin.py
nparseplus-plugin validate my_plugin_pkg/ --app-version 1.15.0 --json
```

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

**Package** — zip your plugin so the archive contains exactly one top-level
entry: the package folder (with `__init__.py`) or the single `.py` file.
That zip is what users feed to Settings > Plugins > *Install from file/URL*.
Multi-file plugins must be packages using **relative imports**
(`from .helper import x`) — plugins are imported under a private namespace,
never via `sys.path`.

Constraints to design for:

- **Dependencies:** inside the shipped (frozen) app only the libraries
  nParse+ bundles are importable — `httpx`, `pydantic`, `PySide6`,
  `platformdirs`, `packaging`, the standard library. No `pip install` at
  runtime; vendoring pure-Python code inside your package is fine.
- **Versioning:** bump `meta.version` on every release; set `requires_sdk`
  honestly. The app refuses (never crashes on) incompatible plugins.

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
release whose body contains the ready-made [registry](registry.md) entry
JSON. See also the [versioning rules](versioning.md).

## Learn from the examples

[`examples/plugins/`](https://github.com/prokopto-dev/nparse-plus/tree/master/examples/plugins)
in the repository:

- **`hello_timer.py`** — minimal: one event subscription, a timer row, TTS.
- **`merchant_prices/`** — the full API: auction tracking, storage,
  throttled PigParse price polling, an overlay window, a settings page.
