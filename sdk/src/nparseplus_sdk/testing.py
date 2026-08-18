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
from dataclasses import dataclass
from datetime import datetime, timedelta
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


@dataclass
class FakeWindowTimer:
    """A recorded pop-window timer; satisfies ``WindowTimerLike``.

    ``window_opened_at`` stays None: the host stamps it on the driver tick
    that observes the crossover, and this fake never ticks. Set it by hand if
    a test needs the post-crossover shape.
    """

    name: str
    group: str
    ends_at: datetime
    window_ends_at: datetime | None = None
    window_opened_at: datetime | None = None
    total_duration_s: float = 0.0


class FakeTimers:
    """In-memory stand-in for the host TimersService.

    Records rather than executes, like the rest of this module — nothing here
    counts down, and ``tick`` does not exist. It is enough for a plugin to
    call ``ctx.timers.add_timer(...)`` at activate time without the app, which
    is what ``FakePluginContext`` needs it for.
    """

    def __init__(self) -> None:
        self.rows: list[Any] = []

    def add_timer(self, row: Any, allow_duplicates: bool = False) -> Any:
        self.rows.append(row)
        return row

    def add_spell(self, row: Any, overwrite: bool = True) -> Any:
        self.rows.append(row)
        return row

    def add_counter(self, row: Any) -> Any:
        self.rows.append(row)
        return row

    def add_roll(self, row: Any) -> Any:
        self.rows.append(row)
        return row

    def remove_row(self, row: Any) -> bool:
        try:
            self.rows.remove(row)
        except ValueError:
            return False
        return True

    def snapshot(self) -> list[Any]:
        return list(self.rows)

    def find(self, name: str, group: str | None = None) -> Any:
        for row in self.rows:
            same_group = group is None or getattr(row, "group", None) == group
            if getattr(row, "name", None) == name and same_group:
                return row
        return None


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
        eq_dir: Path | None = None,
        eq_running: bool = False,
    ) -> None:
        self._meta = meta or _FAKE_META
        self._app_version = app_version
        self._sdk_version = sdk_version
        self._storage = storage or FakeStorage()
        # Defaults to a FakeTimers rather than None: a plugin that touches
        # ctx.timers during activate() could not be exercised at all while
        # this was None, which is exactly what `validate_plugin` does.
        self._timers = FakeTimers() if timers is None else timers
        self._player = player
        self._eq_dir = eq_dir
        #: Flip in a test to exercise the "EQ is running" warning path.
        self.eq_running = eq_running
        self._speaker = FakeSpeaker()
        self._pigparse = RecordingApi()
        self.subscriptions: list[tuple[type[Any], Callable[[Any], None]]] = []
        self.parsers: list[LineParser] = []
        self.ticks: list[Callable[[datetime], None]] = []
        self.submitted: list[tuple[Callable[[], Any], Callable[[Any], None] | None]] = []
        self.windows: list[PluginWindowSpec] = []
        self.settings_pages: list[PluginSettingsPageSpec] = []
        self.window_timers: list[FakeWindowTimer] = []

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
    def eq_dir(self) -> Path | None:
        return self._eq_dir

    def eq_is_running(self) -> bool:
        """The ``eq_running`` flag — never spawns a process, unlike the host."""
        return self.eq_running

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

    def add_window_timer(
        self,
        name: str,
        *,
        group: str,
        started_at: datetime,
        base_seconds: float,
        window_seconds: float,
        allow_duplicates: bool = False,
    ) -> FakeWindowTimer:
        """Record the row the host would build, and return it.

        The one rule the host enforces that is worth enforcing here too: an
        empty window is not a pop window, and the host row model rejects it.
        Everything else records without validating — this is a recorder.
        """
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        ends_at = started_at + timedelta(seconds=base_seconds)
        row = FakeWindowTimer(
            name=name,
            group=group,
            ends_at=ends_at,
            window_ends_at=ends_at + timedelta(seconds=window_seconds),
            total_duration_s=float(base_seconds),
        )
        self.window_timers.append(row)
        if isinstance(self._timers, FakeTimers):
            # So ctx.timers.snapshot()/find() see it too, like the host store.
            self._timers.rows.append(row)
        return row

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
