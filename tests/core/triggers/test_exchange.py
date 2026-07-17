"""Tests for the trigger export/import envelope (core/triggers/exchange.py)."""

import json

import pytest

from nparseplus.core.triggers.exchange import (
    EXPORT_FORMAT,
    dump_triggers,
    parse_triggers,
    sanitize_imported,
)
from nparseplus.core.triggers.model import (
    TimerType,
    Trigger,
    TriggerOutput,
    TriggerTimer,
)


def _sample_trigger(**overrides) -> Trigger:
    fields = dict(
        trigger_enabled=True,
        trigger_name="FTE Alert",
        category="Raids",
        search_text="(?<mob>[\\w` ]+) engages {c}",
        use_regex=True,
        basic=TriggerOutput(display_text_enabled=True, display_text="FTE: {mob}"),
        timer=TriggerTimer(timer_type=TimerType.COUNT_DOWN, seconds=30, show_in_overlay=True),
    )
    fields.update(overrides)
    return Trigger(**fields)


def test_envelope_roundtrip_through_json() -> None:
    triggers = [_sample_trigger(), _sample_trigger(trigger_name="Second", category="Misc")]
    envelope = dump_triggers(triggers)
    assert envelope["format"] == EXPORT_FORMAT
    assert envelope["version"] == 1
    restored = parse_triggers(json.loads(json.dumps(envelope)))
    assert [t.model_dump(mode="json") for t in restored] == [
        t.model_dump(mode="json") for t in triggers
    ]


def test_bare_list_accepted() -> None:
    payload = [_sample_trigger().model_dump(mode="json")]
    restored = parse_triggers(payload)
    assert len(restored) == 1
    assert restored[0].trigger_name == "FTE Alert"


def test_wrong_format_rejected() -> None:
    with pytest.raises(ValueError, match="format"):
        parse_triggers({"format": "somebody-elses-triggers", "triggers": []})


def test_missing_triggers_list_rejected() -> None:
    with pytest.raises(ValueError, match="triggers"):
        parse_triggers({"format": EXPORT_FORMAT, "version": 1})


def test_non_json_object_rejected() -> None:
    with pytest.raises(ValueError, match="JSON object or list"):
        parse_triggers("just a string")


def test_invalid_entry_reports_index() -> None:
    good = _sample_trigger().model_dump(mode="json")
    with pytest.raises(ValueError, match="trigger #2"):
        parse_triggers([good, {"use_regex": "not-a-bool"}])


def test_sanitize_mints_id_and_strips_builtin_markers() -> None:
    source = _sample_trigger(
        is_built_in=True,
        built_in_id="BUILTIN-1",
        customized=True,
        built_in_folder="Encounters",
        built_in_folder_path="Encounters/Sky",
        folder_id="exporter-folder",
        category="Default",
    )
    clean = sanitize_imported(source)
    assert clean.trigger_id != source.trigger_id
    assert clean.is_built_in is False
    assert clean.built_in_id is None
    assert clean.customized is False
    assert clean.built_in_folder == ""
    assert clean.built_in_folder_path == "Custom"
    assert clean.folder_id is None
    # A shared built-in keeps its library folder as the category.
    assert clean.category == "Encounters"
    # Payload untouched.
    assert clean.search_text == source.search_text
    assert clean.timer is not None and clean.timer.seconds == 30


def test_sanitize_preserves_user_category_and_defaults_blank() -> None:
    assert sanitize_imported(_sample_trigger(category="Raids")).category == "Raids"
    assert sanitize_imported(_sample_trigger(category="Default")).category == "Custom"
    assert sanitize_imported(_sample_trigger(category="")).category == "Custom"
