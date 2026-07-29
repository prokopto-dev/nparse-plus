"""Macro pack export/import — the shareable socials file format.

Same envelope shape as the trigger packs in
:mod:`nparseplus.core.triggers.exchange`, so future revisions can evolve
without breaking old importers (``Social``'s ``extra="ignore"`` absorbs
unknown fields going the other way)::

    {
      "format": "nparseplus-socials",
      "version": 1,
      "exported_at": "2026-07-29T12:00:00",
      "label": "Xantik (P1999Green)",
      "socials": [ {...}, ... ]
    }

``parse_socials`` also accepts a bare JSON list, so packs can be
hand-assembled without the envelope.

``label`` records where a pack came from, for the importer to show. It is
the *only* exporter-local identity a pack carries, and it never reaches a
:class:`Social` — which is why :func:`sanitize_imported` is so much thinner
than the trigger equivalent. Grid position deliberately survives: it is the
data the placement step decides how to honour, not something to strip.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from nparseplus.core.socials import MAX_COLOR, Social, normalize_socials

EXPORT_FORMAT = "nparseplus-socials"
EXPORT_VERSION = 1


def dump_socials(socials: Sequence[Social], *, label: str = "") -> dict:
    """Build the export envelope for ``socials`` (JSON-serializable dict)."""
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        # Naive local time, matching every other timestamp in the app.
        "exported_at": datetime.now().replace(microsecond=0).isoformat(),
        "label": label,
        "socials": [s.model_dump(mode="json") for s in normalize_socials(socials)],
    }


def parse_socials(data: object) -> list[Social]:
    """Parse a macro pack (or bare social list) into ``Social`` objects.

    Raises ``ValueError`` on anything that isn't a macro pack.
    """
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        declared = data.get("format")
        if declared is not None and declared != EXPORT_FORMAT:
            raise ValueError(f"not a socials export (format is {declared!r})")
        entries = data.get("socials")
        if not isinstance(entries, list):
            raise ValueError('not a socials export (no "socials" list)')
    else:
        raise ValueError("not a socials export (expected a JSON object or list)")

    socials: list[Social] = []
    for index, entry in enumerate(entries):
        try:
            socials.append(Social.model_validate(entry))
        except Exception as exc:
            raise ValueError(f"macro #{index + 1} is invalid: {exc}") from exc
    return socials


def pack_label(data: object) -> str:
    """The pack's provenance label, if it declared one."""
    if isinstance(data, dict):
        label = data.get("label")
        if isinstance(label, str):
            return label.strip()
    return ""


def sanitize_imported(social: Social) -> Social:
    """Return a copy of ``social`` safe to merge into a character's grid.

    A macro carries no id, folder, or built-in marker, so this only has to
    re-validate the shape: trim, drop trailing blank lines, and clamp the
    colour to a value the client will accept. Page/button are left alone —
    placement is the caller's decision, not a sanitization concern.
    """
    cleaned = normalize_socials([social])
    if cleaned:
        return cleaned[0]
    # An entirely empty macro normalizes away; hand back a well-formed blank
    # so callers can report it rather than silently losing a list entry.
    return Social(
        page=social.page,
        button=social.button,
        name="",
        color=max(0, min(MAX_COLOR, social.color)),
        lines=[],
    )


def sanitize_all(socials: Iterable[Social]) -> list[Social]:
    """``sanitize_imported`` over a pack, dropping the empties."""
    return [s for s in (sanitize_imported(social) for social in socials) if not s.is_empty]
