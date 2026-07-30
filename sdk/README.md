# nparseplus-sdk

The stable contract for building [nParse+](https://github.com/prokopto-dev/nparse-plus)
plugins: addon windows, log parsers, event subscribers, and network pollers
that load into the app at runtime.

- **Plugin authors:** install the SDK (see below — **not on PyPI yet**),
  subclass `nparseplus_sdk.NParsePlugin`, expose a module-level
  `create_plugin()` factory, and check your work with
  `nparseplus-plugin validate <path>`. Note that add-ons are opt-in in the
  app: users must tick *Settings > Advanced > Enable plugins (add-ons)* and
  restart before anything loads. Full guide: the *Plugins* section of the
  [nParse+ documentation](https://prokopto-dev.github.io/nparse-plus/).
- **Versioning:** this package is versioned independently of the app.
  Declare the SDK range you built against in
  `PluginMeta.requires_sdk` (e.g. `">=1.0,<2"`); the app refuses plugins
  whose range does not admit the SDK version it bundles.
- **No dependency on the app:** plugins execute inside nParse+, which
  provides the runtime. `nparseplus_sdk.events` and `nparseplus_sdk.ui`
  re-export host classes lazily; for standalone type-checking/tests install
  the app from source
  (`pip install git+https://github.com/prokopto-dev/nparse-plus`).

## Installing (not on PyPI yet)

The release workflow below exists but has not published a version, so
**`pip install nparseplus-sdk` fails today**. Install from the app repo's
`sdk/` subdirectory instead:

```bash
pip install "git+https://github.com/prokopto-dev/nparse-plus@master#subdirectory=sdk"
pip install ./sdk        # ...or from a nparse-plus checkout
```

In a plugin's `pyproject.toml`:

```toml
dependencies = [
  "nparseplus-sdk @ git+https://github.com/prokopto-dev/nparse-plus@master#subdirectory=sdk",
]
```

Swap either form for a plain `nparseplus-sdk>=1.0,<2` once the package is
live. Nothing else changes — the import path, the CLI and the contract are
identical.

## Repository note

This package currently lives in the `sdk/` directory of the main
`prokopto-dev/nparse-plus` repository as an independent uv workspace member,
versioned and released independently of the app. It may eventually move to
its own repository (`prokopto-dev/nparseplus-sdk`).

## Releasing (maintainers)

The version has exactly one source: `__version__` in
`src/nparseplus_sdk/__init__.py`. `pyproject.toml` declares
`dynamic = ["version"]` and hatchling reads that literal, and `uv.lock`
records no version for a dynamic-version package — so the wheel, the lock and
the runtime constant cannot disagree. Do **not** reintroduce a literal
`version =` in `pyproject.toml`, and do not derive `SDK_VERSION` from
`importlib.metadata`: a PyInstaller-frozen app has no dist metadata, so any
fallback next to that lookup is what every shipped build would report to
`check_compat`.

The SDK is **not** covered by the app's semantic-release automation (that
owns `v*` tags only). To cut a release:

1. Bump `__version__` in `src/nparseplus_sdk/__init__.py`.
2. Commit, then tag `sdk-v<X.Y.Z>` (matching the literal exactly) and push
   the tag: `git tag sdk-v1.1.0 && git push origin sdk-v1.1.0`.
3. `.github/workflows/release-sdk.yml` verifies the tag against
   `__version__`, runs `uv build --package nparseplus-sdk`, smoke-tests the
   built wheel in a clean venv (imports it, checks the reported version, runs
   `nparseplus-plugin --help`, and asserts the wheel does not pull in
   `nparseplus`), then publishes to PyPI. It also accepts a
   `workflow_dispatch` with an existing tag.

Bump the app's `nparseplus-sdk` range in the root `pyproject.toml` only after
the matching version is live on PyPI.

### One-time setup (human, not automatable)

Publishing uses **PyPI Trusted Publishing** (OIDC) — there is no API token and
no repository secret. Both of these must exist before the first `sdk-v*` tag,
or the publish step fails with an OIDC error:

1. **PyPI pending publisher.** The project does not exist on PyPI yet, so
   create a *pending* publisher at
   <https://pypi.org/manage/account/publishing/> with exactly:
   - PyPI Project Name: `nparseplus-sdk`
   - Owner: `prokopto-dev`
   - Repository name: `nparse-plus`
   - Workflow name: `release-sdk.yml`
   - Environment name: `pypi`

   (After the first successful upload this becomes a normal publisher on the
   project's Settings → Publishing page; nothing needs changing.)

2. **GitHub `pypi` environment.** In the repository's
   Settings → Environments, create an environment named `pypi` and add a
   required reviewer. The workflow declares `environment: pypi`, so every
   publish then pauses for a human approval — the last gate between a pushed
   tag and an immutable PyPI upload.
