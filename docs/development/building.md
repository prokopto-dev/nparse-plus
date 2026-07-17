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

## Linux (tarball + Flatpak)

```bash
uv sync --group build
uv run pyinstaller packaging/nparseplus.spec --noconfirm
tar -C dist -czf nparseplus-linux-x86_64.tar.gz nparseplus
```

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
