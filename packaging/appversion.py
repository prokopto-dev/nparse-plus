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
