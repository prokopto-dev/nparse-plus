# Night Vision fix

Classic EQ nights are *dark* — atmospherically at first, then mostly just
annoying. The community's fix replaces a handful of shader and sky/water
texture files in the EQ install so nights are playable without gamma
tricks. nParse+ applies it (and reverts it) with one click.

## Applying it

1. Set your **EQ install directory** in
   [Settings → General](../settings/general.md) — WINE/CrossOver paths
   work fine; it's just files.
2. [Settings → Advanced](../settings/advanced.md) → **Night Vision fix →
   Apply**. Quit EQ first; nParse+ warns if the game looks like it's
   running.
3. Restart EQ. Enjoy seeing.

The fix extracts a bundled zip (`RenderEffects/*.fxo` shader replacements
plus `Resources/*` sky/water textures — the same archive EQTool ships) over
the EQ directory.

## Reverting it

**Revert** in the same settings group restores every original file.

!!! note "Backup-first, by design"
    Where EQTool simply overwrites the game files, nParse+ first copies
    every file it would touch into a `visionfix_backup/` tree beside them.
    Revert restores from that backup — no reinstall, no guessing which
    files changed.

This is the one feature that modifies game files at all, and it only ever
runs when you click the button. Normal operation
[reads the log file only](../index.md).
