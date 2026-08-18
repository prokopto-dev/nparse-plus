"""``ctx.eq_dir`` / ``ctx.eq_is_running`` — the SDK 1.2 install surface (#123)."""

from __future__ import annotations

from pathlib import Path

from nparseplus.core.plugins import context as context_module

from .test_context import make_ctx


def test_eq_dir_is_none_when_unset(backend, tmp_path):
    """First run has a log directory and no install — not an error state."""
    backend.settings.general.eq_install_dir = ""
    assert make_ctx(backend, tmp_path).eq_dir is None


def test_eq_dir_reads_the_configured_install(backend, tmp_path):
    install = tmp_path / "EverQuest"
    install.mkdir()
    backend.settings.general.eq_install_dir = str(install)

    eq_dir = make_ctx(backend, tmp_path).eq_dir

    assert eq_dir == install
    assert isinstance(eq_dir, Path)


def test_eq_dir_follows_a_setting_change_without_a_restart(backend, tmp_path):
    """The whole point of a property: #70 made this setting live everywhere
    else, and a plugin must not be the one consumer stuck on launch's value."""
    first, second = tmp_path / "install-a", tmp_path / "install-b"
    first.mkdir()
    second.mkdir()
    backend.settings.general.eq_install_dir = str(first)
    ctx = make_ctx(backend, tmp_path)
    assert ctx.eq_dir == first

    backend.settings.general.eq_install_dir = str(second)

    assert ctx.eq_dir == second


def test_eq_is_running_delegates_to_the_host_probe(backend, tmp_path, monkeypatch):
    calls: list[int] = []

    def fake_probe() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr("nparseplus.core.eqprocess.eq_is_running", fake_probe)

    assert make_ctx(backend, tmp_path).eq_is_running() is True
    assert calls == [1]


def test_eq_is_running_is_not_called_at_construction(backend, tmp_path, monkeypatch):
    """Building a context must never spawn pgrep — activate() builds one per
    plugin, on the GUI thread, before the driver starts (#88 is the lesson)."""

    def explode() -> bool:  # pragma: no cover - must not run
        raise AssertionError("eq_is_running probed during context construction")

    monkeypatch.setattr("nparseplus.core.eqprocess.eq_is_running", explode)

    make_ctx(backend, tmp_path)


def test_context_implements_every_protocol_member(backend, tmp_path):
    """The host implementation and the published Protocol stay in step.

    Structural rather than ``isinstance``: ``PluginContext`` is deliberately
    not ``@runtime_checkable``, and this catches a member added to the
    Protocol and never implemented — which is exactly how eq_dir would have
    shipped broken.
    """
    from nparseplus_sdk import PluginContext

    ctx = make_ctx(backend, tmp_path)
    missing = [name for name in PluginContext.__protocol_attrs__ if not hasattr(ctx, name)]

    assert missing == []
    assert {"eq_dir", "eq_is_running"} <= PluginContext.__protocol_attrs__


def test_context_module_does_not_import_eqprocess_at_module_scope():
    """The probe is imported inside the method: core.plugins.context is on the
    activate path, and pulling subprocess plumbing in for a capability most
    plugins never touch is cost every launch pays."""
    assert not hasattr(context_module, "eq_is_running")
