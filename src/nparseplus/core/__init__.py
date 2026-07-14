"""Qt-free core: log tailing, parsing, events, handlers, and domain services.

Nothing under ``nparseplus.core`` may import PySide6 (enforced by
``tests/test_architecture.py``). UI code bridges to this layer via
``nparseplus.ui.qtbridge``.
"""
