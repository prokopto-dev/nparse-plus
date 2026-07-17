"""Trigger export/import — the shareable "trigger pack" file format.

An export is a JSON envelope so future revisions can evolve without breaking
old importers (``Trigger``'s ``extra="ignore"`` absorbs unknown fields going
the other way)::

    {
      "format": "nparseplus-triggers",
      "version": 1,
      "exported_at": "2026-07-17T12:00:00",
      "triggers": [ {...}, ... ]
    }

``parse_triggers`` also accepts a bare JSON list of trigger objects, so packs
can be hand-assembled without the envelope.

Imported triggers pass through ``sanitize_imported`` before entering the
user's list: fresh id, built-in markers stripped (a shared copy of a built-in
must never be merged back into the library by ``sync_builtin_triggers``), and
foreign folder references dropped.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from nparseplus.core.triggers.model import Trigger

EXPORT_FORMAT = "nparseplus-triggers"
EXPORT_VERSION = 1


def dump_triggers(triggers: list[Trigger]) -> dict:
    """Build the export envelope for ``triggers`` (JSON-serializable dict)."""
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        # Naive local time, matching every other timestamp in the app.
        "exported_at": datetime.now().replace(microsecond=0).isoformat(),
        "triggers": [t.model_dump(mode="json") for t in triggers],
    }


def parse_triggers(data: object) -> list[Trigger]:
    """Parse an export envelope (or bare trigger list) into ``Trigger`` objects.

    Raises ``ValueError`` on anything that isn't a trigger file.
    """
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        declared = data.get("format")
        if declared is not None and declared != EXPORT_FORMAT:
            raise ValueError(f"not a trigger export (format is {declared!r})")
        entries = data.get("triggers")
        if not isinstance(entries, list):
            raise ValueError('not a trigger export (no "triggers" list)')
    else:
        raise ValueError("not a trigger export (expected a JSON object or list)")

    triggers: list[Trigger] = []
    for index, entry in enumerate(entries):
        try:
            triggers.append(Trigger.model_validate(entry))
        except Exception as exc:
            raise ValueError(f"trigger #{index + 1} is invalid: {exc}") from exc
    return triggers


def sanitize_imported(trigger: Trigger) -> Trigger:
    """Return a copy of ``trigger`` safe to add to the user's list.

    Mirrors the editor's Duplicate action: new id and no built-in markers, so
    the copy can be edited/deleted freely and ``sync_builtin_triggers`` never
    adopts it back into the built-in library. The category survives so a
    shared pack keeps its folder grouping (a shared built-in falls back to
    its library folder name).
    """
    copy = trigger.model_copy(deep=True)
    copy.trigger_id = str(uuid.uuid4())
    category = (copy.category or "").strip()
    if copy.is_built_in and copy.built_in_folder:
        category = copy.built_in_folder
    if not category or category == "Default":
        category = "Custom"
    copy.category = category
    copy.is_built_in = False
    copy.built_in_id = None
    copy.customized = False
    copy.built_in_folder = ""
    # Marks an intentional copy for sync_builtin_triggers (same as Duplicate).
    copy.built_in_folder_path = "Custom"
    # Folder ids reference the exporter's folders, meaningless here.
    copy.folder_id = None
    return copy
