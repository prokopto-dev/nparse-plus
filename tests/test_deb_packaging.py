"""``packaging/deb/build_deb.py`` — the Debian 12 package.

The generic Linux tarball is built on ``ubuntu-latest``, so its glibc floor is
that image's (2.39) and it does not start on Debian 12 (2.36). The ``.deb`` is
the same application built in a ``debian:12`` container. It is an ADDITIONAL
artifact — the tarball and the Flatpak are untouched by it — and these assert
the two properties that make that true: the filename cannot be picked up by
the updater, and the packaging cannot drift away from the Flatpak's.

``dpkg-deb`` is never invoked here, so these run on any platform. One test
does shell out to ``dpkg --compare-versions`` and skips without it — Debian's
ordering rules are the authority on themselves, and the ubuntu leg of the CI
matrix has it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packaging" / "deb"))

import build_deb  # noqa: E402
import check_bundle  # noqa: E402

FLATPAK_MANIFEST = REPO_ROOT / "packaging/flatpak/io.github.prokopto_dev.nparse_plus.yml"


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """A staged package tree over a stand-in for the PyInstaller onedir."""
    dist = tmp_path / "dist" / "nparseplus"
    (dist / "_internal").mkdir(parents=True)
    (dist / "nparseplus").write_text("#!/bin/sh\n", encoding="utf-8")
    (dist / "_internal" / "libpython.so").write_text("x", encoding="utf-8")
    root = tmp_path / "root"
    build_deb.stage(root, dist, "2.24.0", "2.36")
    return root


# --- the filename is the load-bearing decision -----------------------------


def test_the_filename_is_inert_to_the_deployed_updater() -> None:
    """Every already-released binary carries its own ``pick_asset``.

    That copy cannot be patched by this change, so the ``.deb`` has to be
    invisible to it by NAME: no ``-linux`` substring, and none of the suffixes
    any platform branch sweeps for. ``tests/test_updater.py`` asserts the
    other half — that a release carrying this name still picks correctly.
    """
    name = build_deb.deb_filename("2.24.0")
    assert name == "nparseplus_2.24.0_amd64.deb"
    assert "-linux" not in name
    for suffix in (".tar.gz", ".flatpak", ".zip", ".dmg"):
        assert not name.endswith(suffix)


# --- control ----------------------------------------------------------------


def test_control_carries_the_fields_dpkg_and_apt_need(staged: Path) -> None:
    control = (staged / "DEBIAN" / "control").read_text(encoding="utf-8")
    for field in ("Package: nparseplus", "Version: 2.24.0", "Architecture: amd64"):
        assert field in control
    assert "Section:" in control and "Maintainer:" in control
    # Installed-Size must be a real measurement, not the unrendered token.
    size = next(line for line in control.splitlines() if line.startswith("Installed-Size:"))
    assert int(size.split(":", 1)[1].strip()) > 0
    for token in ("@VERSION@", "@INSTALLED_SIZE@", "@GLIBC_FLOOR@"):
        assert token not in control, f"unrendered control.in token {token}"


def test_the_glibc_floor_is_declared(staged: Path) -> None:
    """The floor is the whole reason this package exists, so apt must enforce it.

    CI measures the real value with ``objdump`` over the bundled ELF files and
    passes it in; the default is bookworm's. Either way it lands in Depends,
    so apt refuses the install rather than letting it fail at exec time with
    the ``GLIBC_2.xx not found`` message that started all this.
    """
    control = (staged / "DEBIAN" / "control").read_text(encoding="utf-8")
    assert "libc6 (>= 2.36)" in control


def test_audio_is_a_hard_dependency(staged: Path) -> None:
    """Trigger audio and TTS are core features, so espeak is Depends, not Recommends.

    ``audio/tts.default_speaker`` returns ``EspeakSpeaker`` only when
    ``find_espeak()`` locates an espeak binary on PATH, and **NullSpeaker
    otherwise** — no error, no log line, just silence. Silent degradation is
    the worst failure mode an alerting tool has, and a Recommends is skippable
    (``--no-install-recommends``, and some minimal installs default to it).

    espeak is also the ONLY audio path on Linux: the trigger engine's
    ``sound_player`` seam is never wired, and the spec excludes Qt Multimedia
    and QtTextToSpeech from the bindings entirely. The Flatpak bundles
    espeak-ng and pcaudiolib for this same reason; a .deb declares it instead.
    """
    control = (staged / "DEBIAN" / "control").read_text(encoding="utf-8")
    depends = control.split("Depends:", 1)[1].split("Recommends:", 1)[0]
    assert "espeak-ng" in depends
    recommends = next(line for line in control.splitlines() if line.startswith("Recommends:"))
    assert "espeak" not in recommends


def test_the_silent_fallback_that_makes_audio_a_dependency_still_exists() -> None:
    """Ties the Depends above to the behaviour that justifies it.

    If ``default_speaker`` ever grew a loud failure — a raised error, a
    user-visible warning — espeak could go back to Recommends. While it
    silently returns NullSpeaker, it cannot.
    """
    source = (REPO_ROOT / "src/nparseplus/audio/tts.py").read_text(encoding="utf-8")
    body = source.split("def default_speaker", 1)[1].split("\ndef ", 1)[0]
    assert "find_espeak()" in body
    assert "NullSpeaker()" in body


# --- layout, and not drifting from the Flatpak ------------------------------


def test_the_launcher_is_a_bare_exec(staged: Path) -> None:
    """One source of truth for the Linux env defaults.

    ``app._runtime_env_defaults`` sets QT_QPA_PLATFORM and
    QTWEBENGINE_DISABLE_SANDBOX and never overrides a user's value. The
    Flatpak's launcher repeats them because the portal relaunch path can
    bypass it; nothing bypasses ``/usr/bin/nparseplus``, so repeating them
    here would only create a second copy to forget.
    """
    launcher = (staged / "usr/bin/nparseplus").read_text(encoding="utf-8")
    assert "exec /opt/nparseplus/nparseplus" in launcher
    # Comments (the shebang included) may NAME the variables — the wrapper
    # explains why it omits them. What must not appear is code that sets one.
    code = [
        line for line in launcher.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    assert code == ['exec /opt/nparseplus/nparseplus "$@"']


def test_the_desktop_entry_is_the_flatpak_one_verbatim(staged: Path) -> None:
    """Copied, not forked — the two packagings cannot describe the app differently."""
    installed = staged / f"usr/share/applications/{build_deb.APP_ID}.desktop"
    source = REPO_ROOT / f"packaging/flatpak/{build_deb.APP_ID}.desktop"
    assert installed.read_bytes() == source.read_bytes()
    entry = installed.read_text(encoding="utf-8")
    # Exec resolves through /usr/bin, Icon through the hicolor names below.
    assert "Exec=nparseplus" in entry
    assert f"Icon={build_deb.APP_ID}" in entry


def test_the_icon_set_matches_the_flatpak_manifest(staged: Path) -> None:
    """Same sizes from the same sources, so a new render reaches both packagings.

    hicolor has no 24px directory, which is why ``icon-24.png`` — the Windows
    small-icon step — is the one size ``tools/gen_icons.py`` produces that
    neither packaging installs.
    """
    manifest = FLATPAK_MANIFEST.read_text(encoding="utf-8")
    for size, source in build_deb.ICON_SIZES.items():
        installed = staged / f"usr/share/icons/hicolor/{size}/apps/{build_deb.APP_ID}.png"
        assert installed.is_file(), f"missing {size} icon"
        assert (REPO_ROOT / source).is_file()
        assert f"hicolor/{size}/apps/{build_deb.APP_ID}.png" in manifest
    assert (staged / f"usr/share/icons/hicolor/scalable/apps/{build_deb.APP_ID}.svg").is_file()
    assert "24x24" not in "".join(build_deb.ICON_SIZES)


def test_the_payload_lands_under_opt(staged: Path) -> None:
    assert (staged / "opt/nparseplus/nparseplus").is_file()
    assert (staged / "opt/nparseplus/_internal/libpython.so").is_file()
    assert (staged / "usr/share/doc/nparseplus/copyright").is_file()


def test_maintainer_scripts_never_fail_the_install(staged: Path) -> None:
    """Desktop/icon caches are a nicety; a headless box has neither tool."""
    for name in ("postinst", "postrm"):
        body = (staged / "DEBIAN" / name).read_text(encoding="utf-8")
        assert "command -v" in body and "|| true" in body


@pytest.mark.skipif(os.name == "nt", reason="NTFS has no POSIX executable bit")
def test_maintainer_scripts_are_executable(staged: Path) -> None:
    """dpkg refuses to run a maintainer script that is not executable.

    POSIX-only: ``Path.chmod(0o755)`` cannot set an executable bit on NTFS, so
    on Windows this asserts nothing about the package. That costs no coverage
    — the .deb is only ever built inside the debian:12 container — but the
    assertion has to be skipped rather than dropped, or a lost ``chmod``
    reaches users as a failing install.
    """
    for name in ("postinst", "postrm"):
        script = staged / "DEBIAN" / name
        assert script.stat().st_mode & 0o111, f"{name} must be executable"


# --- the shared-library closure check ---------------------------------------
#
# The first Debian 12 release build failed here for real: the container had no
# libxkbfile1, so every QtWebEngine binary came out with an unresolved
# libxkbfile.so.1 and the Discord overlay would have shipped broken. These
# assert the classification that decides whether that is fatal.

# Verbatim from that failed run (github.com/prokopto-dev/nparse-plus/actions/
# runs/32575040827), trimmed to one file per distinct case.
REAL_FAILURE = {
    "PySide6/QtWebEngineCore.abi3.so": ["libxkbfile.so.1"],
    "PySide6/Qt/lib/libQt6WebEngineCore.so.6": ["libxkbfile.so.1"],
    "PySide6/Qt/libexec/QtWebEngineProcess": ["libxkbfile.so.1"],
    "PySide6/Qt/plugins/platforms/libqwayland.so": ["libwayland-client.so.0"],
    "PySide6/Qt/plugins/platformthemes/libqgtk3.so": ["libgtk-3.so.0"],
    "PySide6/Qt/plugins/imageformats/libqtiff.so": ["libtiff.so.5"],
    "PySide6/Qt/lib/libQt6Multimedia.so.6": ["libpulse.so.0"],
}


def test_a_broken_webengine_is_fatal() -> None:
    """The regression that actually happened. The Discord overlay is the app.

    A build that loses QtWebEngine still produces a perfectly good-looking
    .deb — that is precisely why this has to fail the build rather than warn.
    """
    fatal, _ = check_bundle.classify(REAL_FAILURE)
    assert set(fatal) == {
        "PySide6/QtWebEngineCore.abi3.so",
        "PySide6/Qt/lib/libQt6WebEngineCore.so.6",
        "PySide6/Qt/libexec/QtWebEngineProcess",
    }


def test_plugins_the_app_never_loads_are_not_fatal() -> None:
    """A gate that fails on things that do not matter is a gate that gets turned off.

    libtiff.so.5 cannot be satisfied on bookworm at all (it ships
    libtiff.so.6) and the Ubuntu tarball carries the same unresolved entry
    unnoticed; Wayland is deliberately unused (the app pins xcb); GTK theming
    and PulseAudio belong to Qt modules the spec excludes.
    """
    _, tolerated = check_bundle.classify(REAL_FAILURE)
    assert set(tolerated) == {
        "PySide6/Qt/plugins/platforms/libqwayland.so",
        "PySide6/Qt/plugins/platformthemes/libqgtk3.so",
        "PySide6/Qt/plugins/imageformats/libqtiff.so",
        "PySide6/Qt/lib/libQt6Multimedia.so.6",
    }


def test_a_clean_bundle_passes() -> None:
    fatal, tolerated = check_bundle.classify({})
    assert not fatal and not tolerated
    assert "resolves" in check_bundle.report(fatal, tolerated)


def test_the_report_names_every_unresolved_soname() -> None:
    """Tolerated is not silent — the report is how a NEW gap gets noticed."""
    fatal, tolerated = check_bundle.classify(REAL_FAILURE)
    text = check_bundle.report(fatal, tolerated)
    for soname in ("libwayland-client.so.0", "libgtk-3.so.0", "libtiff.so.5", "libpulse.so.0"):
        assert soname in text
    # And a known-absent one carries its reason, so nobody re-investigates it.
    assert "bookworm ships libtiff.so.6" in text
    assert "libxkbfile.so.1" in text


def test_the_xcb_platform_plugin_is_critical_and_wayland_is_not() -> None:
    """app._runtime_env_defaults pins QT_QPA_PLATFORM=xcb.

    Without libqxcb.so nothing renders at all, so it is fatal. Its Wayland
    sibling is deliberately absent from the critical set: the overlays cannot
    stay on top under native Wayland, which is why the app does not use it.
    """
    assert "libqxcb.so" in check_bundle.CRITICAL
    assert "libqwayland.so" not in check_bundle.CRITICAL


def test_the_package_depends_on_what_webengine_needs() -> None:
    """libxkbfile1 is what the first build was missing; apt must guarantee it."""
    control = (REPO_ROOT / "packaging/deb/control.in").read_text(encoding="utf-8")
    assert "libxkbfile1" in control


# --- a prerelease must still order correctly for apt (#186) -----------------

#: What semantic-release produces for a beta, and what it promotes to.
BETA = "2.30.0-beta.1"
PROMOTED = "2.30.0"
PREVIOUS_STABLE = "2.29.0"


def test_a_prerelease_is_translated_to_the_debian_idiom() -> None:
    """Runs everywhere, including where there is no dpkg to ask.

    Debian splits a version at the LAST hyphen and reads the tail as the
    debian_revision, so a raw SemVer prerelease is not merely ugly here — it
    inverts the ordering (see the dpkg test below). The hyphen must be gone.
    """
    translated = build_deb.debian_version(BETA)
    assert translated == "2.30.0~beta.1"
    assert "-" not in translated, "a hyphen would be read as a debian_revision"
    assert "~" in translated


def test_a_stable_version_is_left_exactly_as_it_was() -> None:
    """Nothing about an existing package changes."""
    for version in (PROMOTED, PREVIOUS_STABLE, "2.24.0", "10.0.1"):
        assert build_deb.debian_version(version) == version
    assert build_deb.deb_filename(PROMOTED) == "nparseplus_2.30.0_amd64.deb"


def test_the_control_file_carries_the_translated_version() -> None:
    """The control field is what dpkg and apt actually compare.

    The filename is cosmetic — `apt` reads `Version:` out of the package — so
    this is the assertion that matters for roll-forward.
    """
    control = build_deb.render_control(BETA, installed_size_kb=1000, glibc_floor="2.36")
    assert "Version: 2.30.0~beta.1" in control
    assert f"Version: {BETA}" not in control


def test_the_filename_of_a_beta_is_still_inert_to_the_deployed_updater() -> None:
    """The #160 rule does not get a pass for carrying a tilde.

    ``updater.pick_asset`` sweeps for ``"-linux" in name`` plus a suffix, and
    that predicate ships compiled into every already-released binary.
    """
    name = build_deb.deb_filename(BETA)
    assert "-linux" not in name
    assert not name.endswith((".tar.gz", ".flatpak", ".zip", ".dmg"))


@pytest.mark.skipif(shutil.which("dpkg") is None, reason="needs a real dpkg")
def test_dpkg_orders_the_beta_before_the_stable_it_promotes_to() -> None:
    """The authority, not a reimplementation of Policy 5.6.12.

    Debian's comparison algorithm has enough special cases (letters before
    non-letters, tilde before end-of-part) that asserting it from the prose
    would be asserting my reading of the prose. ``dpkg`` is installed on the
    ubuntu leg of the CI matrix, so this runs for real on every PR.

    Without the translation this fails on the very first comparison:
    ``dpkg --compare-versions 2.30.0-beta.1 gt 2.30.0`` is TRUE, so a user who
    installed the beta could never roll forward — apt would see a downgrade
    and refuse.
    """

    def compare(left: str, operator: str, right: str) -> bool:
        return (
            subprocess.run(
                ["dpkg", "--compare-versions", left, operator, right],
                check=False,
            ).returncode
            == 0
        )

    beta = build_deb.debian_version(BETA)
    later_beta = build_deb.debian_version("2.30.0-beta.2")
    # The whole point: the beta is OLDER than what it is promoted to, so
    # `apt upgrade` rolls a beta tester forward onto the stable release.
    assert compare(beta, "lt", PROMOTED), f"{beta} must sort before {PROMOTED}"
    # And newer than the stable it followed, or it would never install.
    assert compare(PREVIOUS_STABLE, "lt", beta)
    # Betas order among themselves.
    assert compare(beta, "lt", later_beta)
    assert compare(later_beta, "lt", PROMOTED)
    # The bug this exists to prevent, stated as the thing that is now false.
    assert compare(BETA, "gt", PROMOTED), (
        "raw SemVer no longer inverts the ordering — has dpkg changed?"
    )
