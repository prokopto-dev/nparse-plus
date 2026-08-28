# Release flow

Releases are driven by [Conventional
Commits](https://www.conventionalcommits.org/) and
[Python Semantic Release](https://python-semantic-release.readthedocs.io/):

- `fix:` / `perf:` → patch release
- `feat:` → minor release
- `feat!:` / `BREAKING CHANGE:` → major release
- `chore:` / `ci:` / `docs:` etc. → no release

Every PR's commits are checked against these types by `pr-commit-check.yml`
(see [`tools/check_conventional_commits.py`](https://github.com/prokopto-dev/nparse-plus/blob/master/tools/check_conventional_commits.py)),
so a non-conventional commit can't reach `master`. Merge PRs with a **merge
commit** (not squash) so the individual conventional commits are preserved for
versioning.

## Two channels: master cuts betas, stable is promoted

**A merge to `master` does not ship to everyone.** It cuts a *prerelease* —
`v2.30.0-beta.1`, `v2.30.0-beta.2`, … — and only clients that opted into the
beta channel are offered it. A stable release happens when somebody
deliberately promotes the line.

```
merge to master
   ↓
v2.30.0-beta.1   (prerelease — beta channel only)
v2.30.0-beta.2
   ↓  promoted, deliberately
v2.30.0          (stable — everyone)
```

This needed no migration. `updater.check_for_update` has always skipped
prereleases, in every binary ever published, so master could start cutting
betas without any client noticing: nobody is offered anything new until a
stable is promoted. See
[`UpdateChannel`](https://github.com/prokopto-dev/nparse-plus/blob/master/src/nparseplus/updater.py).

### Promoting a beta to stable

```bash
gh workflow run semantic-release.yml -f promote=true
```

or use **Run workflow → Promote** on the Semantic Release workflow in the
Actions tab. That *finalizes* the current line — `2.30.0-beta.3` becomes
`2.30.0`, not `2.31.0` — publishes the plain tag, and everyone is offered it,
including users already on the beta.

Two implementation notes worth knowing before you change any of it:

- semantic-release decides prerelease-ness from whichever
  `[tool.semantic_release.branches.*]` table matches the **current branch
  name**, and has no CLI flag that forces a stable release on a branch
  configured for prereleases (`--as-prerelease` exists; `--no-prerelease` does
  not). So the promotion job checks master's commit out under the branch name
  `promote-to-stable`, which `pyproject.toml` configures with
  `prerelease = false`. That branch is never pushed and never exists on the
  remote.
- The commit and tag it produces are pushed **back to `master`**. That is not
  incidental: a long-lived `stable` branch would leave master's `__version__`
  behind at `2.30.0-beta.3`, so the next merge would cut `2.30.0-beta.4` — a
  "new" beta that `packaging.Version` orders *below* the `2.30.0` that already
  shipped, stranding the beta line.

### Betas are not Flatpak

Betas publish **DMG, zip, tarball and `.deb` only**. The flatpak job and the
`gh-pages` OSTree publish are skipped for a prerelease, because that
repository is what `flatpak update` follows for *stable* users — publishing a
beta there would push it to people who opted into nothing, through a
force-push that cannot be undone by re-running an older release. The versioned
docs deploy is skipped too (it would create a permanent `2.30.0-beta` version
directory and alias it to `latest`).

The client half is enforced too, not just documented: `updater.effective_channel`
clamps a beta preference to stable whenever the app is running in a Flatpak, at
every read rather than when the setting is written — settings outlive the
install that wrote them, so a preference carried in from a tarball install must
not take effect (and must not be erased either, so it still works if the user
goes back). Both consumers, the settings window and the tray check, route
through it.

None of that is left to review:
`tests/test_release_workflow.py::test_nothing_that_reaches_a_stable_user_runs_for_a_prerelease`
derives the list of dangerous steps from the workflow file itself, so a new
flatpak or `gh-pages` step added without the guard fails the test suite. The
publish step also refuses a prerelease tag at runtime, independently of the
`if:` that was supposed to have stopped it.

### Version spellings

For a beta, three spellings of one version are in play, and they are supposed
to differ:

| Where | Value |
|---|---|
| Git tag | `v2.30.0-beta.1` |
| `nparseplus.__version__` | `2.30.0-beta.1` |
| Wheel, `dist-info`, `importlib.metadata.version()` | `2.30.0b1` |

semantic-release writes SemVer; hatchling normalizes it to PEP 440 for the
distribution metadata. They compare **equal** under `packaging.Version`, which
is what every consumer in the app uses, so this is correct rather than a bug to
fix. In particular, do not "normalize" the `__version__` literal — the tag
check in `release.yml` compares it to the tag character for character, and
`__version__` is deliberately a literal rather than a metadata lookup (that
lookup is exactly what fails in a frozen build).

!!! note "One consequence for plugin authors"

    A prerelease sorts *below* the version it is a prerelease of, so a plugin
    whose `min_app_version` names the version currently in development is
    refused on every beta of it:

    | Running | `min_app_version = "2.29.0"` |
    |---|---|
    | `2.29.0-beta.1` | refused — `2.29.0b1 < 2.29.0` |
    | `2.29.0` | loads |
    | `2.30.0-beta.1` | loads |

    That is `packaging.Version` behaving correctly, and `check_compat` is
    unchanged. It does mean **`min_app_version` should name a version that has
    already been promoted**, not the one the feature is landing in — otherwise
    the plugin is broken for exactly the beta testers who would have caught
    problems with it.

## The pipeline

1. **Semantic Release workflow** — runs **automatically on every merge to
   `master`** (also available via manual dispatch, or `uv run semantic-release
   version` locally). It runs the ruff+pytest gate, computes the next version
   from the commit log, bumps `nparseplus.__version__`, updates
   `CHANGELOG.md`, commits, tags `v<X.Y.Z>-beta.<N>`, and dispatches the
   package workflow (tags created with `GITHUB_TOKEN` don't trigger workflows
   on their own). A merge with only `chore`/`ci`/`docs` commits runs the gate
   and no-ops — no version bump, no release — but CI still builds it.
   Dispatching it with `promote=true` cuts the stable release instead.
2. **Release workflow** (`release.yml`) verifies the tag matches both
   version files, then builds in parallel:
   - macOS DMG (ad-hoc signed), plus a `.app` zip of the same bundle beside
     it — packed with `ditto`, not `zip`, because the signature seals over
     resource forks and extended attributes that `zip` drops; the job
     extracts the zip and re-verifies the seal to prove it. The DMG is the
     human download; the zip exists for code that has to unpack a bundle
     without mounting a disk image (see [the self-updater](../features/updater.md))
   - Windows zip
   - Linux tarball **and** Flatpak bundle (GPG-signed; smoke-tested
     headless inside the sandbox)
   - Linux Debian package, built in a `debian:12` container so it runs where
     the tarball cannot (see [below](#linux-two-builds-two-glibc-floors))
   - publishes the Flatpak OSTree repo to the `gh-pages` branch —
     preserving the deployed docs — so `flatpak update` works.
     **Stable releases only** — see [above](#betas-are-not-flatpak)
3. The **release job** collects the artifacts, extracts that version's
   changelog section, and publishes the GitHub release.
4. The **docs job** deploys this documentation as version `<X.Y>` with
   the `latest` alias (via [mike](https://github.com/jimporter/mike)),
   from the tagged tree. **Stable releases only.**

Between releases, pushes to `master` that touch `docs/` redeploy the
**dev** docs version automatically (`docs-dev.yml`).

## Linux: two builds, two glibc floors

PyInstaller bundles Python and Qt but **not glibc**, so an artifact's glibc
floor is whatever its build host shipped. `build-linux` runs on
`ubuntu-latest` — Ubuntu 24.04, glibc 2.39 — so its tarball cannot start on
Debian 12 (2.36), which reports `version 'GLIBC_2.3x' not found` at exec.
`build-linux-debian12` builds the same application inside a `debian:12`
container and packages it as a `.deb`, putting the floor at bookworm's.

Three choices in that job are worth knowing:

- **A container, not `runs-on: ubuntu-22.04`.** That image begins deprecation
  on 2026-09-17 and is unsupported by 2027-04-17, so pinning it would buy
  under a year. A container decouples the floor from GitHub's runner image
  permanently.
- **`debian:12`, not a manylinux image.** PySide6's Linux wheels are tagged
  `manylinux_2_34`, so the build host itself needs glibc ≥ 2.34 — which rules
  out `manylinux_2_28` (AlmaLinux 8). Bookworm's 2.36 clears it.
- **The long `apt-get install` list is load-bearing.** PyInstaller bundles
  whatever `ldd` resolves *at build time*, and the GitHub runner image carries
  far more than a bare `debian:12`. A library that is simply absent is not
  bundled and **the build still succeeds** — the artifact just quietly loses
  whatever needed it. This is not hypothetical: the first Debian build (v2.25.0)
  had no `libxkbfile1`, so every QtWebEngine binary came out unresolved and the
  Discord overlay would have shipped broken. `packaging/deb/check_bundle.py` is
  what makes that loud, and the `objdump` step *measures* the resulting glibc
  floor rather than asserting it, so a dependency that raises it fails the
  build instead of a user's launch.

  That check is deliberately **not** "nothing may be unresolved". PySide6 ships
  plugins for Qt modules this app never loads — TIFF images, Wayland, GTK
  theming, PulseAudio multimedia — and one of those failing to load costs
  nothing. `libtiff.so.5` cannot even be satisfied on bookworm, which ships
  `libtiff.so.6`; the Ubuntu tarball has carried that same unresolved entry its
  whole life unnoticed. So the script fails only on the components the app
  genuinely needs (`CRITICAL` in that file: core Qt, the xcb platform plugin,
  QtWebEngine, the launcher) and *reports* the rest into the job summary. A
  gate that fails on things that do not matter is a gate that gets turned off.

`verify-deb-debian12` then installs the package on a **pristine** `debian:12`
with `apt-get install ./…deb` and boots it. That is the only thing validating
`Depends:` — the build container has every library installed by hand, so a
missing dependency cannot fail there. It also exercises what a `/opt` install
has and the tarball never does: a frozen app whose `sys._MEIPASS` is
root-owned and read-only.

Both jobs are `continue-on-error`, like the macOS x86_64 leg, so a Debian
hiccup never blocks a release of the artifacts that already worked. The cost
is that a release can then publish **without** the `.deb` and nothing says so
— `download-artifact` simply finds fewer files, and a `.deb` that failed
verification still ships. The `release` job already *waits* on
`verify-deb-debian12`, so dropping `continue-on-error` from these two is the
only change needed to make verification gating; worth doing once they have
proven stable on a few releases.

### The release-asset naming rule

`updater.pick_asset` finds the Linux tarball with `"-linux" in name` plus a
suffix, and takes the first match. **That predicate ships compiled into every
already-released binary**, so it cannot be fixed retroactively for anyone
already running nParse+. Any new Linux release asset must therefore be inert
to it: at most one asset may both contain `-linux` and end in `.tar.gz`.
`nparseplus_<version>_amd64.deb` satisfies that by construction. This is what
[#160](https://github.com/prokopto-dev/nparse-plus/issues/160) was, one
artifact over; `tests/test_updater.py` and `tests/test_release_workflow.py`
guard both halves.

### What CI cannot check

Nothing here proves the package runs on a real Debian 12 desktop. The
offscreen boot exercises no xcb path, no compositor, no tray, no always-on-top
or window opacity, and never starts QtWebEngine's render process. The clean
`apt` install is the strongest available signal and is still headless. One
human on a real bookworm install, once.

## Windows: the bootloader is rebuilt from source

A Windows user's antivirus flagging the download
([#122](https://github.com/prokopto-dev/nparse-plus/issues/122)) is almost
always a match on PyInstaller's *bootloader* — the small C launcher every
PyInstaller build starts from. The one in the published wheel is byte-identical
for everyone who uses the tool, malware included, so the Windows job compiles
its own:

1. `uv sync --frozen --group build` as before, then a narrow second install of
   PyInstaller from its **sdist** with `PYINSTALLER_COMPILE_BOOTLOADER=1`,
   pinned to the same version and sha256 the lock already resolved
   (`tools/pyinstaller_source_pin.py`). The lock is untouched, so macOS and
   Linux keep the wheel.
2. A check that the bootloader in `.venv` no longer equals the one inside the
   published wheel. This is load-bearing, not decorative: PyInstaller's sdist
   **also ships the prebuilt Windows bootloaders**, so a source install without
   that environment variable silently packages the bytes we are replacing and
   looks exactly like success.
3. `uv run --no-sync pyinstaller …` — `--no-sync` because a syncing `uv run`
   may put the lock's wheel back.
4. A smoke test that launches the exe: a bootloader compiled here is exactly
   the thing that could produce an exe that no longer starts, and `console=False`
   means a broken one says nothing on stderr. It asserts the process survives
   30 s **and** that the app got far enough to open `nparseplus.log`, since a
   bootloader error pops a message box and keeps the process alive.

Honest limits: a freshly built bootloader is *unknown* rather than
*known-good*, and some heuristics distrust novelty — the measure of success is
a VirusTotal comparison before and after, not the change itself. And none of
this substitutes for an Authenticode signature
([#19](https://github.com/prokopto-dev/nparse-plus/issues/19)), which is the
only thing that settles it.

## What the app checks before it installs a download

The in-app updater streams the release artifact to a `.part` staging file
under a byte budget, re-asserting `https` on **every** redirect hop (a
release URL that 302s to `http` is refused, not downloaded in plaintext),
and pins the result to the `sha256:` digest GitHub publishes for the asset
(`assets[].digest`). A mismatch is refused before anything opens the file
and names both digests.

That digest arrives over the same TLS session as the release metadata that
describes it, so it is a **channel** guarantee: it proves the object the
CDN served is the object the API described — catching a corrupted,
truncated or substituted artifact — and proves nothing against anything
able to publish a release. A per-release signed `SHA256SUMS` (minisign,
public key compiled into the app) would be the actual signature; that is
still to do.

## Flatpak: adding a permission breaks in-app update once

Flatpak refuses an update whose new version requests a permission the
installed version lacks — the portal's `UpdateMonitor.Update` fails with
`org.freedesktop.DBus.Error.NotSupported` and the user has to update with
the host tools instead. Any addition to `finish-args` in
`packaging/flatpak/io.github.prokopto_dev.nparse_plus.yml` therefore breaks
in-app update across that one release hop, and belongs in the release
notes. A permission a feature needs must ship one release *before* the
feature.

Worth knowing before reaching for one: the Flatpak portal behind in-app
updates (`CreateUpdateMonitor`, `Update`, `Spawn`) answers on
**`org.freedesktop.portal.Flatpak`**, which every sandboxed app may already
call — flatpak grants `--call=org.freedesktop.portal.*=*` by default. The
similar-looking **`org.freedesktop.Flatpak`** is a different service,
`flatpak-session-helper`, whose `Development.HostCommand` runs commands on
the host outside the sandbox; that one is a real privilege grant and is
deliberately absent from the manifest.

## Flatpak: the in-app one-click update

Inside a sandbox the update dialog's button says **Install Update** and
installs in place instead of handing over a ~200 MB bundle
(`src/nparseplus/flatpakportal.py`). The flow is four portal calls —
`CreateUpdateMonitor`, `UpdateMonitor.Update`, the `UpdateMonitor.Progress`
signals, then `Spawn` with `FLATPAK_SPAWN_FLAGS_LATEST_VERSION` to relaunch —
over [jeepney](https://pypi.org/project/jeepney/), a pure-Python D-Bus client
declared `sys_platform == 'linux'` in `pyproject.toml`. It needs **no**
`finish-args` change, for the reason the section above gives.

Everything degrades to the download path that came before it. A `PortalStatus`
of `UNAVAILABLE` — not sandboxed, no jeepney, no session bus, a portal older
than version 2, or a call the sandbox's D-Bus policy refused — is the one
outcome the user never hears about: nothing has been said, so the app quietly
downloads the bundle instead. Every other status is reported, and
`NOT_SUPPORTED` names `flatpak update io.github.prokopto_dev.nparse_plus`
rather than reading as a generic failure.

### What CI cannot check

The wire is asserted in `tests/test_flatpak_portal.py` — real jeepney messages
round-tripped through jeepney's own parser, and the whole flow driven over a
fake connection — but no CI runner has a Flatpak sandbox with a portal in it.
**These need a human on a real install, once:**

- The portal answers `CreateUpdateMonitor` under the sandbox's actual D-Bus
  policy with only the default `--call=org.freedesktop.portal.*=*`. If a
  binding turns out to need more, the correct arg is
  `--talk-name=org.freedesktop.portal.Flatpak` — scoped to the portal, never
  `org.freedesktop.Flatpak` — and it would cost the one release hop above.
- `Progress` signals arrive at all. They are emitted directly at our unique
  name, so `AddMatch` should be unnecessary; the client sends one anyway and
  ignores a refusal.
- `Update` finds the origin remote the bundle configured with `--repo-url`,
  and reports `status = 2` (done) rather than `1` (nothing to pull). On
  release day the OSTree repo can lag the GitHub release by a few minutes,
  which is exactly the `1` case and says so.
- `Spawn` relaunches onto the **new** deploy — check the version in
  Settings → General after the restart, not just that a window came back.
- An install from a `.tar.gz` (no `/.flatpak-info`) still downloads a tarball.

## gh-pages layout

One branch serves both consumers:

```
gh-pages/
  repo/                      # Flatpak OSTree repo (URL must never move)
  nparseplus.flatpakref      # embed GPG key; flatpak install source
  nparseplus.flatpakrepo
  1.4/  dev/  latest/        # mike-managed docs versions
  versions.json  index.html  # mike: version list + redirect to latest
```

The Flatpak publish step rebuilds the branch as a **single orphan
commit** each release (so OSTree objects never pile up in git history)
but seeds it from the previous tree, so the docs directories survive.
mike then commits its docs updates on top. Don't hand-edit gh-pages.
