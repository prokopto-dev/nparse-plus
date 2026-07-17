---
name: verify
description: Drive the real nParse+ app (offscreen) to verify UI/backend changes end-to-end via the log-file surface.
---

# Verifying nParse+ changes by running the app

The app's real input surface is the EQ log file: `LogDriver` polls the newest
`eqlog_*_*.txt` in the configured dir every 100 ms and everything downstream
(parsers → bus → windows) reacts. GUI automation isn't needed — drive by
appending log lines and observe window state programmatically.

## Recipe

1. Build the whole real app in-process, offscreen (works headless, macOS/CI):

   ```python
   os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
   # scratch settings.json: {"general": {"eq_log_dir": <scratch dir with an
   #   eqlog_Name_P1999Green.txt>, "update_check": false}, "sharing": {"mode": "off"}}
   from nparseplus.app import create_app
   ctx = create_app([sys.argv[0]], settings_file=scratch_settings)
   ctx.backend.start()
   ```

   Run from the repo root (`create_app` chdirs for `data/`). Use
   `QTimer.singleShot` steps + `ctx.app.exec()`; allow ~500 ms after each
   appended line for the driver poll + queued bridge delivery.

2. Append log lines with the exact stamp format:
   `[Wed Jul 15 12:00:00 2026] You begin casting Clarity.`
   (`datetime.now().strftime("%a %b %d %H:%M:%S %Y")`). Typed window commands
   (`toggle_clickthrough_maps`, `toggle_maps`, …) also go through this path.

3. Observe: `ctx.app.maps_window`, `ctx.spell_window`, the Settings window via
   `ctx.app._backend_windows["Settings"]`, flags via `win.windowFlags()`.
   To click real buttons: `window.findChildren(QPushButton)` by text
   (Settings apply button is `"Apply && Save"`).

## Gotchas

- **The legacy `nparse.config.json` in the repo root is LIVE dev state and the
  app writes to it** (window toggles, geometry on quit, settings Apply). Back
  it up before a run and restore it after — a silent `cp` failure here mutates
  the developer's config (it is gitignored, unrecoverable from git).
- The dev config may have windows toggled off; call `win.toggle()` before
  visibility assertions.
- Offscreen quantizes `windowOpacity()` to 8-bit (0.5 → 0.498) and ignores
  `raise_()` (warning only).
- PySide6 6.11 does NOT abort on Python slot/virtual-override exceptions —
  it prints and continues. Don't "verify" crash fixes by expecting process
  death; check the crash log (`crashguard`) instead.
- OTHER-only spells (for `ClassDetectedEvent(OTHER)` scenarios): e.g.
  "Aura of Red Petals" — find via
  `set(spell.class_levels) == {PlayerClass.OTHER}` over `backend.spells.spells`.
- Background app processes get reaped between Bash calls; for a surviving
  instance use `nohup ... & disown` (see CLAUDE.md).
