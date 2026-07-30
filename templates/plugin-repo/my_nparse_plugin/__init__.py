"""My nParse+ Plugin — template starting point.

Demonstrates the common plugin shape: a typed event subscription, persistent
storage, TTS, a timer row, and an overlay window. Say ``hello template`` in
game to see it fire. Replace everything here with your plugin.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from nparseplus_sdk import (
    NParsePlugin,
    PluginContext,
    PluginMeta,
    PluginWindowSpec,
)

TRIGGER_TEXT = "hello template"
TIMER_SECONDS = 20


class MyPlugin(NParsePlugin):
    meta = PluginMeta(
        id="my-nparse-plugin",  # TODO template user: pick a unique id
        name="My nParse+ Plugin",
        version="0.1.0",
        requires_sdk=">=1.0,<2",
        description="Template plugin — say 'hello template' in game.",
        author="Your Name",
        homepage="https://github.com/you/my-nparse-plugin",
    )

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        # The window (GUI thread) reads what handlers (driver thread) write:
        # keep shared state tiny and guarded.
        self._lock = threading.Lock()
        self._greetings = 0

    # --- lifecycle ----------------------------------------------------------
    def activate(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        with self._lock:
            self._greetings = int(ctx.storage.load().get("greetings", 0))

        # Chat lines are consumed by the app's comms parser and re-published
        # as typed CommsEvents — subscribe, don't add_parser, for chat.
        # Host classes import lazily so `nparseplus-plugin validate` works
        # in a bare dev environment too.
        try:
            from nparseplus_sdk.events import CommsChannel, CommsEvent
        except ImportError:
            ctx.logger.warning("host events unavailable (standalone run); inert")
            return

        def on_comms(event: Any) -> None:
            if event.channel != CommsChannel.SAY or event.sender != "You":
                return
            if event.content.strip().lower() != TRIGGER_TEXT:
                return
            self._on_greeting(event)

        ctx.subscribe(CommsEvent, on_comms)
        ctx.add_window(
            PluginWindowSpec(
                key="main",
                title="My nParse+ Plugin",
                factory=self._make_window,
                default_geometry=(240, 240, 260, 140),
            )
        )

    def deactivate(self) -> None:
        self._persist()

    # --- driver-thread handlers --------------------------------------------
    def _on_greeting(self, event: Any) -> None:
        assert self._ctx is not None
        from nparseplus_sdk.timers import TimerRow

        now = event.timestamp
        self._ctx.timers.add_timer(
            TimerRow(
                name="Template timer",
                group=" My Plugin",
                updated_at=now,
                ends_at=now + timedelta(seconds=TIMER_SECONDS),
                total_duration_s=float(TIMER_SECONDS),
            )
        )
        self._ctx.speaker.speak("Hello from the template")
        with self._lock:
            self._greetings += 1
        self._persist()

    def _persist(self) -> None:
        assert self._ctx is not None
        with self._lock:
            payload = {"greetings": self._greetings}
        self._ctx.storage.save(payload)

    # --- GUI-thread snapshot ------------------------------------------------
    def greeting_count(self) -> int:
        with self._lock:
            return self._greetings

    def _make_window(self, wctx: Any) -> Any:
        from .window import MyPluginWindow

        return MyPluginWindow(wctx, self)


def create_plugin() -> MyPlugin:
    return MyPlugin()
