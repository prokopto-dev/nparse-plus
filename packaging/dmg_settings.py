"""dmgbuild settings — drag-to-Applications DMG.

    uv run --with dmgbuild dmgbuild -s packaging/dmg_settings.py \
        "nParse+" dist/nParse+.dmg
"""

import os.path

application = defines.get("app", "dist/nParse+.app")  # noqa: F821 - dmgbuild global

files = [application]
symlinks = {"Applications": "/Applications"}

icon = "packaging/icon.icns"
icon_locations = {
    os.path.basename(application): (140, 120),
    "Applications": (360, 120),
}

window_rect = ((200, 200), (500, 280))
icon_size = 80
format = "UDZO"
