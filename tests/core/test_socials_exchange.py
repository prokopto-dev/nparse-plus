"""core.socials_exchange — the nparseplus-socials macro pack format."""

import json
from datetime import datetime

import pytest

from nparseplus.core.socials import Social
from nparseplus.core.socials_exchange import (
    EXPORT_FORMAT,
    EXPORT_VERSION,
    dump_socials,
    pack_label,
    parse_socials,
    sanitize_all,
    sanitize_imported,
)


def _socials() -> list[Social]:
    return [
        Social(page=1, button=1, name="Assist", color=13, lines=["/assist"]),
        Social(page=2, button=3, name="Sit", color=4, lines=["/sit"]),
    ]


def test_dump_socials_round_trips_through_json() -> None:
    payload = json.loads(json.dumps(dump_socials(_socials(), label="Xantik (P1999Green)")))
    assert payload["format"] == EXPORT_FORMAT
    assert payload["version"] == EXPORT_VERSION
    assert payload["label"] == "Xantik (P1999Green)"
    # Naive local time — parseable, and carrying no timezone.
    stamp = datetime.fromisoformat(payload["exported_at"])
    assert stamp.tzinfo is None

    parsed = parse_socials(payload)
    assert [(s.slot, s.name, s.lines) for s in parsed] == [
        ((1, 1), "Assist", ["/assist"]),
        ((2, 3), "Sit", ["/sit"]),
    ]


def test_parse_socials_accepts_a_bare_list() -> None:
    entries = [s.model_dump(mode="json") for s in _socials()]
    assert [s.slot for s in parse_socials(entries)] == [(1, 1), (2, 3)]


def test_parse_socials_rejects_a_trigger_pack() -> None:
    with pytest.raises(ValueError, match="nparseplus-triggers"):
        parse_socials({"format": "nparseplus-triggers", "triggers": []})


def test_parse_socials_rejects_an_envelope_without_socials() -> None:
    with pytest.raises(ValueError, match='no "socials" list'):
        parse_socials({"format": EXPORT_FORMAT})


def test_parse_socials_rejects_a_non_object() -> None:
    with pytest.raises(ValueError, match="JSON object or list"):
        parse_socials("nope")


def test_parse_socials_names_the_offending_entry_one_based() -> None:
    good = _socials()[0].model_dump(mode="json")
    with pytest.raises(ValueError, match="macro #2 is invalid"):
        parse_socials([good, {"page": "not-a-number", "button": 1}])


def test_pack_label_is_optional() -> None:
    assert pack_label(dump_socials(_socials(), label="  Xantik  ")) == "Xantik"
    assert pack_label(dump_socials(_socials())) == ""
    assert pack_label([1, 2, 3]) == ""


def test_label_never_reaches_a_social() -> None:
    payload = dump_socials(_socials(), label="Xantik (P1999Green)")
    for social in parse_socials(payload):
        assert not hasattr(social, "label")
        assert "label" not in social.model_dump()


def test_sanitize_imported_trims_clamps_and_keeps_placement() -> None:
    messy = Social(page=3, button=4, name="  Pull  ", color=9999, lines=[" /shout ", "", ""])
    cleaned = sanitize_imported(messy)
    assert cleaned.name == "Pull"
    assert cleaned.lines == ["/shout"]
    assert cleaned.color == 255
    # Placement survives — where it lands is the placement step's call.
    assert cleaned.slot == (3, 4)


def test_sanitize_imported_drops_unknown_fields() -> None:
    social = Social.model_validate(
        {"page": 1, "button": 1, "name": "X", "lines": ["/x"], "origin": "local", "id": 7}
    )
    dumped = sanitize_imported(social).model_dump()
    assert "origin" not in dumped and "id" not in dumped


def test_sanitize_all_drops_empty_macros() -> None:
    socials = [
        Social(page=1, button=1, name="Keep", lines=["/keep"]),
        Social(page=1, button=2, name="   ", lines=["  "]),
    ]
    assert [s.name for s in sanitize_all(socials)] == ["Keep"]
