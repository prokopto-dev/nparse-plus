"""Shared fixtures for plugin-host tests: a real backend + a tmp plugins dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import Backend, build_backend
from nparseplus.config.settings import PluginEntry, Settings
from nparseplus.core.plugins.host import PluginHost

APP_VERSION = "1.15.0"

# A minimal well-formed plugin; tests .format() the pieces they vary.
PLUGIN_TEMPLATE = """
from nparseplus_sdk import NParsePlugin, PluginMeta


class _Plugin(NParsePlugin):
    meta = PluginMeta(id={plugin_id!r}, name={name!r}, version={version!r}{extra_meta})

    def activate(self, ctx):
{activate_body}

    def deactivate(self):
{deactivate_body}


def create_plugin():
    return _Plugin()
"""


def write_plugin(
    plugins_dir: Path,
    filename: str,
    *,
    plugin_id: str,
    name: str | None = None,
    version: str = "1.0.0",
    extra_meta: str = "",
    activate_body: str = "        pass",
    deactivate_body: str = "        pass",
) -> Path:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    path = plugins_dir / filename
    path.write_text(
        PLUGIN_TEMPLATE.format(
            plugin_id=plugin_id,
            name=name or plugin_id.title(),
            version=version,
            extra_meta=extra_meta,
            activate_body=activate_body,
            deactivate_body=deactivate_body,
        ),
        encoding="utf-8",
    )
    return path


def approve(settings: Settings, plugin_id: str, *, enabled: bool = True) -> None:
    settings.plugins.entries[plugin_id] = PluginEntry(enabled=enabled, approved=True)


@pytest.fixture
def settings() -> Settings:
    s = Settings()
    s.sharing.mode = "off"
    return s


@pytest.fixture
def backend(settings: Settings) -> Backend:
    return build_backend(settings, speaker=NullSpeaker())


@pytest.fixture
def plugins_dir(tmp_path: Path) -> Path:
    return tmp_path / "plugins"


@pytest.fixture
def make_host(settings: Settings, backend: Backend, plugins_dir: Path):
    def _make(**kwargs) -> PluginHost:
        kwargs.setdefault("request_save", lambda: None)
        return PluginHost(
            settings, backend, APP_VERSION, plugins_dir_override=plugins_dir, **kwargs
        )

    return _make
