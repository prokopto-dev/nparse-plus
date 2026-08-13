# The mark

Everything visual that identifies nParse+ — the app icon, the tray icon, the
docs logo, the README banner, the social card — is generated from **one file**:

```
data/assets/icon.svg
```

Regenerate after editing it:

```bash
uv run python tools/gen_icons.py     # every raster artifact
sh packaging/make_icns.sh            # packaging/icon.icns (macOS only)
```

Outputs are committed, like `tools/convert_*.py` and `gen_registry_schema.py`.
This file lives in `dev-notes/` and never publishes.

## What it is

An angular lowercase **n** — a rune cut from straight lines, no curve anywhere
— inside a ring, engraved gold on black glass, seated on a notched Velious
plate. Every colour is a real value from `src/nparseplus/ui/skins.py`, so the
icon belongs on the same shelf as the `velious` skin. The geometry and the
reasoning behind each number are commented in the SVG itself.

It replaced `data/assets/icon.xcf`, a 64x64 GIMP file inherited from the
[nParse](https://github.com/nomns/nparse) fork — a black-on-white "n" that was
never nParse+'s, could not be diffed, and had no resolution to give. Every
large representation we shipped before this was an upscale of it, which
`make_icns.sh` used to admit in its own header comment.

### Two things were tried and rejected, on evidence

- **Nauthiz (ᚾ)**, the actual Elder Futhark n-rune — a stave crossed by one
  diagonal. It is the correct letter and it is unusable: a diagonal through a
  ring is the universal prohibition sign, so it reads as "no entry" at every
  size.
- **A "+" set into the ring.** At 16px it is a two-pixel smudge, and breaking
  the ring to seat it costs more than the plus adds. The "+" lives in the
  wordmark lockup instead, where it has room.

## The 16px rule

The mark's only hard requirement is that it keeps a clean silhouette at 16px,
because a taskbar is where an app icon is actually read. Stroke weights are
chosen so the 16px raster lands on whole pixels, which is also why
`data/ui/` holds **one PNG per size** rather than one large PNG Qt downscales
— `ui/appicon.py` assembles them into a single `QIcon` and Qt picks the native
render for whatever is asking.

`tests/tools/test_gen_icons.py::test_sixteen_pixel_render_still_reads` is the
guard: it asserts the notched corners stay transparent, that real gold
survives the raster, and that there is still a dark field behind it. It is
deliberately not a golden-image comparison — Qt's rasterizer is not
byte-stable across PySide6 versions, which is also why `gen_icons.py` has no
`--check` mode.

## What lands where

| Output | Used by |
| --- | --- |
| `data/ui/icon.png` (256) | Qt window icon, Flatpak `hicolor/256x256` |
| `data/ui/icon-{16,24,32,48,64,128}.png` | `ui/appicon.py`, Flatpak `hicolor/*` |
| `data/ui/icon.ico` | Windows — PyInstaller `EXE(icon=…)` |
| `packaging/icon.icns` | macOS `.app` bundle + the DMG volume icon |
| `docs/assets/images/nparseplus-mark.png` | mkdocs `theme.logo` |
| `docs/assets/images/favicon.png` | mkdocs `theme.favicon` |
| `docs/assets/images/nparseplus-logo.png` | the README header banner |
| `docs/assets/images/social-preview.png` | **manual upload — see below** |

PyInstaller needs no change for any of these: the spec bundles the whole
`data/` root, so new files under `data/ui/` are picked up automatically, and
the two icons it names by path (`packaging/icon.icns`, `data/ui/icon.ico`) keep
their filenames.

The Flatpak manifest installs six raster sizes plus the SVG into
`hicolor/scalable`. It adds **no `finish-args`** — see CLAUDE.md → Packaging
for why a new permission breaks in-app update across exactly one release hop.

## The one manual step: the GitHub social preview

GitHub's social preview image can **only** be uploaded through the web UI —
there is no `gh` command and no REST endpoint for it. So the card is committed
to the repo and a human has to attach it once:

> **Settings → General → Social preview → Edit → Upload an image**, and pick
> `docs/assets/images/social-preview.png` (1280x640).

It only needs doing again if the card changes.
