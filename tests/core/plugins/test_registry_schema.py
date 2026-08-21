"""Guards for templates/registry-repo — the schema and the seed catalogue.

The registry server checks the index it renders against a JSON Schema
*generated* from this app's pydantic models, so it does not have to install
nParse+ to know what the client will accept. That only holds if the committed
schema tracks the models, which is what these tests assert. The drift guard
is the whole reason this directory outlived the pull-request submission
route it was scaffolded for (#147); everything documenting that route is
gone, and the two files left are the ones with a test behind them.

They also pin the other thing a client cannot discover for itself: the URL of
the built-in catalogue, and that the documents telling a human where it lives
say the same thing the constant does.
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
    """A model change without regeneration fails here, not in the server's gate."""
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


def test_registry_repo_holds_the_drift_guard_and_nothing_that_documents_a_pr() -> None:
    """Exactly two files, and the absent ones are the point.

    Ownership, submission and the Pages runbook moved to the registry server
    (#147): ids are database rows, publishing is an authenticated POST, and
    a document here describing a pull request would be a false trust claim
    with nothing testing it. What is left is the generated schema — the
    server vendors it verbatim — and the seed catalogue the client parses.
    """
    present = sorted(
        path.relative_to(REGISTRY_REPO).as_posix()
        for path in REGISTRY_REPO.rglob("*")
        if path.is_file()
    )
    assert present == [
        "index.json",
        f"schema/index-v{REGISTRY_SCHEMA_VERSION}.schema.json",
    ]


def test_the_default_registry_url_is_a_fetchable_index_url() -> None:
    """The shape the whole client depends on, whoever is serving it.

    Everything downstream — ``fetch_index``'s https gate, the installer's
    hop-by-hop re-assertion, ``normalize_registry_url`` in settings — assumes
    an https URL naming an index document. Pinned by shape rather than by
    literal on purpose: this guards the contract, not the choice of host, so
    moving the catalogue (#130 moved it off GitHub Pages to the registry
    server) is a one-line change and not a test rewrite.
    """
    from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

    assert DEFAULT_REGISTRY_URL.startswith("https://")
    assert DEFAULT_REGISTRY_URL.endswith("/index.json")


def test_the_url_has_exactly_one_definition() -> None:
    """settings owns the literal; registry.py is where the app reads it.

    The provenance migration in ``PluginsSettings`` has to compare against the
    same URL ``resolve_registries`` synthesizes the built-in row from, and it
    cannot import this subsystem to get it (that import gate is what keeps a
    plugins-off launch from touching the SDK). Two literals would let the two
    halves of the same move disagree, which is precisely the bug #130 fixed.
    """
    from nparseplus.config.settings import BUILTIN_REGISTRY_URL
    from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

    assert DEFAULT_REGISTRY_URL == BUILTIN_REGISTRY_URL


@pytest.mark.parametrize(
    "document",
    [
        REPO_ROOT / "docs" / "plugins" / "registry.md",
        REPO_ROOT / "docs" / "plugins" / "security.md",
    ],
    ids=["registry-spec", "security-docs"],
)
def test_the_documents_that_name_the_registry_url_track_the_constant(document: Path) -> None:
    """Where the built-in catalogue lives is documented, not folklore.

    The URL is the one thing a user cannot change and the app cannot
    discover: it is compiled in. So the specification and the trust argument
    both have to name whatever the constant currently says, or a reader
    checking where their plugins come from is checking the wrong host. (The
    maintainer runbook used to be the second document here; it described
    standing up GitHub Pages and went with the rest of the submission
    scaffolding in #147.) This is the hardest gate on the URL — refocus it,
    never delete it.
    """
    from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

    text = document.read_text(encoding="utf-8")
    assert DEFAULT_REGISTRY_URL in text, f"{document.name} does not name the built-in registry URL"
