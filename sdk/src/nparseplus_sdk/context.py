"""The capability surface a plugin receives — ``PluginContext`` and friends.

These are :class:`typing.Protocol` definitions: the host app implements them
(``nparseplus.core.plugins.context.HostPluginContext``), plugin authors code
and type-check against them, and ``nparseplus_sdk.testing.FakePluginContext``
implements them for unit tests and the validate CLI.

Threading contract (the part that matters):

- ``activate(ctx)`` runs on the GUI thread before the log-driver starts.
- Everything registered via ``subscribe``/``add_parser``/``add_tick`` runs
  later on the app's single log-driver thread. Timer/bus access is only safe
  from there — which is exactly where your callbacks run, so mutate freely
  inside them, never from threads you create yourself.
- Never block a subscription/tick callback on network I/O: call
  ``ctx.submit(fetch, apply)`` instead. ``fetch`` runs on a worker thread;
  ``apply(result)`` is delivered back onto the driver thread, where touching
  ``ctx.timers`` or publishing is safe.
- Window/page builders run on the GUI thread; read backend state from a
  QTimer poll or the window context's Qt bridge signals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nparseplus_sdk.plugin import (
    PluginMeta,
    PluginSettingsPageSpec,
    PluginWindowSpec,
)

type Unsubscribe = Callable[[], None]


@runtime_checkable
class LineInfoLike(Protocol):
    """Structural view of the host's ``LineInfo`` (a parsed log line)."""

    @property
    def raw(self) -> str: ...
    @property
    def message(self) -> str: ...
    @property
    def timestamp(self) -> datetime: ...
    @property
    def line_number(self) -> int: ...


@runtime_checkable
class LineParser(Protocol):
    """A log-line parser: return True to consume the line.

    Matches the host's parser protocol; ``ctx`` is the host ``ParseContext``
    (``ctx.bus``, ``ctx.player``, …). Plugin parsers run after every built-in
    parser, first-match-wins, and never see lines a built-in consumed.
    """

    def handle(self, line: LineInfoLike, ctx: Any) -> bool: ...


@runtime_checkable
class PluginStorage(Protocol):
    """Per-plugin persistent storage, isolated from the app settings."""

    @property
    def data_dir(self) -> Path:
        """This plugin's private data directory (created on first use)."""
        ...

    def load(self) -> dict[str, Any]:
        """Read the plugin's JSON store (missing/corrupt -> ``{}``)."""
        ...

    def save(self, data: dict[str, Any]) -> None:
        """Atomically persist the plugin's JSON store."""
        ...


@runtime_checkable
class Speaker(Protocol):
    """Text-to-speech, matching the host audio protocol."""

    def speak(self, text: str) -> None: ...


class PluginContext(Protocol):
    """Everything a plugin may touch. Implemented by the host; stable API."""

    # --- identity / environment -------------------------------------------
    @property
    def meta(self) -> PluginMeta: ...
    @property
    def app_version(self) -> str: ...
    @property
    def sdk_version(self) -> str: ...
    @property
    def logger(self) -> logging.Logger:
        """Child of the app's logger tree — lines land in nparseplus.log."""
        ...

    @property
    def storage(self) -> PluginStorage: ...

    # --- backend capabilities (driver-thread objects) ---------------------
    @property
    def timers(self) -> Any:
        """The host TimersService (add_timer/add_counter/...). Driver thread only."""
        ...

    @property
    def player(self) -> Any:
        """The host ActivePlayer (read-only by convention)."""
        ...

    @property
    def speaker(self) -> Speaker: ...

    @property
    def pigparse(self) -> Any:
        """PigParse REST client (host ``PigParseApi`` protocol). Call it only
        inside a ``submit`` fetch — it blocks on HTTP."""
        ...

    # --- registration (call during activate) ------------------------------
    def subscribe(self, event_type: type[Any], fn: Callable[[Any], None]) -> Unsubscribe: ...

    def add_parser(self, parser: LineParser) -> None: ...

    def add_tick(self, fn: Callable[[datetime], None]) -> None:
        """Register a ~100 ms periodic callback on the driver thread."""
        ...

    def submit(
        self,
        fetch: Callable[[], Any],
        apply: Callable[[Any], None] | None = None,
    ) -> None:
        """Run ``fetch()`` on a worker thread; ``apply(result)`` on the driver
        thread. ``fetch`` exceptions are logged and drop the ``apply``."""
        ...

    def add_window(self, spec: PluginWindowSpec) -> None: ...

    def add_settings_page(self, spec: PluginSettingsPageSpec) -> None: ...
