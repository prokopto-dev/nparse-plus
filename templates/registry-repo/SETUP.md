# Standing up the registry (maintainer notes)

The catalogue nParse+ fetches out of the box is served by the live registry
server, [`prokopto-dev/nparse-plugin-regserve`](https://github.com/prokopto-dev/nparse-plugin-regserve)
— one Go binary and a SQLite database, deployed on a droplet behind Traefik.
The URL the app is compiled with is:

```
https://nparseplugins.prokopto.dev/index.json
```

`DEFAULT_REGISTRY_URL` (`src/nparseplus/core/plugins/registry.py`, whose
literal lives in `config/settings.py` — the settings layer owns the URL
plumbing and must not import the plugin subsystem) has to equal that exactly.
`tests/core/plugins/test_registry_schema.py` asserts that this file and
`docs/plugins/registry.md` both name whatever the constant currently says, so
neither can drift from it quietly.

## What this directory is

Two things, and only the first is load-bearing today:

1. **The generated JSON Schema and its drift guard.**
   `schema/index-v1.schema.json` is produced by
   `tools/gen_registry_schema.py` from the pydantic models a released client
   actually parses the index with, and `--check` (run by the test above)
   fails if the committed copy is stale. The registry server vendors this
   file verbatim — its `SCHEMA001` gate diffs what it renders against it — so
   the server and the client cannot disagree about what a valid entry is.
2. **The mirror of `prokopto-dev/nparseplus-plugins`**, the curated repo that
   *was* the registry: `index.json`, `owners.json`, the submission
   instructions and the validate-index workflow. Server-side publishing is
   specified and not yet built, so that repository is still where a listing
   is proposed and reviewed; a maintainer then applies the merged entry to
   the live catalogue.

## The URL contract

| URL part | Requirement |
| --- | --- |
| `https://` | The client refuses anything else, and re-asserts it on every redirect hop. A plain-http hop is a failed fetch, not a downgrade. |
| `nparseplugins.prokopto.dev` | The deployment's single hostname — the same name for SSH and for HTTPS (see the server's `docs/operations/deployment.md`). Traefik terminates TLS; the service publishes no ports of its own. |
| `/index.json` | A schema-1 document. The index endpoints sit **outside** `/api/v1` deliberately, so that versioning the product API never moves the path a released desktop client is compiled with. |

### Verify what the app actually fetches

Do not skip this — it is the only step that proves the app can reach it:

```bash
curl -fsS https://nparseplugins.prokopto.dev/index.json | python -m json.tool | head
# The shape PluginMeta.update_url expects, for one plugin:
curl -fsS https://nparseplugins.prokopto.dev/plugins/merchant-mode/index.json | python -m json.tool | head
# Liveness, and readiness — which says *why* when it is not ready:
curl -fsS https://nparseplugins.prokopto.dev/healthz
curl -fsS https://nparseplugins.prokopto.dev/readyz
```

`schema_version` must read `1`. A newer one makes every released client
refuse the index and tell the user to update nParse+, which is a breaking
change for the whole installed base rather than a regeneration.

## Moving it again

The built-in registry is synthesized from the constant on every read and
**never written to `settings.json`**, so changing where the catalogue lives
moves every existing user instead of stranding them on the URL whichever
release first wrote it. That is the mechanism; the checklist below is what
goes with it (see [#130](https://github.com/prokopto-dev/nparse-plus/issues/130),
which did exactly this):

1. Point `BUILTIN_REGISTRY_URL` (`config/settings.py`) at the new index.
2. Keep the outgoing URL as `_LEGACY_DEFAULT_REGISTRY_URL` beside it and
   re-point `PluginEntry.registry_url` in `PluginsSettings`. Without that,
   everything already installed reads as "from a registry that is no longer
   configured" and every update offer becomes a cross-source confirmation
   between two names for the same catalogue. Rewrite only an exact normalized
   match, and **do not edit the user's registry list** while you are there: a
   stored row holding the outgoing URL was inert (it collapsed into the
   built-in row) but after the move the same URL is an index somebody may add
   on purpose, and no settings file distinguishes the two. Leave it and let
   *Remove* work on it. The one row worth dropping is one the same validation
   just manufactured from the deprecated `plugins.registry_url` override.
3. Introduce the one-shot marker **in the same release as the move** — #130
   did not, so the release that moved the catalogue writes documents
   indistinguishable from pre-move ones, and the code needs a second test
   (a provenance record naming the new URL, which only post-move code can
   write) to recognise them.
4. Regenerate the schema (its `$id` names the host) and copy the result to
   every consumer — see below.
5. Update this file and `docs/plugins/registry.md`; the test named above
   fails until both name the new URL.

## Keeping the schema in sync

`schema/index-v1.schema.json` is generated, in the **app** repo, from the
pydantic models that the client actually parses with:

```bash
# in an nparse-plus checkout
uv run python tools/gen_registry_schema.py
uv run python tools/gen_registry_schema.py --check   # what CI asserts
```

When `nparseplus.core.plugins.registry` changes, regenerate here and copy the
result into the registry server (`internal/registry/testdata/`) and into the
curated repo. Never hand-edit it anywhere: the whole point is that a
generated file, and not three hand-maintained ones, decides what a valid
entry is.

A `schema_version` bump is a breaking change for every already-released
nParse+ (older clients refuse the index and tell the user to update), so it
needs a deprecation plan, not just a regeneration.

## History: the GitHub Pages arrangement

Until [#130](https://github.com/prokopto-dev/nparse-plus/issues/130) the
registry *was* `prokopto-dev/nparseplus-plugins`: `index.json` at the
repository root, served by classic branch-source Pages (branch `main`, folder
`/`) at `https://prokopto-dev.github.io/nparseplus-plugins/index.json`, with
`main` protected, review required, and the `validate` check as a merge gate.
Nothing about that stopped working — it is still the repository submissions
go through — but the app no longer fetches it, and the catalogue the server
holds is the one users see.

Two things about it are still worth knowing if it is ever rebuilt or
re-pointed:

- **The registry repo name has no hyphen in "nparseplus."** The app repo is
  `nparse-plus`; the registry repo is `nparseplus-plugins`.
- **Pages runs Jekyll by default**, which is fine here: it renders
  `README.md` as the landing page and copies `index.json` and `schema/`
  through verbatim (Jekyll does not process files without front matter). If
  that ever changes, an empty `.nojekyll` at the root fixes the JSON at the
  cost of the rendered landing page.

## Afterwards

- Point `CONTRIBUTING.md`'s template link at the real
  `prokopto-dev/nparseplus-plugin-template` repo once that exists (see
  `templates/plugin-repo/TEMPLATE_SETUP.md`). Still pending — that repo has
  not been created.
- ~~Delete `templates/registry-repo/` from the app repo~~ — deliberately
  **not** done. The schema in here is generated from the app's models and
  guarded by `tools/gen_registry_schema.py --check` +
  `tests/core/plugins/test_registry_schema.py`; deleting it would remove the
  drift guard for both the server and the curated repo.
