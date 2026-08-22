# Building packages

All release artifacts are PyInstaller onedir builds, wrapped per
platform. CI builds everything on tag push
([release flow](releasing.md)); locally:

## macOS (.app / DMG)

```bash
uv sync --group build
uv run pyinstaller packaging/nparseplus.spec --noconfirm   # dist/nParse+.app
codesign --force --deep -s - "dist/nParse+.app"            # ad-hoc sign
uv run dmgbuild -s packaging/dmg_settings.py "nParse+" dist/nParse+.dmg
```

## Windows (zip)

```powershell
uv sync --group build
uv run pyinstaller packaging/nparseplus.spec --noconfirm
Compress-Archive -Path dist/nparseplus -DestinationPath dist/nparseplus-win64.zip
```

That gives you a working build, but **not** the one CI ships: the release job
compiles the PyInstaller bootloader from source first, because the prebuilt
one in the wheel is byte-identical for every PyInstaller user on earth and is
what heuristic antivirus engines match on
([#122](https://github.com/prokopto-dev/nparse-plus/issues/122)). To do the
same locally you need MSVC installed, and then:

```powershell
uv sync --group build
uv run python tools/pyinstaller_source_pin.py requirements -o pyi-source.txt
$env:PYINSTALLER_COMPILE_BOOTLOADER = "1"
uv pip install --no-deps --reinstall-package pyinstaller `
    --no-binary pyinstaller --require-hashes -r pyi-source.txt
uv run --no-sync pyinstaller packaging/nparseplus.spec --noconfirm
```

`PYINSTALLER_COMPILE_BOOTLOADER` is not optional: PyInstaller's sdist ships
the prebuilt Windows bootloaders too, so a plain source install packages the
exact bytes this is meant to replace. CI proves the swap happened by hashing
the result against the published wheel's copy — see
[Release flow](releasing.md#windows-the-bootloader-is-rebuilt-from-source).

macOS and Linux stay on the wheel. The macOS bootloader build is
byte-reproducible (a local `waf` build of 6.21.0 reproduces the published
`runw` exactly), so rebuilding it there would change nothing.

## Linux (tarball + Flatpak + Debian package)

```bash
uv sync --group build
uv run pyinstaller packaging/nparseplus.spec --noconfirm
tar -C dist -czf nparseplus-linux-x86_64.tar.gz nparseplus
```

The Debian package wraps the same onedir build too:

```bash
uv run python packaging/deb/build_deb.py \
    --dist-dir dist/nparseplus --version "$(uv run python -c \
    'import nparseplus; print(nparseplus.__version__)')" --outdir dist
```

**Build it on Debian 12 or the artifact is pointless.** The whole reason it
exists is that an artifact's glibc floor is its build host's, so producing it
anywhere newer just makes a `.deb` with the tarball's floor. CI runs the build
inside a `debian:12` container and passes the measured floor in with
`--glibc-floor`; locally, `docker run --rm -v "$PWD:/src" -w /src debian:12`
is the equivalent. See
[Linux: two builds, two glibc floors](releasing.md#linux-two-builds-two-glibc-floors).

The Flatpak wraps the same onedir build via the manifest in
[`packaging/flatpak/`](https://github.com/prokopto-dev/nparse-plus/tree/master/packaging/flatpak)
— building it needs a Linux box with `flatpak-builder` (commands are in
the manifest header; CI does this on release). Release builds are
GPG-signed and published to an OSTree repo on GitHub Pages, which is what
makes [`flatpak update`](../getting-started/install-flatpak.md#5-updating)
work.

## Documentation site

```bash
uv sync --group docs
uv run mkdocs serve            # live preview at http://127.0.0.1:8000
uv run mkdocs build --strict   # the CI gate
```

Versioned deploys use [mike](https://github.com/jimporter/mike); CI
handles them ([release flow](releasing.md)). Screenshots go in
`docs/assets/screenshots/` — missing ones render as placeholders, and
`docs/dev-notes/screenshot-checklist.md` lists what's wanted.
