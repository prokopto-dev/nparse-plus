# Versioning

Three version numbers cooperate in the plugin ecosystem, and each has
exactly one job.

| Coordinate | Owner | Job |
| --- | --- | --- |
| **App version** (`nparseplus`, e.g. `1.18.0`) | semantic-release on the app repo | The product. Each release bundles exactly **one** SDK version. |
| **SDK version** (`nparseplus-sdk`, currently `1.5.0`) | the SDK package (own semver, own `sdk-v*` tags) | **The contract.** The only number plugins and the app negotiate over. |
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
| **1.2** | `ctx.eq_dir` + `ctx.eq_is_running()`, and the `nparseplus_sdk.eqfiles` re-export — enough for a plugin to edit a file in the EverQuest install the way the app does it (preflight, backup-first, splice one section). Declare `requires_sdk=">=1.2,<2"` **and `min_app_version="2.14.0"`** if you touch the install: there is no graceful degradation, since on an older host the attribute is simply absent, and the range alone [does not promise a host that has it](#the-sdk-range-alone-is-not-a-promise-about-the-host) — v2.13.0 admits SDK 1.2 and has no `ctx.eq_dir`. The *host* half of `eq_is_running()` moves on the app's schedule rather than the SDK's, though — it answered `False` on Windows until [#33](https://github.com/prokopto-dev/nparse-plus/issues/33), with no SDK change either side of that fix, so `min_app_version` is the lever if your plugin depends on the answer being truthful there. |
| **1.3** | `ctx.add_window_timer()` / `ctx.add_window_series()` + the `WindowTimerLike` protocol — arm one variable respawn ("pop") window from a time of death, or every candidate window of a spawn that has more than one, with `TimerWindowOpenedEvent` / `TimerWindowClosedEvent` reachable through `nparseplus_sdk.events` ([Pop windows](../features/respawn-timers.md#pop-windows)). Declare `requires_sdk=">=1.3,<2"` **and `min_app_version="2.15.0"`** if you arm one — see [the range alone is not a promise about the host](#the-sdk-range-alone-is-not-a-promise-about-the-host). |
| **1.4** | `nparseplus_sdk.skin` — a curated, Qt-free read surface over what the app currently looks like (the `AppSkin` snapshot, the type roles, the semantic accents, ready-made overlay/config stylesheets), plus `PluginWindow.skin_stylesheet()` and a default `PluginWindow.apply_skin()`, so a window that overrides nothing is skinned under all three skins and one that styles itself is composed with rather than replaced ([Appearance & skins](appearance.md)). **1.4.1** added `AppSkin.band` — the skin's real selection fill — and *corrected* `AppSkin.accent_text`, which shipped naming a pairing that measured 3.4:1 on Ledger's band, below WCAG AA; it is now the same value as `heading` and is **deprecated**, kept for the whole 1.x line because 1.x is additive-only and app v2.26.0 shipped it. Removal is an SDK 2.0 decision. 1.4.1 also fixed `PluginWindow` discarding the stylesheet of a window written before 1.4, and moved the first `skin_stylesheet()` call out of the base constructor. **The floor depends on which part you use, because 1.4.1 added host code too.** `nparseplus_sdk.skin` and the `AppSkin` snapshot shipped in app **2.26.0** (SDK 1.4.0); `AppSkin.band`, `PluginWindow.skin_stylesheet()` and the corrected `accent_text` shipped in app **2.26.2** (SDK 1.4.1) — verified against the tags: 2.26.0 has neither `band` nor `skin_stylesheet`. So declare `requires_sdk=">=1.4,<2"` with `min_app_version="2.26.0"` for the snapshot alone, and **`min_app_version="2.26.2"`** if you touch anything 1.4.1 added. A `>=1.4` range admits SDK 1.4.1 onto a 2.26.0 host, so the range alone [does not promise a host that implements it](#the-sdk-range-alone-is-not-a-promise-about-the-host) — and the `skin_stylesheet()` case fails **silently**, since an older base class simply never calls your override. The two failures differ and both need the pin: `nparseplus_sdk.skin` does not resolve on that host at all, so the import raises and there is nothing to degrade to, while a `skin_stylesheet()` override is simply never called — silent, which is worse. A plugin that only wants to *stop looking out of place* needs no declaration at all — the default dressing is the host's, not the SDK's. |
| **1.5** | `ctx.add_overlay_region()` + `OverlayRegionSpec` / `OverlayRegionContext`, and `nparseplus_sdk.ui.PluginOverlayRegion` — a plugin can claim a region **inside** the Event Overlay and draw text, images or a status panel there instead of opening a window ([Event overlay regions](overlay-regions.md)). Regions are **display-only, permanently**: the overlay window carries `WindowTransparentForInput` and Qt has no per-child exemption, so nothing in a region ever receives a click, and the spec deliberately carries no input-related field — an additive-only 1.x makes a speculative one permanent. Add-ons that need input ship a window. Declare `requires_sdk=">=1.5,<2"` **and `min_app_version="2.29.0"`** if you contribute one — see [the range alone is not a promise about the host](#the-sdk-range-alone-is-not-a-promise-about-the-host). |

### The SDK range alone is not a promise about the host

`requires_sdk` is weighed against the SDK version the app **resolved**, not
against the contract the app **implements**. Inside a shipped DMG, `.deb`,
`.zip` or Flatpak those are the same thing — the bundle contains exactly one
SDK, frozen at build time. A plain `pip`/source install is where they come
apart, and it is the same seam
[`tests/test_sdk_floor.py`](https://github.com/prokopto-dev/nparse-plus/blob/master/tests/test_sdk_floor.py)
exists for, seen from the other side.

Every released app declares a floor, not a pin — v2.28.0 asks for
`nparseplus-sdk>=1.4,<2`. So once SDK 1.5 is on PyPI, installing app v2.28.0
from source resolves **SDK 1.5** quite legitimately. `SDK_VERSION` then
reports 1.5, a plugin declaring `requires_sdk=">=1.5,<2"` passes the
handshake — and `ctx.add_overlay_region` does not exist on that host, because
the *method* shipped in a later app release than the SDK package the resolver
picked. The plugin fails during `activate()` and lands in Settings → Plugins
as an error, instead of being refused cleanly as incompatible.

**So when you adopt a capability whose implementation lives in the host —
anything reached through `ctx`, and anything re-exported from
`nparseplus_sdk.ui` / `.events` / `.timers` / `.skin` / `.eqfiles` — declare
`min_app_version` naming the app release that first shipped it, alongside
`requires_sdk`.** It is the one input to the handshake that comes from the
host itself rather than from the resolver, which is exactly why it is the
lever here. The table above names the release for each minor.

Two things are exempt. A purely SDK-side addition needs no pin — a new spec
field or dataclass is *in* the package the resolver installed. Nor does a
capability that **degrades gracefully**: `PluginMeta.update_url` (SDK 1.1) is
read by the app, but a host that ignores it just never offers an update, which
is a worse experience rather than a broken plugin. Pin when the alternative is
an exception or a silent no-op.

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
