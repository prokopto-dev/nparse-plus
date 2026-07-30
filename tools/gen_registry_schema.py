#!/usr/bin/env python
"""Generate the plugin-registry JSON Schema from the app's pydantic models.

Produces templates/registry-repo/schema/index-v1.schema.json — the schema the
curated registry repo (prokopto-dev/nparseplus-plugins) uses in CI to validate
incoming submissions. Generating it means the registry can check PRs with
nothing but `jsonschema` installed, while the app
(src/nparseplus/core/plugins/registry.py) stays the single source of truth for
the wire format.

`RegistryIndex.model_json_schema()` alone is NOT enough: the string
constraints live in `field_validator`s (https-only urls, 64-hex sha256, the
SDK's plugin-id regex), which pydantic cannot express in JSON Schema. This
script re-applies each of them from the same live regexes/validators, and
asserts every injection target still exists so a model rename fails loudly
here instead of silently loosening the registry's CI.

Usage:
    uv run python tools/gen_registry_schema.py [--check]

    --check exits non-zero if the committed file is stale (what
    tests/core/plugins/test_registry_schema.py asserts).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from nparseplus.core.plugins.registry import (
    _SHA256_RE,
    REGISTRY_SCHEMA_VERSION,
    RegistryIndex,
)
from nparseplus_sdk.plugin import PLUGIN_ID_RE

SOURCE_MODULE = "nparseplus.core.plugins.registry"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = (
    REPO_ROOT
    / "templates"
    / "registry-repo"
    / "schema"
    / f"index-v{REGISTRY_SCHEMA_VERSION}.schema.json"
)

SCHEMA_ID = (
    "https://prokopto-dev.github.io/nparseplus-plugins/"
    f"schema/index-v{REGISTRY_SCHEMA_VERSION}.schema.json"
)

# https-only, mirroring RegistryRelease._https_only. That validator lowercases
# nothing and compares case-insensitively, so allow the scheme in any case.
_HTTPS_PATTERN = "^[Hh][Tt][Tt][Pp][Ss]://"


def _require(container: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    """Fetch a nested schema node, failing loudly if the model moved it."""
    node = container.get(key)
    if not isinstance(node, dict):
        raise SystemExit(
            f"{SOURCE_MODULE} no longer produces {where}.{key} — this generator's "
            "constraint injection is stale; update tools/gen_registry_schema.py"
        )
    return node


def build_schema() -> dict[str, Any]:
    """The committed schema: pydantic's output plus the validator constraints."""
    schema = RegistryIndex.model_json_schema()

    defs = _require(schema, "$defs", "schema")
    index_props = _require(schema, "properties", "schema")
    plugin_props = _require(
        _require(defs, "RegistryPlugin", "$defs"), "properties", "RegistryPlugin"
    )
    release_props = _require(
        _require(defs, "RegistryRelease", "$defs"), "properties", "RegistryRelease"
    )

    # --- constraints pydantic cannot express (see module docstring) ---------
    # RegistryPlugin._valid_id
    _require(plugin_props, "id", "RegistryPlugin.properties")["pattern"] = PLUGIN_ID_RE.pattern
    # RegistryRelease._https_only
    _require(release_props, "url", "RegistryRelease.properties")["pattern"] = _HTTPS_PATTERN
    # RegistryRelease._hex_digest (the validator lowercases first, so the
    # schema is the stricter of the two: the index must store it lowercased).
    _require(release_props, "sha256", "RegistryRelease.properties")["pattern"] = _SHA256_RE.pattern
    # parse_index rejects a newer schema_version than the app understands.
    schema_version = _require(index_props, "schema_version", "schema.properties")
    schema_version["minimum"] = 1
    schema_version["maximum"] = REGISTRY_SCHEMA_VERSION

    # --- document the file itself ------------------------------------------
    header = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": f"nParse+ plugin registry index (schema {REGISTRY_SCHEMA_VERSION})",
        "description": (
            "GENERATED FILE — do not edit by hand. Produced by "
            "tools/gen_registry_schema.py in prokopto-dev/nparse-plus from the "
            f"pydantic models in {SOURCE_MODULE}, which are the source of truth "
            "for this wire format. Note that plugin-id uniqueness and ownership "
            "are NOT expressible here; the registry's validate-index workflow "
            "checks those in Python."
        ),
    }
    # Extra properties are tolerated, matching the client: its models do not
    # set extra='forbid', so an index written by a newer generation of the
    # schema still loads in older apps.
    return {**header, **schema}


def main() -> None:
    check_only = "--check" in sys.argv[1:]
    rendered = json.dumps(build_schema(), indent=2) + "\n"

    if check_only:
        if not OUTPUT_PATH.is_file():
            sys.exit(f"missing {OUTPUT_PATH} — run: uv run python tools/gen_registry_schema.py")
        if OUTPUT_PATH.read_text(encoding="utf-8") != rendered:
            sys.exit(
                f"{OUTPUT_PATH} is stale — regenerate with: "
                "uv run python tools/gen_registry_schema.py"
            )
        print(f"ok: {OUTPUT_PATH} is up to date")
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    print(f"schema_version: {REGISTRY_SCHEMA_VERSION}")
    print(f"plugin id pattern: {PLUGIN_ID_RE.pattern}")


if __name__ == "__main__":
    main()
