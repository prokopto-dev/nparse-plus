"""Built-in trigger library — load data/builtin_triggers.json and keep the
user's stored trigger list in sync with it.

``sync_builtin_triggers`` is a port of EQToolSettingsLoad.SyncBuiltInTriggers:

* A built-in the user hasn't edited (``customized`` false) has its definition
  refreshed from the library, keeping only the user's enabled state and id.
* A built-in the user HAS edited is left as-is (structural markers re-derived).
* A user trigger with no ``built_in_id`` that duplicates a built-in by name (or
  unambiguously by search text) is merged into the built-in, keeping the user's
  outputs/timers/enabled state — unless it was filed into a folder (an
  intentional copy) or the tracked built-in itself was customized.
* A newly shipped built-in is seeded enabled only when it lives in the
  top-level "Encounters" folder; everything else starts disabled.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib import resources
from pathlib import Path

from nparseplus.core.triggers.model import Trigger

EXPECTED_BUILTIN_COUNT = 65


def load_builtin_triggers(path: Path | None = None) -> list[Trigger]:
    """Load the built-in trigger definitions shipped in data/builtin_triggers.json."""
    if path is not None:
        raw = path.read_text(encoding="utf-8")
    else:
        raw = (resources.files("nparseplus") / "data" / "builtin_triggers.json").read_text(
            encoding="utf-8"
        )
    payload = json.loads(raw)
    return [Trigger.model_validate(entry) for entry in payload["triggers"]]


def sync_builtin_triggers(
    triggers: list[Trigger], definitions: Iterable[Trigger] | None = None
) -> tuple[list[Trigger], bool]:
    """Reconcile a stored trigger list with the built-in library.

    Returns ``(effective_triggers, changed)`` where ``changed`` mirrors the C#
    return value: the list changed in a way that should be persisted.
    """
    defs_source = list(definitions) if definitions is not None else load_builtin_triggers()

    # Fresh copies keyed by lowered id (first definition wins, like the C# GroupBy).
    defs: dict[str, Trigger] = {}
    for definition in defs_source:
        if definition.built_in_id and definition.built_in_id.lower() not in defs:
            defs[definition.built_in_id.lower()] = definition.model_copy(deep=True)

    # The entry (if any) already tracking each built-in id.
    tracked_by_id: dict[str, Trigger] = {}
    for trigger in triggers:
        key = (trigger.built_in_id or "").lower()
        if key and key in defs and key not in tracked_by_id:
            tracked_by_id[key] = trigger

    # Adopt orphaned duplicates (user triggers matching a built-in definition).
    adoptions: dict[int, Trigger] = {}
    claimed: set[str] = set()
    for trigger in triggers:
        if trigger.built_in_id:
            continue
        if trigger.folder_id is not None or (trigger.built_in_folder_path or "").strip():
            # Filed into a folder = an intentional copy, not a legacy duplicate.
            continue
        definition = _find_builtin_match(defs.values(), trigger)
        if definition is None or definition.built_in_id is None:
            continue
        key = definition.built_in_id.lower()
        if key in claimed:
            continue
        tracked = tracked_by_id.get(key)
        if tracked is not None and tracked.customized:
            # The user already edited the built-in itself; don't guess which copy wins.
            continue
        adoptions[id(trigger)] = _adopt_orphaned_builtin(definition, trigger, tracked)
        claimed.add(key)

    rebuilt: list[Trigger] = []
    present: set[str] = set()
    changed = bool(adoptions)
    for trigger in triggers:
        merged = adoptions.get(id(trigger))
        key = (trigger.built_in_id or "").lower()
        if merged is not None:
            present.add((merged.built_in_id or "").lower())
            rebuilt.append(merged)
        elif key and key in defs:
            if key in claimed:
                # Superseded by an adopted duplicate above; drop this copy.
                continue
            present.add(key)
            definition = defs[key]
            if trigger.customized:
                # Keep the user's edited definition; re-derive structural markers.
                trigger.is_built_in = True
                trigger.built_in_folder = definition.built_in_folder
                rebuilt.append(trigger)
            else:
                # Refresh the definition from the library, keeping enabled state + id.
                definition.trigger_enabled = trigger.trigger_enabled
                definition.trigger_id = trigger.trigger_id
                rebuilt.append(definition)
        else:
            # User trigger (or an orphaned built-in no longer defined in the library).
            rebuilt.append(trigger)

    for key, definition in defs.items():
        if key in present:
            continue
        # Newly shipped built-in: enabled the first time the user sees it only
        # when it lives in the top-level Encounters folder.
        definition.trigger_enabled = definition.built_in_folder == "Encounters"
        rebuilt.append(definition)
        changed = True

    return rebuilt, changed


def effective_triggers(stored: list[dict] | None) -> list[Trigger]:
    """Convenience: validate the user's stored trigger dicts and sync built-ins."""
    triggers = [Trigger.model_validate(entry) for entry in (stored or [])]
    rebuilt, _changed = sync_builtin_triggers(triggers)
    return rebuilt


def _find_builtin_match(definitions: Iterable[Trigger], trigger: Trigger) -> Trigger | None:
    """The built-in an untagged user trigger duplicates: an exact name match
    wins; a search-text match counts only when exactly one built-in uses it."""
    by_search: Trigger | None = None
    search_matches = 0
    for definition in definitions:
        if _text_equals(definition.trigger_name, trigger.trigger_name):
            return definition
        if _text_equals(definition.search_text, trigger.search_text):
            by_search = definition
            search_matches += 1
    return by_search if search_matches == 1 else None


def _text_equals(a: str | None, b: str | None) -> bool:
    if not (a and a.strip()) or not (b and b.strip()):
        return False
    return a.strip().lower() == b.strip().lower()


def _adopt_orphaned_builtin(
    definition: Trigger, orphan: Trigger, tracked: Trigger | None
) -> Trigger:
    """Merge an orphaned duplicate into its built-in definition: the library
    supplies the general section, the user's copy supplies its configuration."""
    definition.trigger_id = orphan.trigger_id
    definition.trigger_enabled = orphan.trigger_enabled or (
        tracked.trigger_enabled if tracked is not None else False
    )
    definition.customized = True
    definition.basic = orphan.basic
    definition.display_text_enabled = orphan.display_text_enabled
    definition.display_text = orphan.display_text
    definition.audio_text_enabled = orphan.audio_text_enabled
    definition.audio_text = orphan.audio_text
    definition.timer = orphan.timer
    definition.timer_ending = orphan.timer_ending
    definition.timer_ended = orphan.timer_ended
    definition.counter = orphan.counter
    return definition
