"""Pins the "migration-preserved; not read at runtime" comments (#71).

``Settings.maps`` and the legacy half of ``SpellWindowSettings`` persist,
validate and round-trip through settings.json, and nothing reads them. That
is worse than needing a restart — there is no restart that helps — so the
models say so in a comment, and this asserts the comment is still true.

It fails in both directions on purpose: add a reader and the comment is a
lie, so the comment must go; delete the field and the pin must be updated
with it. What it cannot see is a reader that copies the model into a local
under some other name first — this is a source scan, not a proof.
"""

from __future__ import annotations

import ast
from pathlib import Path

from nparseplus.config.settings import MapSettings, SpellWindowSettings

SRC = Path(__file__).resolve().parents[2] / "src" / "nparseplus"

#: The legacy nparse fields on SpellWindowSettings. Everything else on that
#: model is live (the Spell Timers page and the handlers read it).
LEGACY_SPELLWINDOW_FIELDS = frozenset(
    {
        "casting_window_buffer",
        "delay_self_buffs_on_zone",
        "level",
        "sound_enabled",
        "sound_file",
        "use_casting_window",
        "use_item_triggers",
        "use_custom_triggers",
        "use_secondary",
        "use_secondary_all",
    }
)


def _runtime_modules() -> list[Path]:
    """Every module except ``config/`` — migration and the models themselves
    are the readers the comments allow."""
    return [path for path in sorted(SRC.rglob("*.py")) if path.parent.name != "config"]


def _receiver(node: ast.Attribute) -> str:
    """The name the attribute was read off: ``a.b.c`` -> "b" for ``.c``."""
    value = node.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return ""


def _spellwindow_holders(tree: ast.AST) -> set[str]:
    """Names in this module that hold a ``SpellWindowSettings``.

    Three shapes cover the codebase: the canonical ``settings.spellwindow``
    path, a parameter annotated with the model (composition passes it to
    three handlers), and whatever attribute such a parameter is stored under.
    """
    holders = {"spellwindow"}
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            annotation = ast.unparse(node.annotation)
            if "SpellWindowSettings" in annotation:
                holders.add(node.arg)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        sources = {child.id for child in ast.walk(node.value) if isinstance(child, ast.Name)} | {
            child.func.id
            for child in ast.walk(node.value)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        if not sources & holders:
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                holders.add(target.attr)
            elif isinstance(target, ast.Name):
                holders.add(target.id)
    return holders


def test_the_pinned_field_names_still_exist() -> None:
    """If a field is renamed or dropped, this list has to move with it."""
    assert LEGACY_SPELLWINDOW_FIELDS.issubset(SpellWindowSettings.model_fields)


def test_nothing_outside_config_reaches_settings_maps() -> None:
    """MapSettings is only reachable through ``settings.maps`` or by being
    passed as one — assert neither happens, which together is the claim."""
    readers = []
    for path in _runtime_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "maps"
                and _receiver(node) in {"settings", "_settings"}
            ):
                readers.append(f"{path.name}:{node.lineno}")
    assert readers == [], f"MapSettings is read at {readers} — the #71 comment is stale"


def test_map_settings_is_not_passed_around_either() -> None:
    holders = [
        path.name
        for path in _runtime_modules()
        if "MapSettings" in path.read_text()  # an annotation or a construction
    ]
    assert holders == [], f"MapSettings reaches {holders}; it is documented as unread"


def test_nothing_reads_the_legacy_spellwindow_fields() -> None:
    readers = []
    for path in _runtime_modules():
        tree = ast.parse(path.read_text())
        holders = _spellwindow_holders(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in LEGACY_SPELLWINDOW_FIELDS
                and _receiver(node) in holders
            ):
                readers.append(f"{path.name}:{node.lineno} {_receiver(node)}.{node.attr}")
    assert readers == [], f"legacy spell-window fields are read at {readers}"


def test_the_scan_would_notice_a_reader() -> None:
    """The scan's own negative control: it must flag both shapes it claims to
    cover, or the two tests above pass by construction."""
    tree = ast.parse(
        "def f(settings, spell_settings: SpellWindowSettings):\n"
        "    self.spells = spell_settings\n"
        "    return settings.maps.scale, self.spells.level\n"
    )
    holders = _spellwindow_holders(tree)
    assert "spells" in holders  # followed through the attribute assignment
    hits = {
        f"{_receiver(node)}.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and (
            (node.attr == "maps" and _receiver(node) in {"settings", "_settings"})
            or (node.attr in LEGACY_SPELLWINDOW_FIELDS and _receiver(node) in holders)
        )
    }
    assert hits == {"settings.maps", "spells.level"}


def test_the_maps_model_still_carries_its_migrated_values() -> None:
    """Unread is not the same as unused: the values are kept for the maps
    rebuild, so the model must keep round-tripping them."""
    restored = MapSettings.model_validate(MapSettings(scale=0.25, last_zone="qeynos").model_dump())
    assert restored.scale == 0.25
    assert restored.last_zone == "qeynos"
