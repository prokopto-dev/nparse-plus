"""The app's SDK dependency floor must admit only SDKs it can honour.

The host bundles exactly one SDK and reports whatever is *installed* through
``SDK_VERSION``, which is what ``check_compat`` weighs a plugin's
``requires_sdk`` against. So an app that implements the 1.2 contract but
declares ``nparseplus-sdk>=1.1`` can be pip-installed against 1.1 and will
then advertise 1.1 — refusing plugins it is perfectly capable of running, and
missing the SDK-side half of the contract entirely (``nparseplus_sdk.eqfiles``
does not exist in 1.1).

The uv workspace hides this: `nparseplus-sdk = { workspace = true }` always
resolves the in-tree SDK regardless of the floor. Only a plain pip/source
install sees it, which is why it needs a test rather than a habit.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

import nparseplus_sdk

ROOT_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _sdk_requirement() -> Requirement:
    data = tomllib.loads(ROOT_PYPROJECT.read_text(encoding="utf-8"))
    for raw in data["project"]["dependencies"]:
        requirement = Requirement(raw)
        if requirement.name == "nparseplus-sdk":
            return requirement
    raise AssertionError("the app must declare a nparseplus-sdk dependency")


def test_floor_admits_the_sdk_this_host_is_built_against() -> None:
    spec = _sdk_requirement().specifier
    assert spec.contains(Version(nparseplus_sdk.__version__), prereleases=True)


def test_floor_excludes_every_sdk_older_than_the_bundled_minor() -> None:
    """The point of the floor: bumping the SDK's minor without bumping this is
    the drift that lets an install advertise a contract it does not implement."""
    current = Version(nparseplus_sdk.__version__)
    spec = _sdk_requirement().specifier

    previous_minor = f"{current.major}.{max(current.minor - 1, 0)}.0"

    if current.minor == 0:  # pragma: no cover - only on a major bump
        return
    assert not spec.contains(Version(previous_minor), prereleases=True), (
        f"pyproject allows nparseplus-sdk {previous_minor}, but this host is built "
        f"against {current}. Raise the floor to >={current.major}.{current.minor},"
        f"<{current.major + 1} — a pip install resolving the older SDK would report "
        "it through SDK_VERSION and refuse plugins this host can run."
    )


def test_floor_is_capped_below_the_next_major() -> None:
    """The SDK's promise is additive within a major; the next one may break it."""
    current = Version(nparseplus_sdk.__version__)
    spec = _sdk_requirement().specifier

    assert not spec.contains(Version(f"{current.major + 1}.0.0"), prereleases=True)


def test_the_workspace_is_what_hides_this_locally() -> None:
    """Documents why the test exists: the source declares the SDK as a workspace
    member, so local resolution never consults the floor at all."""
    data = tomllib.loads(ROOT_PYPROJECT.read_text(encoding="utf-8"))
    sources = data["tool"]["uv"]["sources"]

    assert sources["nparseplus-sdk"] == {"workspace": True}


def test_declared_floor_matches_the_sdk_minor_exactly() -> None:
    """Belt and braces on the two above: the floor is not merely permissive
    enough, it names the bundled minor, so the shipped contract is the same
    whichever way the app was installed."""
    current = Version(nparseplus_sdk.__version__)
    expected = SpecifierSet(f">={current.major}.{current.minor},<{current.major + 1}")

    assert _sdk_requirement().specifier == expected
