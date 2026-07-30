"""Guards for templates/registry-repo — the seed of the curated registry.

The registry repo validates submissions with a JSON Schema *generated* from
this app's pydantic models, so its CI never has to install nParse+. That only
holds if the committed schema tracks the models, which is what these tests
assert.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nparseplus.core.plugins.registry import REGISTRY_SCHEMA_VERSION, parse_index

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_REPO = REPO_ROOT / "templates" / "registry-repo"
SCHEMA_PATH = REGISTRY_REPO / "schema" / f"index-v{REGISTRY_SCHEMA_VERSION}.schema.json"
INDEX_PATH = REGISTRY_REPO / "index.json"
OWNERS_PATH = REGISTRY_REPO / "owners.json"


@pytest.fixture(scope="module")
def generator():
    """tools/gen_registry_schema.py, imported as a module."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import gen_registry_schema
    finally:
        sys.path.pop(0)
    return gen_registry_schema


def test_committed_schema_matches_the_models(generator) -> None:
    """A model change without regeneration fails here, not in the registry's CI."""
    expected = json.dumps(generator.build_schema(), indent=2) + "\n"
    actual = SCHEMA_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "templates/registry-repo/schema is stale — regenerate it with: "
        "uv run python tools/gen_registry_schema.py"
    )


def test_generator_writes_where_the_registry_repo_expects(generator) -> None:
    assert generator.OUTPUT_PATH == SCHEMA_PATH


def test_schema_carries_the_constraints_pydantic_cannot_express() -> None:
    """The field_validators ARE the registry's gate; losing them would be silent.

    pydantic cannot express them in JSON Schema, so the generator re-injects
    them. Exercised behaviourally: the patterns must accept what the client
    accepts and reject what it rejects.
    """
    import re

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    release = schema["$defs"]["RegistryRelease"]["properties"]
    plugin = schema["$defs"]["RegistryPlugin"]["properties"]

    plugin_id = re.compile(plugin["id"]["pattern"])
    assert plugin_id.match("merchant-prices")
    assert not plugin_id.match("Merchant-Prices")
    assert not plugin_id.match("1bad")

    url = re.compile(release["url"]["pattern"])
    assert url.match("https://example.com/x.zip")
    assert url.match("HTTPS://example.com/x.zip"), "the client compares case-insensitively"
    assert not url.match("http://example.com/x.zip")

    digest = re.compile(release["sha256"]["pattern"])
    assert digest.match("0" * 64)
    assert not digest.match("0" * 63)
    assert not digest.match("A" * 64), "the index must store the digest lowercased"

    assert schema["properties"]["schema_version"]["maximum"] == REGISTRY_SCHEMA_VERSION


def test_seed_index_parses_through_the_real_client() -> None:
    """The published index must load in the app, not merely satisfy the schema."""
    index = parse_index(INDEX_PATH.read_bytes())
    assert index.schema_version == REGISTRY_SCHEMA_VERSION
    assert index.plugins == []


def test_seed_index_validates_against_the_committed_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(INDEX_PATH.read_text(encoding="utf-8")), schema)


def test_seed_owners_file_has_the_documented_shape() -> None:
    owners = json.loads(OWNERS_PATH.read_text(encoding="utf-8"))
    assert owners["owners"] == {}, "the seed ownership record starts empty"
    # Documentation keys the registry workflow deliberately ignores.
    assert isinstance(owners["_readme"], list)
    assert all(isinstance(handles, list) for handles in owners["_example"].values())


def test_registry_repo_is_complete() -> None:
    for relative in (
        "index.json",
        "owners.json",
        "README.md",
        "CONTRIBUTING.md",
        "SETUP.md",
        f"schema/index-v{REGISTRY_SCHEMA_VERSION}.schema.json",
        ".github/workflows/validate-index.yml",
    ):
        assert (REGISTRY_REPO / relative).is_file(), f"registry repo file missing: {relative}"


def test_setup_instructions_match_default_registry_url() -> None:
    """SETUP.md's Pages configuration has to produce DEFAULT_REGISTRY_URL.

    Repo name, publishing branch and root path are all pinned by that URL; if
    someone edits one side, this catches the drift.
    """
    from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

    assert DEFAULT_REGISTRY_URL == "https://prokopto-dev.github.io/nparseplus-plugins/index.json"
    setup = (REGISTRY_REPO / "SETUP.md").read_text(encoding="utf-8")
    assert DEFAULT_REGISTRY_URL in setup
    assert "prokopto-dev/nparseplus-plugins" in setup
    assert "source[branch]=main" in setup
    assert "source[path]=/" in setup
