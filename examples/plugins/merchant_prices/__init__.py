"""Merchant Prices — the full-API nParse+ example plugin.

Tracks the items you offer for sale (your own ``You auction, 'WTS …'``
lines), polls PigParse for their WTS price stats on a throttle, and shows
them in an overlay window. Demonstrates every v1 plugin capability:

- subscribing to a typed bus event (``CommsEvent``) feeding plugin state
  (chat lines are consumed by the built-in comms parser, so subscribing —
  not ``add_parser`` — is the right tool; ``add_parser`` is for log lines
  the app doesn't already understand)
- per-plugin persistent storage (the tracked item list + poll interval)
- a periodic driver tick (``add_tick``) that never blocks on the network
- ``submit`` + ``ctx.pigparse.item_prices`` for threaded REST fetches whose
  results apply back on the driver thread
- an overlay window (``add_window`` + ``PluginWindow``) polling a snapshot
- a settings page (``add_settings_page``) for the poll interval

Install: zip this directory (the ``merchant_prices`` folder itself) and use
Settings > Plugins > Install from file, or copy the folder into the plugins
directory.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from nparseplus_sdk import (
    NParsePlugin,
    PluginContext,
    PluginMeta,
    PluginSettingsPageSpec,
    PluginWindowSpec,
)

from .pricing import extract_wts_items, merge_tracked

DEFAULT_POLL_SECONDS = 300
MIN_POLL_SECONDS = 60


def _watch_own_auctions(ctx: PluginContext, plugin: MerchantPricesPlugin) -> None:
    """Subscribe to your own auction lines via the typed comms event."""
    from nparseplus_sdk.events import CommsChannel, CommsEvent

    def on_comms(event: Any) -> None:
        if event.channel != CommsChannel.AUCTION or event.sender != "You":
            return
        items = extract_wts_items(event.content)
        if items:
            plugin.track_items(items)

    ctx.subscribe(CommsEvent, on_comms)


class MerchantPricesPlugin(NParsePlugin):
    meta = PluginMeta(
        id="merchant-prices",
        name="Merchant Prices",
        version="1.0.0",
        description=(
            "Tracks the items you auction (WTS) and shows their PigParse "
            "price history in an overlay window."
        ),
        author="nParse+ examples",
        requires_sdk=">=1.0,<2",
    )

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        # Snapshot the window polls; guarded by a lock only because the
        # GUI reads while the driver thread writes (plain dict swap).
        self._lock = threading.Lock()
        self._items: list[str] = []
        self._prices: dict[str, int] = {}  # item name -> 6-month WTS average (pp)
        self._last_poll: datetime | None = None
        self._poll_seconds = DEFAULT_POLL_SECONDS
        self._version = 0  # bumped on every state change; window dirty-check

    # --- lifecycle ----------------------------------------------------------
    def activate(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        stored = ctx.storage.load()
        with self._lock:
            self._items = [str(i) for i in stored.get("items", [])]
            self._poll_seconds = max(
                MIN_POLL_SECONDS, int(stored.get("poll_seconds", DEFAULT_POLL_SECONDS))
            )
        try:
            _watch_own_auctions(ctx, self)
        except ImportError:
            ctx.logger.warning("host events unavailable (standalone run); not watching auctions")
        ctx.add_tick(self._tick)
        ctx.add_window(
            PluginWindowSpec(
                key="prices",
                title="Merchant Prices",
                factory=self._make_window,
                default_geometry=(220, 220, 340, 260),
            )
        )
        ctx.add_settings_page(
            PluginSettingsPageSpec(
                title="Merchant Prices",
                builder=self._build_settings_page,
                apply=self._apply_settings_page,
            )
        )

    def deactivate(self) -> None:
        self._persist()

    # --- state (driver thread) ---------------------------------------------
    def track_items(self, items: list[str]) -> None:
        with self._lock:
            merged = merge_tracked(self._items, items)
            if merged == self._items:
                return
            self._items = merged
            self._version += 1
            self._last_poll = None  # fetch fresh prices on the next tick
        self._persist()

    def _tick(self, now: datetime) -> None:
        assert self._ctx is not None
        with self._lock:
            items = list(self._items)
            due = self._last_poll is None or (
                (now - self._last_poll).total_seconds() >= self._poll_seconds
            )
        if not items or not due:
            return
        server = self._ctx.player.server
        if server is None:
            return
        self._last_poll = now
        api = self._ctx.pigparse
        server_int = int(server)
        self._ctx.submit(
            lambda: api.item_prices(server_int, items),
            self._apply_prices,
        )

    def _apply_prices(self, prices: Any) -> None:
        """Runs on the driver thread (delivered via the coordinator inbox)."""
        if not prices:
            return
        with self._lock:
            for record in prices:
                self._prices[record.item_name] = record.total_wts_last_6_months_average
            self._version += 1

    def _persist(self) -> None:
        assert self._ctx is not None
        with self._lock:
            payload = {"items": list(self._items), "poll_seconds": self._poll_seconds}
        self._ctx.storage.save(payload)

    # --- snapshot for the window (GUI thread) -------------------------------
    def snapshot(self) -> tuple[int, list[tuple[str, int | None]]]:
        with self._lock:
            rows = [(name, self._prices.get(name)) for name in self._items]
            return self._version, rows

    def clear_items(self) -> None:
        with self._lock:
            self._items = []
            self._prices = {}
            self._version += 1
        self._persist()

    # --- GUI contributions (imported lazily: Qt only exists in the app) -----
    def _make_window(self, wctx: Any) -> Any:
        from .window import MerchantPricesWindow

        return MerchantPricesWindow(wctx, self)

    def _build_settings_page(self, parent: Any) -> Any:
        from .window import build_settings_page

        return build_settings_page(parent, self._poll_seconds)

    def _apply_settings_page(self, page: Any) -> None:
        from .window import read_settings_page

        with self._lock:
            self._poll_seconds = max(MIN_POLL_SECONDS, read_settings_page(page))
        self._persist()


def create_plugin() -> MerchantPricesPlugin:
    return MerchantPricesPlugin()
