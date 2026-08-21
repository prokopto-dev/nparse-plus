# Versioning

Three version numbers cooperate in the plugin ecosystem, and each has
exactly one job.

| Coordinate | Owner | Job |
| --- | --- | --- |
| **App version** (`nparseplus`, e.g. `1.18.0`) | semantic-release on the app repo | The product. Each release bundles exactly **one** SDK version. |
| **SDK version** (`nparseplus-sdk`, currently `1.3.0`) | the SDK package (own semver, own `sdk-v*` tags) | **The contract.** The only number plugins and the app negotiate over. |
| **Plugin version** (`PluginMeta.version`) | the plugin author | The plugin's own releases; drives registry update detection. |

The SDK version has exactly one source: the `__version__` literal in
`sdk/src/nparseplus_sdk/__init__.py`. `sdk/pyproject.toml` is
`dynamic = ["version"]` and hatchling reads that literal, so the wheel, the
lock file and the runtime `SDK_VERSION` constant cannot disagree. It is
deliberately *not* derived from `importlib.metadata`: a frozen PyInstaller
build has no dist metadata, so a metadata lookup would fail in exactly the
builds every user runs, and whatever fallback sat beside it would become the
version reported to `check_compat`.

## The compatibility handshake

A plugin declares what it needs; the app enforces it at load time:

```python
meta = PluginMeta(
    id="my-plugin",
    name="My Plugin",
    version="1.4.0",
    requires_sdk=">=1.0,<2",     # PEP 440 range vs the bundled SDK version
    min_app_version="1.18.0",    # optional app floor (feature-based, e.g. an
)                                #   event class added in a given app release)
```

If `requires_sdk` doesn't admit the bundled SDK, or the app is older than
`min_app_version`, the plugin is refused with a readable reason (shown in
Settings > Plugins) — never a crash, never a half-load. The registry Browse
list runs the *same* check on index metadata, so incompatible plugins are
filtered before anything is downloaded.

`min_app_version` is only checked when an app version is available, which is
always true inside the app and optional for `nparseplus-plugin validate`
(pass `--app-version`).

## The SDK stability promise

- **SDK 1.x is additive-only.** Existing names in `nparseplus_sdk` keep
  their signatures and behavior for the entire 1.x line; new capabilities
  (context members, events, spec fields) may appear in minor releases.
  A plugin built against SDK 1.0 keeps loading against every 1.x.
- **Breaking changes require SDK 2.0**, and would ship with a transition
  plan (the app can bundle a 1.x compatibility shim for a window).
- Because of that promise, `requires_sdk=">=1.0,<2"` is the right default
  for almost every plugin. Raise the floor (e.g. `">=1.3,<2"`) only when
  you use a capability added in that minor.

### What each 1.x minor added

| SDK | Added |
| --- | --- |
| **1.1** | `PluginMeta.update_url` — an optional https index the app polls to offer in-place updates for a plugin distributed outside any registry ([Shipping updates](developing.md#shipping-updates-without-a-registry)). Declare `requires_sdk=">=1.1,<2"` only if your plugin is *useless* without it; a 1.0-declaring plugin loaded by an older app simply gets no update offers. |
| **1.2** | `ctx.eq_dir` + `ctx.eq_is_running()`, and the `nparseplus_sdk.eqfiles` re-export — enough for a plugin to edit a file in the EverQuest install the way the app does it (preflight, backup-first, splice one section). Declare `requires_sdk=">=1.2,<2"` if you touch the install: there is no graceful degradation, since on an older host the attribute is simply absent. |
| **1.3** | `ctx.add_window_timer()` / `ctx.add_window_series()` + the `WindowTimerLike` protocol — arm one variable respawn ("pop") window from a time of death, or every candidate window of a spawn that has more than one, with `TimerWindowOpenedEvent` / `TimerWindowClosedEvent` reachable through `nparseplus_sdk.events` ([Pop windows](../features/respawn-timers.md#pop-windows)). Declare `requires_sdk=">=1.3,<2"` if you arm one; on an older host the method is simply absent. **No `min_app_version` needed** — the app bundles exactly one SDK, so the range already implies the host-side classes shipping in the same release. |

What the promise does *not* cover: host objects reached through the context
(`ctx.timers`, `ctx.player`, `ctx.pigparse`, the classes behind
`nparseplus_sdk.events` / `.timers`) belong to the app, not the SDK. They
are stable in practice — the app's own code depends on them — but they move
on the app's schedule, and `min_app_version` is the lever for that, not
`requires_sdk`.

### Deprecation policy

Additive-only makes removal the interesting case. Within SDK 1.x nothing
public is ever removed or changed in meaning. When something needs to go:

1. **Announce.** The name is documented as deprecated in this
   documentation and in its own docstring, with the replacement named, in a
   normal SDK minor release. It keeps working, unchanged.
2. **Warn.** From the *next* SDK minor, using it emits a
   `DeprecationWarning` naming the replacement. It still works. Plugin
   authors see it in `nparseplus-plugin validate` output and in
   `nparseplus.log`.
3. **Remove.** Only in the next SDK **major**, never before, and never
   sooner than **six months** after step 1.

So the shortest possible life of a deprecated 1.x name is one minor cycle
plus a major bump, and the app will keep bundling a 1.x shim for a window
after 2.0 lands. If you see a deprecation, you have at least two SDK
releases and half a year to move.

Anything not in `nparseplus_sdk.__all__` — private names, module internals,
anything reached through a host object — is outside this policy and can
change in any release.

## Author release checklist

1. Bump `PluginMeta.version` (semver: breaking behavior for your users =
   major, features = minor, fixes = patch).
2. Keep `requires_sdk` honest — if you adopted a newer SDK capability,
   raise the floor. Set `min_app_version` if you depend on something the
   app added in a specific release.
3. Run `nparseplus-plugin validate` one more time against the version of the
   app you expect users to run (`--app-version`).
4. Tag `v<version>` — the template's release workflow refuses a tag that
   doesn't match `meta.version`, then builds the zip and computes its
   sha256.
5. Publish the release, then
   [`POST` it to the registry](registry.md#publishing-a-plugin) with your
   scoped token. The registry re-downloads the artifact and hashes it
   itself, so the digest you send is a cross-check rather than the published
   value. Listing gets your users one-click installs, a verified sha256, and
   "update available" notices on every later release.

    Optionally include plain-text release notes with that request — what
    changed, in a couple of sentences. nParse+ shows them beside the listing
    verbatim; they are not Markdown, so asterisks arrive as asterisks.

    If you'd rather not list it, distribution still works without the
    registry:

    - Point users at *Settings > Plugins > Install from URL…* with your
      release asset's https URL, and publish the sha256 next to it so
      careful users can check the download themselves — the app does not
      verify a hash on URL installs.
    - Or tell them to download the zip and use *Install from file…*.
    - Or declare an [update feed](developing.md#shipping-updates-without-a-registry)
      so those copies can still update themselves. Listing later changes
      none of this; it just adds Browse and the pinned hash.

## Where the SDK lives

The SDK is versioned and packaged independently of the app (currently in the
app repo's `sdk/` directory as a workspace member; planned to move to its
own repository). It is published on PyPI as
[`nparseplus-sdk`](https://pypi.org/project/nparseplus-sdk/) — see
[Installing the SDK](developing.md#installing-the-sdk). Releases go out on
`sdk-v<X.Y.Z>` tags through `.github/workflows/release-sdk.yml`, which
verifies the tag against `__version__`, smoke-tests the built wheel in a
clean venv, and publishes via PyPI trusted publishing behind a required
human approval.

Nothing about the handshake changes when the package moves repositories —
plugins already target the package, not the repo.
