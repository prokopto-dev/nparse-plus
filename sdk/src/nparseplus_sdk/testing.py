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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nparseplus_sdk.context import LineParser, Unsubscribe
from nparseplus_sdk.plugin import (
    OverlayRegionSpec,
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
    window_series: str = ""
    window_index: int = 0
    window_count: int = 0


def _eq(a: str, b: str) -> bool:
    """Casefold comparison — the host's own (name, group) identity rule."""
    return str(a).casefold() == str(b).casefold()


def _same_row(a: Any, b: Any) -> bool:
    return _eq(getattr(a, "name", ""), getattr(b, "name", "")) and _eq(
        getattr(a, "group", ""), getattr(b, "group", "")
    )


class FakeTimers:
    """In-memory stand-in for the host TimersService.

    Records rather than executes: nothing counts down, and there is no
    ``tick``. What it *does* reproduce is the host's **replacement rules**,
    because a plugin test that adds the same timer twice should see what the
    app would show — one row, not two:

    * ``add_timer(row, allow_duplicates=False)`` drops an existing timer with
      the same (name, group), casefolded, exactly like ``TimersService``.
    * ``add_spell(row, overwrite=True)`` does the same for spells.

    Kinds are tracked per row so replacement matches only rows added the same
    way — the stand-in for the host's ``isinstance`` check, without importing
    the host row classes.

    What it does **not** reproduce, said out loud rather than diverged from
    silently: ``add_counter`` does not increment an existing tally (the host
    bumps ``count`` and re-stamps ``updated_at``), and ``add_roll`` does not
    reset the window of the other rolls in its group. Both are stateful
    behaviours a recorder has no business guessing at — assert on ``rows``.
    """

    def __init__(self) -> None:
        self.rows: list[Any] = []
        # id(row) -> which add_* put it here. Safe as a key: the row stays
        # referenced by self.rows for exactly as long as the entry lives.
        self._kinds: dict[int, str] = {}

    def _record(self, row: Any, kind: str) -> Any:
        self.rows.append(row)
        self._kinds[id(row)] = kind
        return row

    def _replace(self, row: Any, kind: str) -> None:
        for existing in list(self.rows):
            if self._kinds.get(id(existing)) == kind and _same_row(existing, row):
                self.rows.remove(existing)
                self._kinds.pop(id(existing), None)

    def add_timer(self, row: Any, allow_duplicates: bool = False) -> Any:
        if not allow_duplicates:
            self._replace(row, "timer")
        return self._record(row, "timer")

    def add_spell(self, row: Any, overwrite: bool = True) -> Any:
        if overwrite:
            self._replace(row, "spell")
        return self._record(row, "spell")

    def add_counter(self, row: Any) -> Any:
        return self._record(row, "counter")

    def add_roll(self, row: Any) -> Any:
        return self._record(row, "roll")

    def remove_row(self, row: Any) -> bool:
        try:
            self.rows.remove(row)
        except ValueError:
            return False
        self._kinds.pop(id(row), None)
        return True

    def snapshot(self) -> list[Any]:
        return list(self.rows)

    def find(self, name: str, group: str | None = None) -> Any:
        for row in self.rows:
            if not _eq(getattr(row, "name", ""), name):
                continue
            if group is None or _eq(getattr(row, "group", ""), group):
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
        self.overlay_regions: list[OverlayRegionSpec] = []
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

    def add_overlay_region(self, spec: OverlayRegionSpec) -> None:
        self.overlay_regions.append(spec)

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
        # Through the store, exactly as HostPluginContext does — never around
        # it. Recording the row while skipping ``ctx.timers`` would hide it
        # from a test that asks the store, and would drop ``allow_duplicates``
        # on the floor, so a plugin arming the same window twice would look
        # like two rows here and one row in the app.
        add_timer = getattr(self._timers, "add_timer", None)
        if not callable(add_timer):
            raise TypeError(
                "ctx.timers must provide add_timer(row, allow_duplicates=False) for "
                "add_window_timer(); the host TimersService and FakeTimers both do. "
                "Inject a double with that method, or leave timers unset to get a "
                "FakeTimers."
            )
        add_timer(row, allow_duplicates=allow_duplicates)
        self.window_timers.append(row)
        return row

    def add_window_series(
        self,
        name: str,
        *,
        group: str,
        started_at: datetime,
        windows: Sequence[tuple[float, float]],
    ) -> list[FakeWindowTimer]:
        """Record every candidate window of one spawn, and return the rows.

        The same set-level rules the host enforces, since a caller that gets
        them wrong should hear about it in a test rather than in game.
        """
        if not windows:
            raise ValueError("a window series needs at least one candidate window")
        previous_end: float | None = None
        for base_seconds, window_seconds in windows:
            if window_seconds <= 0:
                raise ValueError("every candidate window must be a positive span")
            if previous_end is not None and base_seconds < previous_end:
                raise ValueError("candidate windows must be in ascending, non-overlapping order")
            previous_end = base_seconds + window_seconds
        series = f"{group}|{name}|{started_at.isoformat()}"
        rows: list[FakeWindowTimer] = []
        for index, (base_seconds, window_seconds) in enumerate(windows, start=1):
            ends_at = started_at + timedelta(seconds=base_seconds)
            row = FakeWindowTimer(
                name=name,
                group=group,
                ends_at=ends_at,
                window_ends_at=ends_at + timedelta(seconds=window_seconds),
                total_duration_s=float(base_seconds),
                window_series=series,
                window_index=index,
                window_count=len(windows),
            )
            # Duplicates always allowed: the candidates share a name.
            add_timer = getattr(self._timers, "add_timer", None)
            if not callable(add_timer):
                raise TypeError(
                    "ctx.timers must provide add_timer(row, allow_duplicates=False) for "
                    "add_window_series(); leave timers unset to get a FakeTimers."
                )
            add_timer(row, allow_duplicates=True)
            self.window_timers.append(row)
            rows.append(row)
        return rows

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
