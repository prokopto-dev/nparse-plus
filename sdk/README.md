# nparseplus-sdk

The stable contract for building [nParse+](https://github.com/prokopto-dev/nparse-plus)
plugins: addon windows, log parsers, event subscribers, and network pollers
that load into the app at runtime.

nParse+ is a log-parsing overlay for the EverQuest emulator server
[Project 1999](https://www.project1999.com/) — timers, DPS tracking,
maps, triggers, and shared raid information, driven entirely by reading the
game's own log file. This package is how you extend it.

```bash
pip install nparseplus-sdk
```

## A minimal plugin

```python
from datetime import timedelta

from nparseplus_sdk import NParsePlugin, PluginContext, PluginMeta


class HelloTimerPlugin(NParsePlugin):
    meta = PluginMeta(
        id="hello-timer",
        name="Hello Timer",
        version="1.0.0",
        requires_sdk=">=1.0,<2",
    )

    def activate(self, ctx: PluginContext) -> None:
        # Host classes are imported lazily so `import nparseplus_sdk` never
        # drags in the app (or Qt) — see the "No dependency on the app" note.
        from nparseplus_sdk.events import CommsChannel, CommsEvent
        from nparseplus_sdk.timers import TimerRow

        def on_comms(event) -> None:
            if event.channel is not CommsChannel.SAY or event.sender != "You":
                return
            if event.content.strip().lower() != "hello nparse":
                return
            ctx.timers.add_timer(
                TimerRow(
                    name="Hello from a plugin",
                    group=" Hello Timer",
                    updated_at=event.timestamp,
                    ends_at=event.timestamp + timedelta(seconds=30),
                    total_duration_s=30.0,
                )
            )

        ctx.subscribe(CommsEvent, on_comms)


def create_plugin() -> NParsePlugin:
    return HelloTimerPlugin()
```

Drop that in the app's plugins folder, approve it when asked, and saying
"hello nparse" in game starts a 30-second timer. Check your work before you
ship it:

```bash
nparseplus-plugin validate path/to/your_plugin.py
```

The full guide — the context API, plugin windows, settings pages, packaging
and publishing — is the **Plugins** section of the
[nParse+ documentation](https://prokopto-dev.github.io/nparse-plus/latest/plugins/).

## What you should know before building

- **Add-ons are opt-in.** Users must tick *Settings > Advanced > Enable
  plugins (add-ons)* and restart before anything loads. A user who only wants
  maps and timers never sees the feature at all.
- **Every plugin is consent-gated.** Yours does not run until the user
  approves it by name, and it runs with the same access to their machine as
  nParse+ itself. Write accordingly.
- **Versioning is independent of the app.** Declare the SDK range you built
  against in `PluginMeta.requires_sdk` (e.g. `">=1.0,<2"`); the app refuses
  plugins whose range does not admit the SDK version it bundles. 1.x is
  additive-only.
- **You can ship updates without a registry.** Set `PluginMeta.update_url`
  (SDK 1.1+) to an https index document listing your latest release and the
  app offers in-place updates for your plugin. It can only ever offer your
  own id, it never appears in the Browse list, and the sha256 in it is your
  own claim rather than anyone's review — see
  [Shipping updates without a registry](https://prokopto-dev.github.io/nparse-plus/plugins/developing/#shipping-updates-without-a-registry).
- **Your window can match the app.** `nparseplus_sdk.skin` (SDK 1.4+) is a
  small, Qt-free read surface over what nParse+ currently looks like — the
  colours, the type scale, ready-made overlay and config stylesheets — and
  `PluginWindow` is skinned by default even if you read none of it. The user
  can change skin live, so re-dress in `apply_skin()`; and the rule that
  governs the values is **the palette owns value, the skin owns hue** — see
  [Appearance & skins](https://prokopto-dev.github.io/nparse-plus/latest/plugins/appearance/).
- **No dependency on the app.** Plugins execute inside nParse+, which provides
  the runtime, so this package installs standalone with nothing but `pydantic`
  and `packaging`. `nparseplus_sdk.events`, `.timers`, `.ui`, `.eqfiles` and
  `.skin` re-export host names *lazily* — import them inside `activate()` (or
  your window module), not at plugin module scope. For
  standalone type-checking or tests you can install the app from source
  (`pip install git+https://github.com/prokopto-dev/nparse-plus`); it is not
  itself a PyPI package.

## Repository note

This package lives in the `sdk/` directory of the main
`prokopto-dev/nparse-plus` repository as an independent uv workspace member,
versioned and released independently of the app. It may eventually move to its
own repository (`prokopto-dev/nparseplus-sdk`).

Maintainers: the release procedure and the one-time publishing setup are in
[CONTRIBUTING.md](https://github.com/prokopto-dev/nparse-plus/blob/master/CONTRIBUTING.md#working-on-the-sdk).

> **The version has exactly one source:** `__version__` in
> `src/nparseplus_sdk/__init__.py`. `pyproject.toml` declares
> `dynamic = ["version"]` and hatchling reads that literal; `uv.lock` records
> no version for a dynamic-version package — so the wheel, the lock and the
> runtime constant cannot disagree. Do **not** reintroduce a literal
> `version =` in `pyproject.toml`, and do **not** derive `SDK_VERSION` from
> `importlib.metadata`: a PyInstaller-frozen app has no dist metadata, so any
> fallback next to that lookup is what every shipped build would report to
> `check_compat`.
