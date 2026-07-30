"""Settings > Plugins > Plugin registries: the list widget and its add flow."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QCheckBox, QInputDialog, QMessageBox

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core.plugins.host import PluginHost
from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL
from nparseplus.ui import pluginregistries
from nparseplus.ui.pluginregistries import (
    CONSENT_WARNING,
    REGISTRY_WARNING,
    RegistryListWidget,
    registry_confirm_text,
)

pytestmark = pytest.mark.qt

GUILD = "https://guild.example/index.json"


@pytest.fixture
def settings() -> Settings:
    settings = Settings()
    settings.sharing.mode = "off"
    return settings


@pytest.fixture
def host(settings: Settings, tmp_path: Path) -> PluginHost:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    backend = build_backend(settings, speaker=NullSpeaker())
    return PluginHost(settings, backend, "1.15.0", plugins_dir_override=plugins_dir)


def make_widget(qtbot, host: PluginHost) -> RegistryListWidget:
    widget = RegistryListWidget(host)
    qtbot.addWidget(widget)
    return widget


def _prompts(monkeypatch, *values):
    """Queue QInputDialog.getText answers as (text, ok) pairs, in order."""
    answers = iter(values)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: next(answers)))


def _confirm(monkeypatch, accept: bool) -> list[tuple]:
    seen: list[tuple] = []

    def fake(parent, url, name=""):
        seen.append((url, name))
        return accept

    monkeypatch.setattr(pluginregistries, "confirm_add_registry", fake)
    return seen


# --- warning text -----------------------------------------------------------


def test_registry_warning_states_the_limit_of_the_hash() -> None:
    assert "sha256" in REGISTRY_WARNING
    lowered = REGISTRY_WARNING.lower()
    assert "not a review" in lowered
    assert "any code" in lowered


def test_confirm_text_names_the_url_and_stacks_both_warnings() -> None:
    text = registry_confirm_text(GUILD, "Guild registry")
    assert GUILD in text
    assert "Guild registry" in text
    assert REGISTRY_WARNING in text
    # The plugin-level warning follows as a second paragraph: adding a
    # registry is a decision about code that will later run unsandboxed.
    assert CONSENT_WARNING in text
    assert text.index(REGISTRY_WARNING) < text.index(CONSENT_WARNING)


def test_confirm_text_without_a_name_still_names_the_url() -> None:
    assert GUILD in registry_confirm_text(GUILD)


# --- the built-in row -------------------------------------------------------


def test_the_builtin_registry_is_the_first_row(qtbot, host) -> None:
    widget = make_widget(qtbot, host)
    assert widget._table.rowCount() == 1
    assert widget._table.item(0, 2).text() == DEFAULT_REGISTRY_URL


def test_remove_button_is_disabled_on_the_builtin_row(qtbot, host) -> None:
    widget = make_widget(qtbot, host)
    widget._table.setCurrentCell(0, 1)
    assert not widget._remove_button.isEnabled()


def test_removing_the_builtin_is_refused_and_the_row_survives(qtbot, host, monkeypatch) -> None:
    """Second guard: the handler refuses even if the button is bypassed."""
    infos: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a)))
    questions: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: questions.append(a)))
    widget = make_widget(qtbot, host)
    widget._table.setCurrentCell(0, 1)
    widget._remove_selected()
    assert infos, "no refusal message was shown"
    assert "cannot be removed" in infos[0][2]
    assert not questions, "the built-in row must not even offer a confirm"
    widget.refresh()
    assert widget._table.item(0, 2).text() == DEFAULT_REGISTRY_URL


def test_unticking_the_builtin_persists(qtbot, host, settings: Settings) -> None:
    widget = make_widget(qtbot, host)
    box = widget._table.cellWidget(0, 0)
    assert isinstance(box, QCheckBox) and box.isChecked()
    box.setChecked(False)
    assert settings.plugins.default_registry_enabled is False
    assert host.enabled_registries() == []


def test_rendering_the_stored_state_does_not_write_it_back(qtbot, host, settings) -> None:
    """Restoring the stored ticks must not look like user toggles."""
    host.add_registry(GUILD, "Guild registry")
    saves: list[int] = []
    host._request_save = lambda: saves.append(1)
    make_widget(qtbot, host)  # both rows are ticked; drawing them saves nothing
    assert saves == []
    assert settings.plugins.registries[0].enabled is True


# --- add --------------------------------------------------------------------


def test_add_confirms_then_appends(qtbot, host, settings: Settings, monkeypatch) -> None:
    _prompts(monkeypatch, (f"  {GUILD} ", True), ("Guild registry", True))
    seen = _confirm(monkeypatch, True)
    widget = make_widget(qtbot, host)
    widget._add()
    assert seen == [(GUILD, "Guild registry")]
    assert [(s.url, s.name) for s in settings.plugins.registries] == [(GUILD, "Guild registry")]
    assert widget._table.rowCount() == 2
    assert widget._table.item(1, 1).text() == "Guild registry"


def test_add_declined_writes_nothing(qtbot, host, settings: Settings, monkeypatch) -> None:
    _prompts(monkeypatch, (GUILD, True), ("Guild registry", True))
    _confirm(monkeypatch, False)
    widget = make_widget(qtbot, host)
    widget._add()
    assert settings.plugins.registries == []
    assert widget._table.rowCount() == 1


def test_add_cancelled_at_the_url_prompt_asks_nothing_else(qtbot, host, monkeypatch) -> None:
    _prompts(monkeypatch, ("", False))
    seen = _confirm(monkeypatch, True)
    make_widget(qtbot, host)._add()
    assert seen == []


def test_add_cancelled_at_the_name_prompt_writes_nothing(
    qtbot, host, settings: Settings, monkeypatch
) -> None:
    _prompts(monkeypatch, (GUILD, True), ("", False))
    seen = _confirm(monkeypatch, True)
    make_widget(qtbot, host)._add()
    assert seen == []
    assert settings.plugins.registries == []


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("http://guild.example/index.json", "https"),
        (DEFAULT_REGISTRY_URL, "built-in"),
    ],
)
def test_add_surfaces_the_hosts_rejection(
    qtbot, host, settings: Settings, monkeypatch, url: str, fragment: str
) -> None:
    warnings: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    _prompts(monkeypatch, (url, True), ("", True))
    _confirm(monkeypatch, True)
    make_widget(qtbot, host)._add()
    assert warnings, "no rejection was surfaced"
    assert fragment in warnings[0][2]
    assert settings.plugins.registries == []


def test_add_rejects_a_duplicate(qtbot, host, settings: Settings, monkeypatch) -> None:
    warnings: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    host.add_registry(GUILD, "Guild registry")
    _prompts(monkeypatch, (GUILD.upper().replace("HTTPS", "https"), True), ("Again", True))
    _confirm(monkeypatch, True)
    widget = make_widget(qtbot, host)
    widget._add()
    assert warnings and "already in the list" in warnings[0][2]
    assert len(settings.plugins.registries) == 1


# --- remove -----------------------------------------------------------------


def test_removing_a_user_registry_confirms_first(
    qtbot, host, settings: Settings, monkeypatch
) -> None:
    host.add_registry(GUILD, "Guild registry")
    asked: list[tuple] = []

    def question(*args, **kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    widget = make_widget(qtbot, host)
    widget._table.setCurrentCell(1, 1)
    assert widget._remove_button.isEnabled()
    widget._remove_selected()
    assert asked and GUILD in asked[0][2]
    assert settings.plugins.registries == []
    assert widget._table.rowCount() == 1


def test_declining_the_remove_confirm_keeps_the_registry(
    qtbot, host, settings: Settings, monkeypatch
) -> None:
    host.add_registry(GUILD, "Guild registry")
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    widget = make_widget(qtbot, host)
    widget._table.setCurrentCell(1, 1)
    widget._remove_selected()
    assert len(settings.plugins.registries) == 1


def test_unticking_a_user_registry_persists(qtbot, host, settings: Settings) -> None:
    host.add_registry(GUILD, "Guild registry")
    widget = make_widget(qtbot, host)
    widget._table.cellWidget(1, 0).setChecked(False)
    assert settings.plugins.registries[0].enabled is False
