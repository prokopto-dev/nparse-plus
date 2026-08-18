"""``nparseplus_sdk.eqfiles`` — the lazy install-file re-export (SDK 1.2)."""

from __future__ import annotations

import pytest

from nparseplus_sdk import eqfiles


def test_exports_are_an_explicit_allowlist():
    """Not blanket forwarding: the additive-only 1.x promise should cover the
    names we chose, not everything that happens to live in the host module."""
    assert set(eqfiles.EXPORTS) == {
        "NULL_SENTINEL",
        "backup_once",
        "detect_newline",
        "preflight",
        "read_lines",
        "replace_section",
        "section_body",
        "section_bounds",
        "split_key_value",
        "write_lines",
    }


def test_every_exported_name_resolves_to_the_host_helper():
    from nparseplus.core import eqini

    for name in eqfiles.EXPORTS:
        assert getattr(eqfiles, name) is getattr(eqini, name)


def test_unknown_names_raise_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute 'character_ini_files'"):
        eqfiles.character_ini_files  # noqa: B018 - the access is the assertion


def test_dir_lists_the_allowlist():
    assert set(dir(eqfiles)) == set(eqfiles.EXPORTS) | {"EXPORTS"}


def test_importing_eqfiles_does_not_pull_qt():
    """Same contract as the other re-exports: a plugin's Qt-free unit tests
    and the validate CLI must be able to import it."""
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['PySide6'] = None;\n"
        "from nparseplus_sdk import eqfiles;\n"
        "assert 'PySide6.QtWidgets' not in sys.modules;\n"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_the_round_trip_the_docstring_promises(tmp_path):
    """Splice one section, leave every other byte alone, back it up first."""
    install = tmp_path / "EverQuest"
    install.mkdir()
    (install / "eqgame.exe").write_text("stub")
    (install / "uifiles").mkdir()
    host_file = install / "eqhost.txt"
    host_file.write_text(
        "[LoginServer]\r\nHost=login.eqemulator.net:5998\r\n\r\n[Other]\r\nKeep=me\r\n"
    )

    assert eqfiles.preflight(install) is None
    newline = eqfiles.detect_newline(host_file)
    eqfiles.backup_once(host_file, "loginproxy_backup")
    lines = eqfiles.replace_section(
        eqfiles.read_lines(host_file), "LoginServer", ["Host=127.0.0.1:5998"]
    )
    eqfiles.write_lines(host_file, lines, newline=newline)

    assert eqfiles.section_body(eqfiles.read_lines(host_file), "LoginServer") == [
        "Host=127.0.0.1:5998"
    ]
    assert eqfiles.section_body(eqfiles.read_lines(host_file), "Other") == ["Keep=me"]
    assert newline == "\r\n"
    backup = install / "loginproxy_backup" / "eqhost.txt"
    assert "login.eqemulator.net" in backup.read_text()


def test_backup_once_keeps_the_pristine_copy(tmp_path):
    """Re-applying must not overwrite the original with a modified copy —
    otherwise a revert restores the edit it was meant to undo."""
    install = tmp_path / "EverQuest"
    install.mkdir()
    host_file = install / "eqhost.txt"
    host_file.write_text("[LoginServer]\nHost=login.eqemulator.net:5998\n")

    eqfiles.backup_once(host_file, "b")
    host_file.write_text("[LoginServer]\nHost=127.0.0.1:5998\n")
    eqfiles.backup_once(host_file, "b")

    assert "login.eqemulator.net" in (install / "b" / "eqhost.txt").read_text()


def test_preflight_refuses_a_directory_that_is_not_an_install(tmp_path):
    assert eqfiles.preflight(tmp_path) is not None
    assert eqfiles.preflight(None) is not None
