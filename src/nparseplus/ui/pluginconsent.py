"""First-load plugin consent — one warning dialog per never-seen plugin.

The answer persists either way (``PluginHost.record_consent``), so each
plugin asks exactly once; changing your mind later happens in
Settings > Plugins. The wording mirrors docs/plugins/security.md.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nparseplus.core.plugins.host import LoadedPlugin, PluginHost

CONSENT_WARNING = (
    "Plugins are third-party code that runs with the full permissions of "
    "nParse+ on your computer. nParse+ cannot verify what a plugin does — "
    "only enable plugins from authors you trust."
)


def _ask_dialog(loaded: LoadedPlugin) -> bool:
    """Show the modal consent dialog; True = enable the plugin."""
    from PySide6.QtWidgets import QMessageBox

    meta = loaded.meta
    assert meta is not None
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Enable plugin?")
    lines = [f"{meta.name} v{meta.version}"]
    if meta.author:
        lines.append(f"by {meta.author}")
    lines.append(f"from {loaded.source.location}")
    if meta.description:
        lines.append("")
        lines.append(meta.description)
    box.setText("\n".join(lines))
    box.setInformativeText(CONSENT_WARNING)
    enable = box.addButton("Enable plugin", QMessageBox.ButtonRole.AcceptRole)
    keep_disabled = box.addButton("Keep disabled", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(keep_disabled)
    box.exec()
    return box.clickedButton() is enable


def run_consent_prompts(
    host: PluginHost,
    ask: Callable[[LoadedPlugin], bool] | None = None,
) -> None:
    """Answer every pending-consent plugin via ``ask`` (injectable for tests)."""
    if ask is None:
        ask = _ask_dialog
    for loaded in host.pending_consent():
        if loaded.meta is None:
            continue
        host.record_consent(loaded.meta.id, ask(loaded))
