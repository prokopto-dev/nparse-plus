"""Version arithmetic shared by the PyInstaller spec and its tests.

Its own module, and not inline in ``nparseplus.spec``, for the reason
``packaging/deb/build_deb.py`` is: a spec file runs under PyInstaller's
interpreter before the Analysis and cannot import the app, so anything it does
is untestable where it sits — and the one thing here that is easy to get wrong
kills the Windows build at spec evaluation, before any of that workflow's loud
checks can report it.

Deliberately stdlib-only and free of any ``nparseplus`` import: the spec
evaluates this before the app is importable at all.
"""

from __future__ import annotations

import re

#: The assignment in ``src/nparseplus/__init__.py`` — the single source of
#: truth for the app's version (see the spec's comment on ``copy_metadata``
#: for why it is a literal and not a metadata lookup).
_VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)


def read_version(init_text: str) -> str:
    """The ``__version__`` literal out of ``__init__.py``'s source."""
    match = _VERSION_RE.search(init_text)
    if not match:
        raise SystemExit("cannot find __version__ in src/nparseplus/__init__.py")
    return match.group(1)


def is_prerelease(version: str) -> bool:
    """True for a beta (#186). A SemVer prerelease carries a hyphen.

    The same test ``release.yml``'s ``check-version`` job makes of the tag,
    which is built from this literal — so the workflow and the build agree by
    construction rather than by coincidence.
    """
    return "-" in version


def windows_version_tuple(version: str) -> tuple[int, int, int, int]:
    """The 4-part numeric tuple Windows VERSIONINFO requires.

    Every part must be an integer, and a beta version is ``2.30.0-beta.1``,
    whose third dot-separated field is ``0-beta``. The naive
    ``int(p) for p in version.split(".")[:3]`` raised there, and it raised
    while the spec was being *evaluated* — so the Windows leg of the first
    beta release would have died before the workflow's own version checks
    could say anything useful about why.

    The release segment fills the first three parts and the prerelease
    revision fills the fourth, which is what that slot is for. A stable
    version keeps exactly the tuple it has always produced.
    """
    release, _, pre = version.partition("-")
    parts = tuple(int(p) for p in release.split(".")[:3])
    parts += (0,) * (3 - len(parts))
    revision = re.findall(r"\d+", pre)
    return (*parts, int(revision[-1]) if revision else 0)  # type: ignore[return-value]


#: The prerelease slot a FINAL release occupies in the macOS build number.
#: Above every beta revision of the same version, so the release always
#: outranks the betas it was promoted from.
MACOS_STABLE_BUILD = 999

#: Radix of the macOS build number. Bounds the version components: a minor
#: over 99, a patch over 99 or a beta revision over 998 would carry into the
#: next field and silently invert the ordering, so they are refused loudly
#: instead. The whole number stays well under 2**31.
_MACOS_RADIX = ((10_000_000, 999), (100_000, 99), (1_000, 99))


def macos_short_version(version: str) -> str:
    """``CFBundleShortVersionString`` — the marketing version.

    Apple requires **exactly three period-separated integers**
    (developer.apple.com, CFBundleShortVersionString), so the SemVer
    prerelease suffix cannot go here: ``2.30.0-beta.1`` is not a valid value
    and nothing in the build would have said so. ``codesign --verify`` passes
    on a bundle carrying it — verified — because it checks that the plist was
    signed, not that its fields mean anything.

    A beta of 2.30.0 *is* marketing version 2.30.0, so the release segment is
    the honest answer, and a stable version is returned exactly as before.
    Which beta it is lives in ``macos_bundle_version`` below, giving Finder
    the standard "2.30.0 (2)" reading. The precise version is never lost: the
    app shows ``nparseplus.__version__`` in Settings, tag and literal alike.
    """
    release = version.partition("-")[0]
    parts = [int(p) for p in release.split(".")[:3]]
    parts += [0] * (3 - len(parts))
    return ".".join(str(p) for p in parts)


def macos_bundle_version(version: str) -> str:
    """``CFBundleVersion`` — the build number, monotonic across every release.

    Apple allows one to three period-separated integers here and expects the
    value to increase with each build. One integer is the only shape that can
    order a beta *below* the release it is promoted to while staying valid:
    betas and their stable share a marketing version, so there is no room left
    in a dotted form to separate them (``2.30.0`` is already taken by the
    stable, and three integers is the ceiling).

    So the components are packed into a single number, with the prerelease
    revision in the lowest field and a final release pinned at
    ``MACOS_STABLE_BUILD``::

        2.30.0-beta.1  ->  23000001
        2.30.0-beta.2  ->  23000002
        2.30.0         ->  23000999   (promoted: outranks its own betas)
        2.30.1-beta.1  ->  23001001
        2.31.0-beta.1  ->  23100001

    This deliberately changes what a *stable* build reports (2.28.1 used to
    say "2.28.1"). A build number that is simply the marketing version cannot
    express a prerelease at all, and leaving stables on one scheme while betas
    used another would give macOS two incomparable orderings for the same
    field — the one thing it is supposed to be able to compare.
    """
    release, _, prerelease = version.partition("-")
    parts = [int(p) for p in release.split(".")[:3]]
    parts += [0] * (3 - len(parts))
    digits = re.findall(r"\d+", prerelease)
    revision = int(digits[-1]) if digits else MACOS_STABLE_BUILD
    if prerelease and revision >= MACOS_STABLE_BUILD:
        raise SystemExit(
            f"prerelease revision {revision} in {version!r} would tie or outrank "
            f"the final release's build number ({MACOS_STABLE_BUILD})"
        )
    build = revision
    for value, (multiplier, ceiling) in zip(parts, _MACOS_RADIX, strict=True):
        if value > ceiling:
            raise SystemExit(
                f"version component {value} in {version!r} exceeds {ceiling}; the "
                "macOS build number would carry into the next field and invert "
                "the release ordering"
            )
        build += value * multiplier
    return str(build)
