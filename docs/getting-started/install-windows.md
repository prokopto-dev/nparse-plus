# Install on Windows

nParse+ ships as a plain zip for 64-bit Windows
(`nparseplus-<version>-win64.zip`) — no installer, no admin rights needed.

## 1. Download and unpack

1. Download the zip from the
   [latest release](https://github.com/prokopto-dev/nparse-plus/releases/latest).
2. Extract it anywhere you like (e.g. `C:\Games\nparseplus\`).
3. Run `nparseplus.exe` from the extracted folder.

nParse+ lives in the system tray (bottom-right, near the clock) — if you
don't see a window, look for the tray icon.

!!! note "SmartScreen warning"
    The binaries are not code-signed (yet), so Windows SmartScreen may show
    "Windows protected your PC" on first launch. Click **More info →
    Run anyway**. The [source is
    public](https://github.com/prokopto-dev/nparse-plus) if you'd rather
    audit or build it yourself.

## 2. Point it at your logs

Continue with [First run](first-run.md) — you'll select your EverQuest
`Logs` folder and turn on `/log on` in game.

## Updating

nParse+ checks GitHub for new releases at startup and offers the zip download
from the tray menu — unpack the new version over (or beside) the old one.
See [Updating](updating.md).

## Uninstall

Delete the extracted folder. Settings live separately under
`%LOCALAPPDATA%\nparseplus\` — delete that too for a clean slate.
