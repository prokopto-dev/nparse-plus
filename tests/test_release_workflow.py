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
import tomllib
from pathlib import Path
from typing import Any

import yaml
from packaging.version import Version

from nparseplus.updater import ReleaseAsset, ReleaseInfo, pick_asset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging" / "deb"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

import appversion
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


def test_the_debian_job_checks_the_bundle_before_packaging_it() -> None:
    """Without this the .deb ships whatever the container happened to resolve.

    The first Debian release build lost QtWebEngine this way and produced a
    perfectly healthy-looking artifact. The check is what turns that into a
    failed build instead of a broken Discord overlay.
    """
    closure = next(
        step for step in steps("build-linux-debian12") if "check_bundle.py" in str(step.get("run"))
    )
    body = str(closure["run"])
    # It must not be piped. This job's shell is `sh -e` (dash), which has no
    # pipefail, so a pipeline reports the LAST command's status — piping the
    # check into `tee` for the step summary would swallow the failure whole
    # and hand us a broken .deb with a green tick.
    assert "check_bundle.py" in body
    assert "|" not in body.replace("||", ""), "the closure check must not be piped"


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


# --- the beta channel: what must NOT reach a stable user (#186) --------------

#: The one expression every prerelease-gated step and job carries. Written out
#: rather than pattern-matched: the *sense* is the whole point, and a guard
#: someone inverted to ``== 'true'`` would still "mention the output".
PRERELEASE_GUARD = "needs.check-version.outputs.prerelease != 'true'"

#: A step touching any of these is a step that can reach somebody who never
#: opted into a beta. Substring-matched over the step's whole YAML, so a
#: ``uses:``/``with:`` step is screened as readily as a ``run:`` one.
STABLE_USER_REACHING = ("flatpak", "gh-pages")


def all_steps() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """``(job name, job, step)`` for every step in the workflow."""
    return [(name, job, step) for name, job in jobs().items() for step in job.get("steps", [])]


def step_text(step: dict[str, Any]) -> str:
    """One step, flattened — name, run, uses, with and env together.

    Shell comments are stripped from ``run`` first, matching ``run_script``:
    a comment explaining that some *other* job boots the flatpak bundle is not
    a step that touches flatpak, and screening on it would either fail this
    test or teach someone to stop writing the comment.
    """
    screened = dict(step)
    if "run" in screened:
        screened["run"] = "\n".join(
            line for line in str(screened["run"]).splitlines() if not line.lstrip().startswith("#")
        )
    return yaml.safe_dump(screened).lower()


def is_guarded(job: dict[str, Any], step: dict[str, Any]) -> bool:
    """A step is gated if it carries the guard, or its whole job does."""
    return PRERELEASE_GUARD in str(step.get("if", "")) or PRERELEASE_GUARD in str(job.get("if", ""))


def test_check_version_publishes_the_prerelease_verdict() -> None:
    """One classification for the whole run, not a string test per consumer.

    Everything below reads ``needs.check-version.outputs.prerelease``. If that
    output stopped being produced, every guard would silently compare against
    an empty string — which is ``!= 'true'`` — and every gated step would run.
    That is the failure that publishes a beta to stable Flatpak users, so the
    output's existence is asserted before anything that depends on it.
    """
    check = jobs()["check-version"]
    assert "prerelease" in check.get("outputs", {}), "check-version publishes no prerelease output"
    classify = next(step for step in check["steps"] if step.get("id") == "classify")
    body = str(classify["run"])
    assert 'echo "prerelease=$PRERELEASE" >> "$GITHUB_OUTPUT"' in body
    # A SemVer prerelease is exactly "carries a hyphen"; the tag is the input.
    assert "RELEASE_TAG" in body and "*-*" in body


def test_nothing_that_reaches_a_stable_user_runs_for_a_prerelease() -> None:
    """**The** assertion of #186, derived rather than enumerated.

    The gh-pages branch IS the OSTree repo ``flatpak update`` follows, and the
    job that writes it force-pushes an orphan commit. Publishing a beta there
    ships it to STABLE Flatpak users, who opted into nothing — and it cannot be
    undone by re-running an older release. Betas are deliberately DMG / zip /
    tarball only for this first cut.

    Deriving the list from the file is what makes this hold for a step that
    does not exist yet: add a ninth flatpak step without the guard and this
    fails, where a hardcoded list of the eight would pass. Reviewing a diff for
    a missing ``if:`` is exactly the job a human does badly.
    """
    unguarded = [
        f"{job_name}: {step.get('name') or step.get('uses')}"
        for job_name, job, step in all_steps()
        if any(token in step_text(step) for token in STABLE_USER_REACHING)
        and not is_guarded(job, step)
    ]
    assert not unguarded, (
        "these steps can reach a stable Flatpak user and are not gated on "
        f"the release being stable: {unguarded}"
    )


def test_the_gh_pages_publish_refuses_a_prerelease_by_itself() -> None:
    """Belt and braces on the single most dangerous step in the file.

    The ``if:`` above it is the gate; this is the backstop. A guard that lives
    only in a YAML expression is one careless edit away from silence, and the
    thing it prevents is irreversible for every stable Flatpak user. So the
    step also refuses at runtime, on its own, without consulting the gate that
    was supposed to have stopped it.
    """
    publish = next(
        step
        for _job_name, _job, step in all_steps()
        if "gh-pages" in str(step.get("name", "")).lower()
    )
    body = str(publish["run"])
    assert "refusing to publish prerelease" in body
    assert "exit 1" in body
    # It must refuse BEFORE it can push anything.
    assert body.index("exit 1") < body.index("git push"), (
        "the refusal must come before the force-push, not after it"
    )


def test_the_github_release_is_marked_prerelease_for_a_beta() -> None:
    """The flag is the entire client-side mechanism.

    ``updater.check_for_update`` skips any release carrying ``prerelease``
    unless the user opted into the beta channel — and that filter is compiled
    into every nParse+ binary ever shipped, so it cannot be corrected later for
    anyone already installed. If the publish step stopped setting this, a beta
    would be offered to every stable client on earth, which is the precise
    thing this issue exists to prevent.
    """
    publish = next(
        step
        for _job_name, _job, step in all_steps()
        if str(step.get("uses", "")).startswith("softprops/action-gh-release")
    )
    assert (
        publish["with"]["prerelease"] == "${{ needs.check-version.outputs.prerelease == 'true' }}"
    )
    assert "check-version" in jobs()[PUBLISHER]["needs"], (
        "the publish job must wait for check-version to read its output"
    )


def beta_artifact_paths() -> str:
    """Every artifact an unguarded upload step publishes.

    Not gated on the release being stable means, by definition, an artifact a
    beta ships.
    """
    return " ".join(
        str(step.get("with", {}).get("path", ""))
        for _job_name, job, step in all_steps()
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
        and not is_guarded(job, step)
    )


def test_a_beta_never_publishes_a_flatpak_bundle() -> None:
    """The artifact whose distribution channel a beta must not enter.

    Unlike every other artifact here, a .flatpak is not an inert file someone
    downloads deliberately — installing one wires up the gh-pages OSTree remote
    that `flatpak update` then follows, and that repo is stable-only.
    """
    assert ".flatpak" not in beta_artifact_paths()


def test_a_beta_still_publishes_everything_a_tester_can_install() -> None:
    """A beta nobody can install is not a beta.

    The .deb is here on purpose, and only because ``build_deb.debian_version``
    translates the prerelease into Debian's ordering — see
    ``test_deb_packaging.py``. Debian splits a version at the last hyphen, so a
    raw ``2.30.0-beta.1`` sorts AFTER the ``2.30.0`` it is promoted to, and a
    tester who installed it could never roll forward. That is the property, not
    the presence of the file, that makes shipping it safe; if the translation
    were reverted, that test fails and this one should be revisited with it.
    """
    beta_paths = beta_artifact_paths()
    for expected in (".tar.gz", ".dmg", ".zip", ".deb"):
        assert expected in beta_paths, f"a beta should still publish {expected}"


def test_the_debian_build_is_exercised_on_a_beta() -> None:
    """Why the .deb is not simply gated off for prereleases.

    ``build-linux-debian12`` and ``verify-deb-debian12`` are both in the
    publish job's ``needs``, and a skipped job in ``needs`` skips its
    dependents — so gating them would skip the whole release for a beta, and
    the only way back is an ``always()``-flavoured ``if`` on ``release`` that
    would weaken its failure semantics for *every* dependency.

    The upside is the real reason though: the Debian leg is the most fragile
    one in the file (glibc floor, shared-library closure, a pristine-container
    install that is the only check on ``Depends:``), so having it run on every
    beta is exactly what a beta is for.
    """
    for job in ("build-linux-debian12", "verify-deb-debian12"):
        assert PRERELEASE_GUARD not in str(jobs()[job].get("if", "")), (
            f"{job} must keep running for a beta"
        )
        assert job in jobs()[PUBLISHER]["needs"]


def test_the_versioned_docs_deploy_is_stable_only() -> None:
    """Two distinct reasons, one guard.

    ``DOCS_VERSION="${VERSION%.*}"`` turns 2.30.0-beta.1 into a permanent
    "2.30.0-beta" entry in mike's versions.json, which there is no tidy way to
    remove; and the deploy aliases itself to ``latest``, which is what the docs
    site serves by default — so a beta's docs would become what every reader
    sees.
    """
    assert PRERELEASE_GUARD in str(jobs()["docs"].get("if", ""))
    assert "check-version" in jobs()["docs"]["needs"]


# --- the PEP 440 trap: tag, literal and distribution metadata (#186) --------

# What semantic-release writes for a beta. SemVer, which is NOT normalized
# PEP 440 — the whole of the trap.
BETA_VERSION = "2.30.0-beta.1"
BETA_NORMALIZED = "2.30.0b1"


def test_the_tag_and_the_version_literal_still_agree_for_a_beta() -> None:
    """``release.yml``'s first gate, checked here instead of on release day.

    semantic-release writes the SemVer spelling into ``__version__`` and builds
    the tag from the same string, so the grep at ``check-version`` matches
    character for character and needed no change. Asserting it here is what
    stops somebody "normalizing" the literal to keep it PEP 440 — which would
    look tidier and would break that gate on the first beta.
    """
    tag_body = f"v{BETA_VERSION}"[1:]
    literal_line = f'__version__ = "{BETA_VERSION}"'
    assert appversion.read_version(literal_line) == tag_body


def test_the_distribution_metadata_normalizes_and_that_is_fine() -> None:
    """The three-way disagreement, stated so nobody 'fixes' it.

    hatchling reads the literal through ``[tool.hatch.version]`` and emits a
    normalized version, so the wheel, the dist-info directory and
    ``importlib.metadata.version()`` all say ``2.30.0b1`` while the tag and the
    literal say ``2.30.0-beta.1``. Three spellings, one version: they compare
    equal under ``packaging.Version``, which is what every consumer in the app
    actually uses. Verified against a real wheel and a real frozen bundle
    before this was written.
    """
    assert str(Version(BETA_VERSION)) == BETA_NORMALIZED
    assert Version(BETA_VERSION) == Version(BETA_NORMALIZED)
    # And the ordering the whole channel design rests on.
    assert Version("2.29.0") < Version(BETA_VERSION) < Version("2.30.0")


def test_the_windows_version_resource_survives_a_beta() -> None:
    """The one thing that genuinely broke, and where it broke.

    VERSIONINFO wants four integers. ``int(p) for p in version.split(".")[:3]``
    hits ``"0-beta"`` and raises during *spec evaluation*, so the Windows leg
    of the first beta would have died before the workflow's own version checks
    could say anything about why.
    """
    assert appversion.windows_version_tuple("2.28.0") == (2, 28, 0, 0)
    assert appversion.windows_version_tuple(BETA_VERSION) == (2, 30, 0, 1)
    assert appversion.windows_version_tuple("2.30.0-beta.12") == (2, 30, 0, 12)
    # A stable version keeps exactly the tuple it always produced.
    assert appversion.windows_version_tuple("2.30.0") == (2, 30, 0, 0)


def test_the_prerelease_test_is_the_same_one_the_workflow_makes() -> None:
    """The build and ``check-version`` must not disagree about what a beta is."""
    assert appversion.is_prerelease(BETA_VERSION)
    assert not appversion.is_prerelease("2.30.0")
    classify = next(
        step for step in jobs()["check-version"]["steps"] if step.get("id") == "classify"
    )
    # Both sides ask "is there a hyphen".
    assert "*-*" in str(classify["run"])


def test_the_spec_uses_the_shared_version_arithmetic() -> None:
    """Not a private copy — the tested one.

    A spec file cannot be imported, so anything inlined there is untestable.
    The three tests above are only worth anything if the spec actually calls
    this code.
    """
    spec = (REPO_ROOT / "packaging/nparseplus.spec").read_text(encoding="utf-8")
    assert "from appversion import read_version, windows_version_tuple" in spec
    assert "windows_version_tuple(VERSION)" in spec


# --- semantic-release: master cuts betas, stable is promoted ----------------


def semantic_release_config() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "semantic_release"
    ]


def test_master_is_configured_to_cut_prereleases() -> None:
    """The default blast radius of a merge, as a test.

    This is the whole feature: merging to master must not ship to everyone. If
    this table went back to ``prerelease = false``, every merge would resume
    auto-publishing a stable release to every installed client — silently, and
    with nothing else in the repo noticing.
    """
    branches = semantic_release_config()["branches"]
    master = branches["main"]
    assert master["prerelease"] is True
    assert master["prerelease_token"] == "beta"


def test_there_is_a_deliberate_promotion_path() -> None:
    """Betas are worthless without a way to promote one.

    semantic-release decides prerelease-ness from the branch table matching the
    CURRENT branch and offers no flag that forces a stable release on a
    prerelease branch — so the promotion is a branch NAME, and the workflow
    checks master out under it. The two halves have to agree.
    """
    promote = semantic_release_config()["branches"]["promote"]
    assert promote["prerelease"] is False
    branch_name = promote["match"]
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/semantic-release.yml").read_text(encoding="utf-8")
    )
    steps_ = workflow["jobs"]["release"]["steps"]
    checkout = next(s for s in steps_ if f"git checkout -b {branch_name}" in str(s.get("run", "")))
    assert "inputs.promote" in str(checkout["if"])
    # And it must land on master, or master's __version__ falls behind the
    # newest tag and the next merge cuts a beta BELOW the shipped stable.
    land = next(s for s in steps_ if "Land the promotion" in str(s.get("name", "")))
    assert "git push origin HEAD:master" in str(land["run"])
    # Refusing to land something still carrying a prerelease segment.
    assert "*-*" in str(land["run"]) and "exit 1" in str(land["run"])
