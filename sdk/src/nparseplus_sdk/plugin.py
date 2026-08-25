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
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, field_validator

PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")


class PluginMeta(BaseModel):
    """Static identity + compatibility declaration for a plugin.

    ``requires_sdk`` is a PEP 440 specifier matched against the SDK version
    bundled in the host app (e.g. ``">=1.0,<2"``); ``min_app_version`` is an
    optional lower bound on the nParse+ app version. Incompatible plugins are
    refused with a readable reason — they never crash the app.

    ``update_url`` (optional) points at a registry-format index document the
    app polls to offer updates for **this plugin only**. It exists so a plugin
    distributed outside any registry can still be updated in place instead of
    uninstalled and reinstalled. It is a self-published channel: you supply
    both the artifact URL and the sha256 it is checked against, so the pin
    proves the download matches what you published and nothing more. The app
    ignores any listing in that document whose id is not yours.
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
    update_url: str = ""

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not PLUGIN_ID_RE.match(value):
            raise ValueError(
                "plugin id must match ^[a-z][a-z0-9_-]{1,39}$ (lowercase, digits, '-', '_')"
            )
        return value

    @field_validator("update_url")
    @classmethod
    def _https_feed(cls, value: str) -> str:
        """Empty (no feed) or https. A refused feed beats an ignored one.

        Rejecting here makes a bad URL a load error the author sees in
        ``nparseplus-plugin validate``, rather than a feed that silently never
        produces an update. The field is new, so this costs no compatibility.
        """
        cleaned = value.strip()
        if cleaned and not cleaned.lower().startswith("https://"):
            raise ValueError("update_url must be an https:// URL (or empty for no update feed)")
        return cleaned


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


def _no_content_hook() -> None:
    """Default :attr:`OverlayRegionContext.on_content_changed` — does nothing.

    A module-level function rather than a lambda so the dataclass default is a
    named, importable object (and reads as one in ``repr``).
    """


@dataclass(frozen=True)
class OverlayRegionSpec:
    """A region the plugin wants to occupy **inside the Event Overlay**.

    A region is a **paint surface, not a widget you can click**. The Event
    Overlay is a top-level window carrying
    ``Qt.WindowType.WindowTransparentForInput``, and Qt has no per-child
    exemption from it: outside position mode nothing inside the overlay gets
    a mouse, a key, a hover, a wheel or a context menu, and inside position
    mode every click belongs to the user repositioning their chrome. That is
    a permanent design decision, not a gap waiting to be filled — which is
    why nothing here is input-related and nothing ever will be. **If your
    add-on needs clicks, ship a window**: :class:`PluginWindowSpec` via
    ``ctx.add_window``.

    ``factory`` runs on the GUI thread and receives an
    :class:`OverlayRegionContext`; it must return a QWidget. Subclassing the
    host's ``PluginOverlayRegion`` base (see ``nparseplus_sdk.ui``) gives you
    the skinning, the sample content and the non-interactive posture for
    free.

    ``has_content`` is asked — often, on the GUI thread — whether this region
    currently has anything to show. It is **required** because the overlay
    hides itself when every region is empty, so a region with no opinion
    could never keep the overlay on screen by itself. Keep it cheap: read a
    flag, do not compute.

    ``default_anchor``/``default_dx``/``default_dy``/``default_width``/
    ``default_height`` are only where the region *starts*. Once the user drags
    it in position mode, their placement wins and is persisted under the
    namespaced region key — and is deliberately kept if the plugin is later
    disabled, so re-enabling it brings the region back where they put it.
    """

    key: str
    title: str
    factory: Callable[[OverlayRegionContext], Any]
    has_content: Callable[[], bool]
    default_anchor: Literal["top", "center", "bottom"] = "top"
    default_dx: int = 0
    default_dy: int = 0
    default_width: int | None = None
    default_height: int | None = None


@dataclass
class OverlayRegionContext:
    """Handed to :class:`OverlayRegionSpec` factories on the GUI thread.

    Loosely typed for the same reason :class:`PluginWindowContext` is — this
    module stays importable without the host. ``settings`` is the host's
    pydantic ``Settings`` root and ``bridge`` the ``QtEventBridge`` whose
    ``event_received``/``events_batch`` signals deliver bus events on the GUI
    thread; a region is display-only, so that signal (or a QTimer) is how
    anything ever changes inside it.

    ``region_key`` is the namespaced ``plugin.<id>.<key>`` the host persists
    the placement under, matching the ``window_key`` convention.

    ``on_content_changed`` tells the overlay your content's height changed so
    it can re-anchor the region and re-ask ``has_content``. Call it whenever
    you add, remove or resize what is inside — the overlay cannot see that by
    itself, and a region that grew downward will otherwise sit at its old
    size until something else moves. ``PluginOverlayRegion`` exposes it as
    ``notify_content_changed()``.
    """

    settings: Any
    region_key: str
    title: str
    on_save: Callable[[], None]
    on_content_changed: Callable[[], None] = _no_content_hook
    bridge: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


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
