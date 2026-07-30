"""Unit tests against the SDK's FakePluginContext — no app, no Qt, no network."""

from __future__ import annotations

from my_nparse_plugin import MyPlugin, create_plugin

from nparseplus_sdk.testing import FakePluginContext


def test_metadata() -> None:
    meta = MyPlugin.meta
    assert meta.id == "my-nparse-plugin"
    assert meta.requires_sdk == ">=1.0,<2"


def _host_events_available() -> bool:
    """True when nParse+ itself is installed (not just the SDK).

    ``nparseplus_sdk.events`` re-exports the host app's event classes lazily,
    so it only resolves when ``nparseplus`` is importable. CI installs the SDK
    alone, a live nParse+ run has both — the plugin must work either way.
    """
    try:
        from nparseplus_sdk.events import CommsEvent  # noqa: F401
    except ImportError:
        return False
    return True


def test_activation_registers_window() -> None:
    # The window needs nothing from the host, so it is always registered.
    ctx = FakePluginContext()
    plugin = create_plugin()
    plugin.activate(ctx)
    assert len(ctx.windows) == 1
    assert ctx.windows[0].key == "main"


def test_activation_subscribes_when_host_events_are_available() -> None:
    ctx = FakePluginContext()
    plugin = create_plugin()
    plugin.activate(ctx)
    expected = 1 if _host_events_available() else 0
    assert len(ctx.subscriptions) == expected


def test_storage_roundtrip() -> None:
    ctx = FakePluginContext()
    plugin = create_plugin()
    plugin.activate(ctx)
    plugin._greetings = 3
    plugin.deactivate()
    assert ctx.storage.data == {"greetings": 3}

    restored = create_plugin()
    restored.activate(FakePluginContext(storage=ctx.storage))
    assert restored.greeting_count() == 3
