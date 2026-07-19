"""Legacy settings signals.

The legacy SettingsWindow QDialog (and its CustomTriggerSettings child) were
replaced by ui.settingswindow.UnifiedSettingsWindow; only the signal hub
remains — ParserWindow subscribes to ``config_updated`` to live-apply window
opacity/flags when the unified window saves legacy keys.
"""

from PySide6.QtCore import QObject, Signal


class SettingsSignals(QObject):
    config_updated = Signal()

    def __init__(self):
        super().__init__()
