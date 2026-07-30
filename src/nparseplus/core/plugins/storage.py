"""Per-plugin persistent storage — separate JSON files, not settings.json.

Implements the SDK ``PluginStorage`` protocol. Each plugin gets
``config_dir()/plugin-data/<id>/storage.json`` written with the same atomic
tmp-write + rename recipe as ``save_settings``, keeping plugin data out of
the app's settings document (and out of its save path) entirely.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STORAGE_FILENAME = "storage.json"


class JsonPluginStorage:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)

    @property
    def data_dir(self) -> Path:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    @property
    def storage_path(self) -> Path:
        return self._data_dir / STORAGE_FILENAME

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("plugin storage unreadable, treating as empty: %s", self.storage_path)
            return {}
        return raw if isinstance(raw, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        path = self.storage_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, default=str)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)
