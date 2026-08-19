# Plugin system design brief (1.18)

Internal notes for the v1 plugin/addon system. User-facing docs live in
`docs/plugins/`; this records the decisions and their reasons.

## Decisions & trade-offs

1. **SDK as a separate package (`sdk/` uv workspace member, published to
   PyPI as `nparseplus-sdk`).** The user wanted the API package
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
   plugins the user placed in the folder themselves — and `validate_plugin`
   goes further, calling `activate()` against a fake context at install
   time. Documented honestly in docs/plugins/security.md. v2 improvement: a
   declarative manifest (plugin.toml) read before any import.
9. **Settings window**: `extra_pages` registry (duck-typed to
   `PluginSettingsPageSpec`) with per-page guarded build/apply. The
   built-in dual-write `apply()` body stays monolithic on purpose — its
   legacy-bridge ordering is load-bearing.
10. **Installer** (Settings > Plugins): zip-slip-safe extraction
    (absolute/`..`/symlink members rejected, member+size caps), single
    top-level entry rule, staging + `validate_plugin` gate before the move
    into place, https-only URL installs with the download on a worker
    thread, uninstall-to-`trash/`. No hot-load in v1 — installs/toggles
    apply on restart (`PluginHost.reload_plugin` is future work; tracked as
    issue #45).

## Ecosystem increment (same branch, post-v1)

11. **Registry = curated static index** (`core/plugins/registry.py`):
    pydantic schema (schema_version gate, https-only URLs, validated
    sha256), injectable fetch, `release_compat` reusing the SDK handshake,
    `update_available` via packaging.version. Live since 1.18; the default
    URL pointed at `prokopto-dev/nparseplus-plugins` GitHub Pages until the
    move to the registry server (item 29). A failing registry degrades to a
    "could not reach" line without hiding the ones that answered. Spec:
    docs/plugins/registry.md. (The single-URL override this shipped with,
    `plugins.registry_url`, was superseded by the registry list — items
    24–28.)
12. **sha256 pinning is the trust boundary**: `expected_sha256` on all
    install paths, refused before extraction/import; InstallResult and
    PluginEntry carry sha256 + source_url provenance
    (`PluginHost.record_install`, consent semantics untouched). NOTE the
    asymmetry: only registry installs *have* an expected hash to pass, so
    `Install from URL…` is https + size cap and nothing more. Said plainly
    in index.md and security.md rather than papered over.
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
    additive-only; three-coordinate model (app / SDK / plugin); explicit
    deprecation ladder (document → warn one minor later → remove only in
    the next major, never sooner than six months).

## Hardening increment (1.18)

16. **Master toggle, default OFF** (`Settings.plugins.enabled`, read only
    through `config.settings.plugins_enabled()`). Honouring "off" means
    more than hiding a page: when plugins are off none of the machinery may
    be *imported* (SDK, installer, registry client, httpx, zipfile, the
    manager table). `pluginbootstrap.py` exists to give `create_app`
    exactly two gated import sites, and
    `tests/core/plugins/test_master_toggle.py` checks that structurally.
    `NPARSEPLUS_NO_PLUGINS=1` stays a **veto**, never an enabler — it is
    the safe-mode recovery switch, so it must not be able to turn plugins
    on for someone who never opted in. Toggling requires a restart
    (`_notify_plugins_restart` in ui/settingswindow.py); the tray's
    "Open Plugins Folder" keys off `plugin_host is not None`, not the
    setting, so a failed discovery sweep doesn't leave a misleading entry.
17. **Uninstall forgets** (`PluginHost.forget` + `install.trash_plugin_data`).
    Uninstall previously moved only the code to `trash/`, leaving the
    consent record in settings.json and `plugin-data/<id>/` on disk — a
    consent bypass, since the next thing to claim that id would load
    pre-approved and inherit the predecessor's storage. Both now go with
    the code, into `trash/` and `trash/plugin-data/` respectively (moved,
    not deleted: an uninstall-by-mistake stays recoverable). Also why
    `set_enabled` creates an *unapproved* entry when none exists — ticking
    a checkbox must not stand in for consent.
18. **Download hardening** (`install.fetch_https_bytes`). `follow_redirects
    =True` let an https URL 302 to http and be downloaded in plaintext;
    redirects are now followed by hand with the https assertion re-applied
    on every hop (cap `MAX_REDIRECTS`). Bodies stream against
    `max_bytes` and abort mid-download rather than being buffered whole
    before anyone checks a length. Extraction likewise moved off
    `extractall` to `_extract_limited`, which counts bytes as they are
    *written* — declared member sizes are attacker-controlled, so the
    pre-check is only a cheap early reject.
19. **Supervised plugin ticks** (`core/driver.py`). One thread runs log
    tailing, the parser chain, every countdown, the fight tracker and the
    sharing inbox, so a slow plugin tick stalls all of it.
    `add_supervised_tick` times plugin callbacks against `TICK_BUDGET_S`
    (250 ms) and drops one after `TICK_BREACH_LIMIT` (2) **consecutive**
    breaches — consecutive because a GC pause or a cold import legitimately
    stretches one call. App-owned ticks append to `on_tick` directly and
    are never timed or dropped (dropping `TimersService.tick` would break
    more than any stall). The plugin stays active; `HostPluginContext.
    tick_dropped` carries the reason as a plain string for the manager page
    to render ("tick disabled (too slow)"). The timing path only engages
    when the supervision dict is non-empty, i.e. never for users with no
    plugins.
20. **A version bump does NOT re-prompt** (`host._load_one`): consent keys
    on `meta.id`, and a changed `meta.version` just updates `last_version`.
    Deliberate (a dialog on every patch trains click-through) but
    security-relevant, so it is stated outright in security.md rather than
    left for someone to discover.
21. **SDK version is one literal** (`sdk/src/nparseplus_sdk/__init__.py`
    `__version__`, with `sdk/pyproject.toml` dynamic-versioned via
    hatchling). The `importlib.metadata` lookup is *gone*, not fallback-ed:
    a frozen build has no dist metadata, so the lookup raised and the
    fallback beside it became the version every shipped build reported to
    `check_compat` — silently refusing plugins with a newer `requires_sdk`,
    invisible in CI, reproducible only from a release artifact.
22. **SDK release pipeline** (`.github/workflows/release-sdk.yml`): `sdk-v*`
    tags, tag-vs-`__version__` check, `uv build --package nparseplus-sdk`,
    clean-venv smoke test (imports it, checks the version, runs the CLI,
    asserts the wheel does not pull in `nparseplus`), then PyPI trusted
    publishing behind the `pypi` environment's required reviewer. The
    one-time human setup (PyPI pending publisher + GitHub environment) is
    documented in sdk/README.md; `sdk-v1.0.0` is published, so the template
    and the docs depend on `nparseplus-sdk>=1.0,<2` from PyPI.
23. **Registry repo scaffolding** (`templates/registry-repo/`): seed
    `index.json`, `owners.json` (plugin-id ownership record — makes
    "first come" machine-checkable instead of review-only), the generated
    `schema/index-v1.schema.json` (from `tools/gen_registry_schema.py`, so
    the schema and the app's pydantic parser cannot drift), CONTRIBUTING,
    README, SETUP, and `validate-index.yml`. **Deliberate divergence from
    the original spec: registry CI does NOT run `nparseplus-plugin
    validate`.** Validation imports and activates the plugin, i.e. executes
    unreviewed PR code on the registry's infrastructure on every
    submission. CI downloads artifacts, hashes them, and discards them —
    never extracts, never executes — and runs on `pull_request`, so fork
    PRs get a read-only token and no secrets. docs/plugins/registry.md says
    so explicitly.

## Multiple registries increment (post-1.18)

24. **The built-in registry is synthesized, never persisted.**
    `plugins.registries` holds *user* entries only; `resolve_registries`
    prepends a `ResolvedRegistry` built from the `DEFAULT_REGISTRY_URL`
    constant and only its checkbox persists
    (`plugins.default_registry_enabled`). The anti-stranding argument is
    the whole reason: had the default been written into settings.json the
    first time anyone opened the page, changing where the catalogue lives
    would strand every existing user on the old URL while new installs got
    the new one — a support problem with no in-app fix. Corollaries: the
    default row can be unticked but not removed (two independent guards —
    the disabled button in `RegistryListWidget._sync_buttons` and a refusal
    in `PluginHost.remove_registry` — because a keyboard path must not
    delete the only way back), and a user entry whose URL equals the
    default collapses into that row instead of duplicating it, so promoting
    a community registry to default is a no-op for the people who already
    added it. Legacy `plugins.registry_url` is folded into the list by a
    `model_validator` on `PluginsSettings` and cleared; that validator also
    normalizes (lower-case scheme+host, path untouched), dedupes, and
    **drops** unusable rows rather than raising — `load_settings` treats a
    ValueError as a corrupt document and falls back to defaults, so raising
    would trade every setting the user has for one bad registry line.
    `normalize_registry_url` lives in `config/settings.py`, not the registry
    client, so constructing a `Settings` never imports the plugin subsystem
    (master toggle, item 16).
25. **Provenance is a wrapper, not a wire field.** `MergedListing(registry,
    plugin)` carries which registry served a listing, instead of stamping a
    `registry_url` onto `RegistryPlugin`. Two reasons. A registry document
    *cannot truthfully* self-identify — a mirror copies the claim verbatim
    and the app would render a lie; the URL we actually fetched from is
    knowledge only the client has. And the wire models generate the registry
    repo's committed JSON Schema (`tools/gen_registry_schema.py`, item 23),
    so a field added for display would become part of the published format
    and something submitters could set.
26. **Concurrent fetch with per-registry results.** `fetch_indexes` returns
    a `MultiFetchResult` of one `RegistryFetchResult` per registry (index
    XOR error), placed by index rather than completion order so the merged
    table is deterministic. Concurrency is a correctness concern more than a
    speed one: serially, a dead registry ahead of a live one costs its full
    timeout before the working one is tried. It deliberately does **not**
    filter on `enabled` — the caller passes the enabled ones — so
    `summary_lines()` never has to explain a row it silently skipped. That
    method is pure, keeping `RegistryBrowserDialog` a thin renderer; partial
    failure annotates and still shows the rest.
27. **`best_update`'s trust rule.** Candidates are compat-filtered first —
    it previously offered any higher version, including ones `check_compat`
    would then refuse to load, which is a behaviour fix, not just a
    refactor. Among the compatible ones the registry a plugin was
    *installed from* (`PluginEntry.registry_url`) wins if it offers
    anything, even against a higher version elsewhere: silently promoting a
    different publisher's build of the same id is a trust hop the user never
    agreed to. When the offer does come from elsewhere the manager says so
    by name ("update available (vX from <registry>)"), and the Browse row
    for an id installed from another registry is a disabled "Installed
    (other source)" rather than an Install button — uninstall-then-install
    is the only path across publishers, on purpose.
28. **Warning placement.** `REGISTRY_WARNING` lives in
    `ui/pluginregistries.py` (kept out of `pluginmanager` so the add flow
    and its text are importable/monkeypatchable without the manager page)
    and appears in three places: the list's footnote label (plus the
    non-default rows' name tooltips), the tail of every Browse Source-cell
    tooltip, and the add confirmation — where it is stacked with
    `CONSENT_WARNING` and defaults to Cancel. `add_registry` persists, so
    it is the last call in the flow, never the first.

## Registry-server increment (#130)

29. **The built-in registry moved to a live server.** `DEFAULT_REGISTRY_URL`
    now names `https://nparseplugins.prokopto.dev/index.json`
    (`prokopto-dev/nparse-plugin-regserve`, Go + SQLite) instead of the
    curated repo's GitHub Pages. **No client change came with it**, and that
    is the point: same schema-1 document, same single unconditional GET, and
    the server keeps the index endpoints outside `/api/v1` so the path a
    released binary is compiled with never moves. Caching, `ETag` and a
    `User-Agent` were deliberately left out — the client sends the same
    request it always did, and changing that is its own decision.
30. **The move's real work was provenance, not the constant.** Item 24's
    anti-stranding design covers the *registry list*: nothing is persisted,
    so the new URL simply appears. But `PluginEntry.registry_url` is a
    record written once at install time, so every plugin installed before
    the move pointed at a URL that is no longer configured — Source falls
    back to a bare host, Browse offers "Installed (other source)", and
    `best_update`'s trust rule (item 27) turns the built-in registry's own
    offer into a cross-source confirmation between two names for one
    catalogue. `PluginsSettings._follow_the_moved_default`
    rewrites those records inside the validator that is documented to never
    raise, under two conditions that each answer a different objection
    raised in review. **The old URL must be in no row of the user's own
    `registries`** — a listed registry makes the record true as written, so
    row and records are both left alone, which is what lets someone add the
    old index deliberately now that it is an ordinary third-party URL. Where
    no row holds it, nothing else the record could mean is left:
    `add_registry` refused a URL equal to `DEFAULT_REGISTRY_URL` and
    `resolve_registries` collapsed a stored copy into the built-in row, so
    an install cannot have come through a copy. **And it runs at most once**,
    gated by `plugins.registry_move_applied`: otherwise the same rule fires
    again when a user *removes* a registry they had deliberately added,
    silently re-attributing its plugins — which is precisely what
    `PluginEntry.registry_url` promises not to do. Known limit, in the
    docstring: v2.16.0 moved the constant without writing a marker, so a
    document from that one release cannot be told apart from a pre-move one.
    The list is edited in exactly one case — a row the same validation
    manufactured from the deprecated `plugins.registry_url` override, an
    artifact of the fold and never a list entry.
31. **The URL literal lives in `config/settings.py`.** `BUILTIN_REGISTRY_URL`
    is defined beside `normalize_registry_url` and re-exported as
    `core.plugins.registry.DEFAULT_REGISTRY_URL`, for the reason that
    function is there at all: settings must be able to migrate a stored URL
    without importing the plugin subsystem (item 16's gate is what keeps a
    plugins-off launch from touching the SDK). One literal, because the two
    halves of a move disagreeing is exactly the bug. The URL-pin test was
    refocused, not deleted: it now asserts the shape (`https://…/index.json`)
    and that `templates/registry-repo/SETUP.md` and `docs/plugins/registry.md`
    both name whatever the constant says.

## Follow-ups (open as issues)

- Extract `sdk/` to its own repo (`git subtree split -P sdk`).
- Switch the app's own `nparseplus-sdk` dependency off
  `[tool.uv.sources] workspace = true` to resolve the PyPI range instead —
  only worth doing after the repo split, since the workspace source is what
  makes an SDK edit immediately visible to the app's tests.
- Declarative plugin manifest to close the import-before-consent caveat
  (and to let the installer skip the `activate()` call in `validate_plugin`).
- Hot enable/disable/reload without restart (#45).
- Frozen-PYZ stdlib audit (which stdlib modules plugin authors can rely on
  in the bundle) + mkdocstrings-generated API reference. Until it exists,
  docs/plugins/developing.md hedges the frozen-dependency list rather than
  promising one.
