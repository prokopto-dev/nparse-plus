"""Event handlers — bus subscribers that drive services and UI state.

Ports of EQTool Services/Handlers. Handlers are constructed once at
composition time (subscription happens in ``__init__``) and kept alive in a
list, mirroring EQTool's ``IEnumerable<BaseHandler>`` constructor trick.
"""

from nparseplus.core.handlers.base import BaseHandler

__all__ = ["BaseHandler"]
