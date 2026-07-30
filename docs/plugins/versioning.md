# Versioning

Three version numbers cooperate in the plugin ecosystem, and each has
exactly one job.

| Coordinate | Owner | Job |
| --- | --- | --- |
| **App version** (`nparseplus`, e.g. `1.15.0`) | semantic-release on the app repo | The product. Each release bundles exactly **one** SDK version. |
| **SDK version** (`nparseplus-sdk`, e.g. `1.0.0`) | the SDK package (own semver; moving to its own repo) | **The contract.** The only number plugins and the app negotiate over. |
| **Plugin version** (`PluginMeta.version`) | the plugin author | The plugin's own releases; drives registry update detection. |

## The compatibility handshake

A plugin declares what it needs; the app enforces it at load time:

```python
meta = PluginMeta(
    id="my-plugin",
    name="My Plugin",
    version="1.4.0",
    requires_sdk=">=1.0,<2",     # PEP 440 range vs the bundled SDK version
    min_app_version="1.15.0",    # optional app floor (feature-based, e.g. an
)                                #   event class added in a given app release)
```

If `requires_sdk` doesn't admit the bundled SDK, or the app is older than
`min_app_version`, the plugin is refused with a readable reason (shown in
Settings > Plugins) — never a crash, never a half-load. The registry Browse
list runs the *same* check on index metadata, so incompatible plugins are
filtered before anything is downloaded.

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

## Author release checklist

1. Bump `PluginMeta.version` (semver: breaking behavior for your users =
   major, features = minor, fixes = patch).
2. Keep `requires_sdk` honest — if you adopted a newer SDK capability,
   raise the floor.
3. Tag `v<version>` — the template's release workflow refuses a tag that
   doesn't match `meta.version`, then builds the zip, computes its sha256,
   and emits the registry entry JSON.
4. Update your registry listing (PR to the index) so users get the
   "update available" notice.

## Where the SDK lives

The SDK is versioned and packaged independently of the app (currently in
the app repo's `sdk/` directory as a workspace member; planned to move to
its own repository with PyPI publishing). Nothing about the handshake
changes when it moves — plugins already target the package, not the repo.
