"""Release-workflow invariants that a broken release day would otherwise teach us.

Packaging runs only on tags (``ci.yml`` is tests-only), so a mistake in
``release.yml`` first shows up while cutting a release — after
semantic-release has already tagged. These assert the wiring that would fail
silently: an artifact nobody publishes, a job the publish does not wait for,
and the asset-naming rule the updater depends on.

Parsed, not regexed: the graph (``needs``, per-job ``container``, which steps
upload what) is what matters, and text matching over YAML gets it wrong in
both directions. PyYAML is in the ``dev`` group for this.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from nparseplus.updater import ReleaseAsset, ReleaseInfo, pick_asset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging" / "deb"))

import build_deb

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/release.yml"

# The job that publishes the GitHub release. Everything a user downloads has
# to reach it.
PUBLISHER = "release"


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def jobs() -> dict[str, Any]:
    return workflow()["jobs"]


def steps(job: str) -> list[dict[str, Any]]:
    return jobs()[job].get("steps", [])


def uploads(job: str) -> list[dict[str, Any]]:
    """The ``with:`` blocks of every upload-artifact step in one job."""
    return [
        step.get("with", {})
        for step in steps(job)
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]


def uploading_jobs() -> dict[str, list[dict[str, Any]]]:
    return {name: found for name in jobs() if (found := uploads(name))}


def run_script(job: str) -> str:
    """Every ``run:`` body in one job, comments stripped.

    Comments may discuss a command the job must not call — the Debian job's
    own comment explains why it uses no ``sudo``.
    """
    lines: list[str] = []
    for step in steps(job):
        for line in str(step.get("run", "")).splitlines():
            if not line.lstrip().startswith("#"):
                lines.append(line)
    return "\n".join(lines)


# --- the publish graph ------------------------------------------------------


def test_every_artifact_producing_job_is_waited_for() -> None:
    """The generalisation of "did you add the new job to ``needs``".

    ``download-artifact`` with ``merge-multiple`` takes whatever exists when
    it runs and ``files: artifacts/*`` globs whatever it found — so a producer
    missing from ``needs`` does not error, it just races and sometimes ships
    nothing. Deriving the producer list from the file means a future artifact
    cannot be forgotten the same way.
    """
    waited = set(jobs()[PUBLISHER]["needs"])
    producers = set(uploading_jobs())
    assert producers <= waited, f"not waited for: {sorted(producers - waited)}"


def test_artifact_names_are_unique_across_jobs() -> None:
    """``merge-multiple: true`` flattens every artifact into one directory.

    Two uploads sharing a name collide there, not at upload time.
    """
    names = [with_block["name"] for found in uploading_jobs().values() for with_block in found]
    assert len(names) == len(set(names)), f"duplicate artifact names: {names}"


def test_the_debian_package_and_the_tarball_ship_from_different_jobs() -> None:
    """The whole point: the .deb's glibc floor comes from its own build host.

    If these ever collapsed into one job, the .deb would be built on the same
    runner as the tarball and would carry the same floor — the exact thing it
    exists to avoid, and nothing else in CI would notice.
    """
    producers = uploading_jobs()
    assert "build-linux" in producers and "build-linux-debian12" in producers
    tarball_paths = " ".join(w.get("path", "") for w in producers["build-linux"])
    deb_paths = " ".join(w.get("path", "") for w in producers["build-linux-debian12"])
    assert ".tar.gz" in tarball_paths and ".flatpak" in tarball_paths
    assert ".deb" in deb_paths
    assert ".deb" not in tarball_paths


# --- the container IS the feature -------------------------------------------


def test_the_debian_job_builds_in_a_bookworm_container() -> None:
    """Dropping back to a bare runner rebuilds the artifact this replaces.

    glibc is the one thing PyInstaller cannot bundle, so the build host is
    what decides the floor. ``ubuntu-latest`` here would silently produce a
    second copy of the tarball under a Debian name.
    """
    job = jobs()["build-linux-debian12"]
    assert job["container"] == "debian:12"
    assert jobs()["verify-deb-debian12"]["container"] == "debian:12"


def test_the_container_jobs_never_call_sudo() -> None:
    """A container job runs as root and the base image has no sudo installed."""
    for name in ("build-linux-debian12", "verify-deb-debian12"):
        assert "sudo " not in run_script(name), f"{name} calls sudo"


def test_the_debian_job_measures_the_glibc_floor_and_feeds_it_to_the_package() -> None:
    """The floor is a measurement, not a claim.

    If the objdump step or the hand-off to build_deb.py went away, the package
    would declare ``control.in``'s fallback regardless of what it actually
    links against — i.e. it would go on claiming 2.36 while requiring more,
    which is the original bug wearing a Depends field.
    """
    script = run_script("build-linux-debian12")
    assert "objdump" in script
    assert "--glibc-floor" in script
    assert "packaging/deb/build_deb.py" in script


def test_the_package_is_verified_on_a_clean_container() -> None:
    """``Depends:`` cannot be validated where the build ran.

    That container has every library installed by hand, so a missing
    dependency cannot fail there. ``apt-get install ./file.deb`` on a pristine
    image resolves the declared dependencies from the archive; ``dpkg -i``
    would not, and would pass while broken.
    """
    verify = jobs()["verify-deb-debian12"]
    assert verify["needs"] == "build-linux-debian12" or "build-linux-debian12" in verify["needs"]
    script = run_script("verify-deb-debian12")
    assert "apt-get install -y ./nparseplus_*.deb" in script
    assert "dpkg -i" not in script


# --- the asset-naming rule ---------------------------------------------------


def test_no_two_release_assets_share_the_updaters_linux_predicate() -> None:
    """The rule every future Linux artifact has to obey.

    ``updater.pick_asset`` sweeps for ``"-linux" in name`` plus a suffix and
    takes the first match — and that predicate ships compiled into every
    already-released binary, so it cannot be fixed retroactively for anyone.
    At most one release asset may both contain ``-linux`` and end in
    ``.tar.gz``. This is what #160 was, one artifact over.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    tarballs = set(re.findall(r'"?(dist/[\w.$@{}#-]*\.tar\.gz)"?', text))
    ambiguous = [name for name in tarballs if "-linux" in name]
    assert len(ambiguous) <= 1, f"two Linux tarballs share the updater's predicate: {ambiguous}"


# --- the workflow's names and the updater's expectations, tied together -----


def linux_asset_names(version: str) -> list[str]:
    """The Linux assets this workflow actually publishes, as filenames.

    Scoped to Linux on purpose: macOS and Windows build their names from
    matrix values and shell expansions that are not worth reconstructing, and
    the Debian artifact is the change this file exists for. The ``.deb`` name
    comes from ``build_deb`` rather than the workflow, because that is where
    it is decided.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    found = {
        match.removeprefix("dist/").replace("$VERSION", version)
        for match in re.findall(r"dist/nparseplus-\$VERSION-linux[^\"' ]*", text)
    }
    return sorted(found | {build_deb.deb_filename(version)})


def test_the_published_linux_names_are_the_ones_the_updater_resolves() -> None:
    """The end-to-end tie: rename an artifact in CI and this fails.

    Every already-released binary carries its own ``pick_asset``, so a rename
    here breaks updates for everyone already installed — and it would break
    them silently, since a release that publishes nothing matching just
    degrades to "open the release page". Nothing else in the repo connects
    what CI names to what the app looks for.
    """
    names = linux_asset_names("9.9.9")
    release = ReleaseInfo(
        version="9.9.9",
        html_url="https://example/release",
        assets=tuple(
            ReleaseAsset(name=name, browser_download_url=f"https://dl.test/{name}")
            for name in names
        ),
    )
    tarball = pick_asset(release, "linux", in_flatpak=False)
    flatpak = pick_asset(release, "linux", in_flatpak=True)
    assert tarball is not None, f"no tarball the updater can find in {names}"
    assert flatpak is not None, f"no flatpak the updater can find in {names}"
    assert tarball.name.endswith(".tar.gz")
    assert flatpak.name.endswith(".flatpak")
    # And the .deb is published without being reachable by either — #163.
    assert any(name.endswith(".deb") for name in names)
    assert not tarball.name.endswith(".deb") and not flatpak.name.endswith(".deb")
