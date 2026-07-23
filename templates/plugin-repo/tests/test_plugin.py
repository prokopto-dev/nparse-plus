"""Unit tests against the SDK's FakePluginContext — no app, no Qt, no network."""

from __future__ import annotations

from my_nparse_plugin import MyPlugin, create_plugin

from nparseplus_sdk.testing import FakePluginContext


def test_metadata() -> None:
    meta = MyPlugin.meta
    assert meta.id == "my-nparse-plugin"
    assert meta.requires_sdk == ">=1.0,<2"


def test_activation_registers_window_and_subscription() -> None:
    ctx = FakePluginContext()
    plugin = create_plugin()
    plugin.activate(ctx)
    assert len(ctx.windows) == 1
    assert ctx.windows[0].key == "main"
    # One CommsEvent subscription (when the host events are importable).
    assert len(ctx.subscriptions) <= 1


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
