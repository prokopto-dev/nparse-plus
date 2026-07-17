"""MkDocs hook: swap references to not-yet-captured screenshots for a styled
placeholder so ``mkdocs build --strict`` passes and pages degrade gracefully.

Drop the real PNG into docs/assets/screenshots/ (see
docs/dev-notes/screenshot-checklist.md for the wanted filenames) and the
placeholder is replaced by the image with no other change needed.
"""

import re
from pathlib import Path

_SCREENSHOT_IMG = re.compile(r"!\[([^\]]*)\]\(((?:\.\./)*assets/screenshots/[\w.-]+)\)")


def on_page_markdown(markdown, page, config, files):
    page_dir = Path(page.file.abs_src_path).parent

    def replace(match: re.Match[str]) -> str:
        alt, rel = match.group(1), match.group(2)
        if (page_dir / rel).resolve().exists():
            return match.group(0)
        name = Path(rel).name
        label = f"{alt} — " if alt else ""
        return (
            f'<div class="screenshot-pending" markdown>{label}screenshot pending '
            f"(<code>{name}</code>)</div>"
        )

    return _SCREENSHOT_IMG.sub(replace, markdown)
