"""Test doubles for plugin authors — a fully in-memory PluginContext.

``FakePluginContext`` records everything a plugin registers so unit tests
(and the ``nparseplus-plugin validate`` CLI) can activate a plugin without
the app, Qt, or the network. ``submit`` records the (fetch, apply) pair
without executing it — call :meth:`FakePluginContext.run_submitted` to drive
the pairs synchronously in a test.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from nparseplus_sdk.context import LineParser, Unsubscribe
from nparseplus_sdk.plugin import (
    PluginMeta,
    PluginSettingsPageSpec,
    PluginWindowSpec,
)

_FAKE_META = PluginMeta(id="fake", name="Fake Plugin")


class FakeStorage:
    """In-memory PluginStorage; ``data_dir`` is a lazily created temp dir."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir
        self.data: dict[str, Any] = {}
        self.save_count = 0

    @property
    def data_dir(self) -> Path:
        if self._data_dir is None:
            self._data_dir = Path(tempfile.mkdtemp(prefix="nparseplus-plugin-"))
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    def load(self) -> dict[str, Any]:
        return dict(self.data)

    def save(self, data: dict[str, Any]) -> None:
        self.data = dict(data)
        self.save_count += 1


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class RecordingApi:
    """Stands in for the PigParse client: records calls, returns None.

    Plugins should only call the API inside a ``submit`` fetch, which tests
    drive explicitly — so inert return values are fine.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Callable[..., None]:
        def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))
            return None

        return _record


class FakePluginContext:
    """Implements the PluginContext protocol entirely in memory."""

    def __init__(
        self,
        meta: PluginMeta | None = None,
        *,
        app_version: str = "0.0.0",
        sdk_version: str = "1.0.0",
        storage: FakeStorage | None = None,
        timers: Any = None,
        player: Any = None,
    ) -> None:
        self._meta = meta or _FAKE_META
        self._app_version = app_version
        self._sdk_version = sdk_version
        self._storage = storage or FakeStorage()
        self._timers = timers
        self._player = player
        self._speaker = FakeSpeaker()
        self._pigparse = RecordingApi()
        self.subscriptions: list[tuple[type[Any], Callable[[Any], None]]] = []
        self.parsers: list[LineParser] = []
        self.ticks: list[Callable[[datetime], None]] = []
        self.submitted: list[tuple[Callable[[], Any], Callable[[Any], None] | None]] = []
        self.windows: list[PluginWindowSpec] = []
        self.settings_pages: list[PluginSettingsPageSpec] = []

    # --- identity / environment -------------------------------------------
    @property
    def meta(self) -> PluginMeta:
        return self._meta

    @property
    def app_version(self) -> str:
        return self._app_version

    @property
    def sdk_version(self) -> str:
        return self._sdk_version

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"nparseplus.plugins.{self._meta.id}")

    @property
    def storage(self) -> FakeStorage:
        return self._storage

    @property
    def timers(self) -> Any:
        return self._timers

    @property
    def player(self) -> Any:
        return self._player

    @property
    def speaker(self) -> FakeSpeaker:
        return self._speaker

    @property
    def pigparse(self) -> RecordingApi:
        return self._pigparse

    # --- registration ------------------------------------------------------
    def subscribe(self, event_type: type[Any], fn: Callable[[Any], None]) -> Unsubscribe:
        entry = (event_type, fn)
        self.subscriptions.append(entry)

        def _unsubscribe() -> None:
            if entry in self.subscriptions:
                self.subscriptions.remove(entry)

        return _unsubscribe

    def add_parser(self, parser: LineParser) -> None:
        self.parsers.append(parser)

    def add_tick(self, fn: Callable[[datetime], None]) -> None:
        self.ticks.append(fn)

    def submit(
        self,
        fetch: Callable[[], Any],
        apply: Callable[[Any], None] | None = None,
    ) -> None:
        self.submitted.append((fetch, apply))

    def add_window(self, spec: PluginWindowSpec) -> None:
        self.windows.append(spec)

    def add_settings_page(self, spec: PluginSettingsPageSpec) -> None:
        self.settings_pages.append(spec)

    # --- test drivers ------------------------------------------------------
    def run_submitted(self) -> None:
        """Execute all recorded (fetch, apply) pairs synchronously."""
        pending, self.submitted = self.submitted, []
        for fetch, apply in pending:
            result = fetch()
            if apply is not None:
                apply(result)

    def publish(self, event: Any) -> None:
        """Deliver an event to matching recorded subscriptions (exact type)."""
        for event_type, fn in list(self.subscriptions):
            if type(event) is event_type:
                fn(event)
