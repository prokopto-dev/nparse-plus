"""run_consent_prompts: each pending plugin asked once, answers recorded."""

from __future__ import annotations

from nparseplus.core.plugins.discovery import PluginSource
from nparseplus.core.plugins.host import LoadedPlugin
from nparseplus.ui.pluginconsent import run_consent_prompts
from nparseplus_sdk import PluginMeta


def pending(plugin_id: str) -> LoadedPlugin:
    return LoadedPlugin(
        source=PluginSource(
            origin="dir", name=plugin_id, location=f"/plugins/{plugin_id}.py", load=lambda: None
        ),
        status="pending_consent",
        meta=PluginMeta(id=plugin_id, name=plugin_id.title()),
    )


class FakeHost:
    def __init__(self, plugins: list[LoadedPlugin]) -> None:
        self._plugins = plugins
        self.recorded: list[tuple[str, bool]] = []

    def pending_consent(self) -> list[LoadedPlugin]:
        return list(self._plugins)

    def record_consent(self, plugin_id: str, allowed: bool) -> None:
        self.recorded.append((plugin_id, allowed))


def test_each_pending_plugin_asked_and_recorded() -> None:
    host = FakeHost([pending("alpha"), pending("bravo")])
    answers = {"alpha": True, "bravo": False}
    asked: list[str] = []

    def ask(loaded: LoadedPlugin) -> bool:
        assert loaded.meta is not None
        asked.append(loaded.meta.id)
        return answers[loaded.meta.id]

    run_consent_prompts(host, ask=ask)
    assert asked == ["alpha", "bravo"]
    assert host.recorded == [("alpha", True), ("bravo", False)]


def test_no_pending_plugins_asks_nothing() -> None:
    host = FakeHost([])
    run_consent_prompts(host, ask=lambda loaded: (_ for _ in ()).throw(AssertionError))
    assert host.recorded == []


def test_metaless_pending_entry_skipped() -> None:
    broken = pending("ghost")
    broken.meta = None
    host = FakeHost([broken])
    run_consent_prompts(host, ask=lambda loaded: True)
    assert host.recorded == []
