# Plugin system design brief (1.15)

Internal notes for the v1 plugin/addon system. User-facing docs live in
`docs/plugins/`; this records the decisions and their reasons.

## Decisions & trade-offs

1. **SDK as a separate package (`sdk/` uv workspace member,
   `nparseplus-sdk` on PyPI eventually).** The user wanted the API package
   alongside, not within, the core codebase, with independent versioning.
   The SDK has NO install-time dependency on `nparseplus`; its `events` /
   `timers` / `ui` modules re-export host classes lazily via module
   `__getattr__` — always resolvable in-process, and standalone dev
   installs the app from git for full type checking. Slated for extraction
   to `prokopto-dev/nparseplus-sdk` via `git subtree split -P sdk` (needs
   repo access this session didn't have).
2. **Version handshake, not import-and-pray.** `PluginMeta.requires_sdk`
   (PEP 440 specifier) vs the bundled `SDK_VERSION`, plus optional
   `min_app_version` vs `__version__`; `check_compat` returns a readable
   refusal reason and malformed strings are a reason, never a crash.
3. **Capability-scoped `PluginContext`, not raw `Backend`.** ~12 members
   are the stable surface; internals can churn behind it. The host
   implementation (`core/plugins/context.py`) wraps every plugin callback
   in a per-plugin try/except so bus dispatch, the tick loop, and the
   parser chain survive broken plugins.
4. **Plugins dir is the primary distribution channel** because every
   end-user build is a frozen PyInstaller bundle with no site-packages on
   `sys.path` — entry points (`nparseplus.plugins` group) work for source
   installs only and return `[]` under frozen builds naturally. Imports go
   through `spec_from_file_location` under the `nparseplus_user_plugins.*`
   namespace — never `sys.path.insert`, so a stray `httpx.py` in the
   plugins folder can't shadow app dependencies.
5. **Single-phase activation, pre-start, GUI thread.** `activate(ctx)` runs
   in `create_app` after Qt is up and before `backend.start()`; the driver
   thread doesn't exist yet, so registrations are race-free. Windows and
   settings pages are declared as specs and materialized by app.py.
6. **Networking never hands plugins `None`.** With sharing off the host
   lazily builds one shared NetWorker + PigParse client
   (`_OwnedNet`), delivering apply-closures through
   `sharing.enqueue_inbound` — the coordinator drains its inbox on every
   tick regardless of sharing mode (verified; no coordinator change).
7. **Per-plugin storage = separate JSON files**
   (`config_dir()/plugin-data/<id>/storage.json`, atomic tmp+rename).
   `Settings` drops unknown keys, so only the small consent/enable map
   (`Settings.plugins.entries`) lives in settings.json.
8. **Consent gates activation, not import.** Reading `meta` requires
   importing the module, so top-level plugin code runs at discovery for
   plugins the user placed in the folder themselves. Documented honestly in
   docs/plugins/security.md. v2 improvement: a declarative manifest
   (plugin.toml) read before any import.
9. **Settings window**: `extra_pages` registry (duck-typed to
   `PluginSettingsPageSpec`) with per-page guarded build/apply. The
   built-in dual-write `apply()` body stays monolithic on purpose — its
   legacy-bridge ordering is load-bearing.
10. **Installer** (Settings > Plugins): zip-slip-safe extraction
    (absolute/`..`/symlink members rejected, member+size caps), single
    top-level entry rule, staging + `validate_plugin` gate before the move
    into place, https-only URL installs with the download on a worker
    thread, uninstall-to-`trash/`. No hot-load in v1 — installs/toggles
    apply on restart (`PluginHost.reload_plugin` is future work).

## Ecosystem increment (same branch, post-v1)

11. **Registry = curated static index** (`core/plugins/registry.py`):
    pydantic schema (schema_version gate, https-only URLs, validated
    sha256), injectable fetch, `release_compat` reusing the SDK handshake,
    `update_available` via packaging.version. Default URL points at the
    planned `prokopto-dev/nparseplus-plugins` GitHub Pages; overridable via
    `plugins.registry_url`; the Browse dialog degrades to "registry
    unavailable" until the repo exists. Spec: docs/plugins/registry.md.
12. **sha256 pinning is the trust boundary**: `expected_sha256` on all
    install paths, refused before extraction/import; InstallResult and
    PluginEntry carry sha256 + source_url provenance
    (`PluginHost.record_install`, consent semantics untouched).
13. **Browse UI** (`RegistryBrowserDialog` in ui/pluginmanager.py): worker
    -thread fetch, compat-filtered rows, pinned-hash installs through the
    page's existing worker; passive "update available (vX)" decoration on
    the main table once an index was fetched this session (no startup
    auto-fetch yet).
14. **Repo template** (`templates/plugin-repo/`): complete content of the
    future `nparseplus-plugin-template` repo (TEMPLATE_SETUP.md documents
    the split); release workflow enforces tag == meta.version and emits
    the registry entry JSON + sha256; guard tests keep the in-repo copy
    green until the split.
15. **Versioning policy documented** (docs/plugins/versioning.md): SDK 1.x
    additive-only; three-coordinate model (app / SDK / plugin).

## Follow-ups (open as issues)

- Extract `sdk/` to its own repo + PyPI trusted publishing for both
  packages; switch the app dependency from workspace to the PyPI range.
- Declarative plugin manifest to close the import-before-consent caveat.
- Hot enable/disable/reload without restart.
- Curated/signed plugin index the installer can browse.
- Frozen-PYZ stdlib audit (which stdlib modules plugin authors can rely on
  in the bundle) + mkdocstrings-generated API reference.
