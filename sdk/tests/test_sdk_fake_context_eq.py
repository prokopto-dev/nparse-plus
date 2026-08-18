"""FakePluginContext's EQ-install surface (SDK 1.2) — fakeable, never probing."""

from __future__ import annotations

from pathlib import Path

from nparseplus_sdk.testing import FakePluginContext


def test_eq_dir_defaults_to_none():
    assert FakePluginContext().eq_dir is None


def test_eq_dir_is_whatever_the_test_passes(tmp_path: Path):
    assert FakePluginContext(eq_dir=tmp_path).eq_dir == tmp_path


def test_eq_is_running_defaults_false_and_is_flippable():
    ctx = FakePluginContext()
    assert ctx.eq_is_running() is False

    ctx.eq_running = True

    assert ctx.eq_is_running() is True


def test_eq_running_can_be_set_at_construction():
    assert FakePluginContext(eq_running=True).eq_is_running() is True


def test_eq_is_running_never_spawns_a_process(monkeypatch):
    """A plugin's unit tests must not shell out to pgrep."""
    import subprocess

    def explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("FakePluginContext spawned a process")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)

    assert FakePluginContext(eq_running=True).eq_is_running() is True


def test_fake_implements_every_protocol_member():
    """A plugin type-checked against the Protocol must be testable with the
    Fake — so the two cannot drift."""
    from nparseplus_sdk import PluginContext

    ctx = FakePluginContext()
    missing = [name for name in PluginContext.__protocol_attrs__ if not hasattr(ctx, name)]

    assert missing == []
