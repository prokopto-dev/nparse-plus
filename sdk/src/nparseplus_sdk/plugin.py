"""Plugin metadata and base class — the heart of the nParse+ plugin contract.

A plugin is a Python module (single file or package) exposing a module-level
``create_plugin() -> NParsePlugin`` factory. The host app imports the module,
calls the factory, checks ``plugin.meta`` for identity and version
compatibility, and — once the user has consented — calls
``plugin.activate(ctx)`` with a :class:`~nparseplus_sdk.context.PluginContext`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")


class PluginMeta(BaseModel):
    """Static identity + compatibility declaration for a plugin.

    ``requires_sdk`` is a PEP 440 specifier matched against the SDK version
    bundled in the host app (e.g. ``">=1.0,<2"``); ``min_app_version`` is an
    optional lower bound on the nParse+ app version. Incompatible plugins are
    refused with a readable reason — they never crash the app.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    version: str = "0.0.0"
    requires_sdk: str = ">=1.0,<2"
    min_app_version: str | None = None
    description: str = ""
    author: str = ""
    homepage: str = ""

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not PLUGIN_ID_RE.match(value):
            raise ValueError(
                "plugin id must match ^[a-z][a-z0-9_-]{1,39}$ (lowercase, digits, '-', '_')"
            )
        return value


@dataclass(frozen=True)
class PluginWindowSpec:
    """A window the plugin wants the host to create and manage.

    ``factory`` runs on the GUI thread and receives a
    :class:`PluginWindowContext`; it must return a widget exposing
    ``.toggle()`` and ``.isVisible()`` (subclassing the host's
    ``PluginWindow`` base — see ``nparseplus_sdk.ui`` — gives that plus the
    full overlay recipe for free). ``command_key`` names the in-game chat
    toggle (``toggle_<command_key>``); it defaults to
    ``<plugin_id>_<key>`` with ``-`` mapped to ``_``.
    """

    key: str
    title: str
    factory: Callable[[PluginWindowContext], Any]
    default_geometry: tuple[int, int, int, int] = (200, 200, 320, 240)
    command_key: str | None = None


@dataclass(frozen=True)
class PluginSettingsPageSpec:
    """A page the plugin contributes to the nParse+ Settings window.

    ``builder`` runs on the GUI thread with the page's parent widget and must
    return the page widget. ``apply`` (optional) is called on Settings
    "Apply && Save" with the widget ``builder`` returned; persist plugin
    config via ``ctx.storage`` inside it.
    """

    title: str
    builder: Callable[[Any], Any]
    apply: Callable[[Any], None] | None = None


@dataclass
class PluginWindowContext:
    """Handed to :class:`PluginWindowSpec` factories on the GUI thread.

    Fields are loosely typed so this module stays importable without the
    host: ``settings`` is the host's pydantic ``Settings`` root, ``bridge``
    is the ``QtEventBridge`` whose ``event_received``/``events_batch``
    signals deliver bus events on the GUI thread.
    """

    settings: Any
    window_key: str
    title: str
    default_geometry: tuple[int, int, int, int]
    on_save: Callable[[], None]
    bridge: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


class NParsePlugin:
    """Base class for nParse+ plugins.

    Subclass it, set ``meta`` as a class attribute, and implement
    ``activate``. ``activate`` runs once on the GUI thread while the app is
    composing itself (the log-driver thread has not started yet), so
    registering subscriptions, parsers, and ticks is race-free. Never block
    in ``activate``; schedule network work via ``ctx.submit``.
    """

    meta: ClassVar[PluginMeta]

    def activate(self, ctx: Any) -> None:  # ctx: PluginContext
        raise NotImplementedError

    def deactivate(self) -> None:
        """Called at app shutdown (best-effort). Default: no-op."""
