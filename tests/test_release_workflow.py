"""Release-workflow invariants that a broken release day would otherwise teach us.

Packaging runs only on tags (``ci.yml`` is tests-only), so a mistake in
``release.yml`` first shows up while cutting a release. These are the two
cheap guards worth having.

Deliberately text/regex, never ``yaml.safe_load``: PyYAML is in the ``docs``
dependency group, not ``dev``, so parsing here would add a test-time
dependency. ``tests/core/plugins/test_template.py`` and
``tests/test_flatpak_portal.py`` read packaging files the same way.

This is a weak guard by nature — it checks names and wiring, not semantics.
What it catches is the pair of mistakes that actually break a release.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/release.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_two_linux_jobs_upload_under_different_artifact_names() -> None:
    """``download-artifact`` merges every artifact into one directory.

    ``merge-multiple: true`` in the release job flattens them, so two uploads
    sharing a name would collide there rather than at upload time.
    """
    # Only the UPLOAD sites: the verify job names the same artifact to
    # download it, which is not a second upload.
    uploads = [block for block in workflow_text().split("uses: actions/upload-artifact")[1:]]
    names = [
        match.group(1)
        for block in uploads
        if (match := re.search(r"^\s+name: (linux[\w-]*)$", block, re.MULTILINE))
    ]
    assert "linux-builds" in names, "the tarball/flatpak artifact was renamed"
    assert "linux-deb-debian12" in names, "the Debian artifact is not uploaded"
    assert len(names) == len(set(names)), f"duplicate artifact names: {names}"


def test_the_release_job_waits_for_the_debian_build() -> None:
    """Without this the .deb races the publish and silently does not ship.

    ``download-artifact`` takes whatever exists when it runs and
    ``files: artifacts/*`` globs whatever it found — there is no error to
    notice.
    """
    needs = re.search(r"^  release:\n    needs: \[([^\]]+)\]", workflow_text(), re.MULTILINE)
    assert needs is not None
    listed = {item.strip() for item in needs.group(1).split(",")}
    assert "build-linux-debian12" in listed
    assert {"build-macos", "build-windows", "build-linux"} <= listed


def test_the_debian_job_builds_in_a_container_not_on_the_runner() -> None:
    """The container IS the feature — it is what sets the glibc floor.

    Dropping back to a bare runner would rebuild the exact artifact this job
    exists to replace, and nothing else in CI would notice.
    """
    text = workflow_text()
    job = text.split("  build-linux-debian12:", 1)[1].split("\n  verify-deb-debian12:", 1)[0]
    assert re.search(r"^    container: debian:12$", job, re.MULTILINE)
    # A container job runs as root and the base image has no sudo. Comments
    # may say so; no command may call it.
    code = "\n".join(line for line in job.splitlines() if not line.lstrip().startswith("#"))
    assert "sudo " not in code


def test_no_linux_tarball_asset_name_is_ambiguous() -> None:
    """The naming rule every future Linux artifact has to obey.

    ``updater.pick_asset`` sweeps for ``"-linux" in name`` plus a suffix, and
    that predicate ships inside every already-released binary — it cannot be
    fixed retroactively. So at most one release asset may end in ``.tar.gz``
    while containing ``-linux``. This is what #160 was, one artifact over.
    """
    text = workflow_text()
    tarballs = set(re.findall(r'"?(dist/[\w.$@{}#-]*\.tar\.gz)"?', text))
    ambiguous = [name for name in tarballs if "-linux" in name]
    assert len(ambiguous) <= 1, f"two Linux tarballs share the updater's predicate: {ambiguous}"
