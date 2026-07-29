"""core.eqini — shared read/splice/write plumbing for EQ character ini files."""

from pathlib import Path

from nparseplus.core.eqini import (
    SERVER_SUFFIXES,
    backup_once,
    character_ini_files,
    character_name,
    detect_newline,
    preflight,
    read_lines,
    replace_section,
    section_body,
    section_bounds,
    split_key_value,
    write_lines,
)

SAMPLE = """[Defaults]
Version=1

[Friends]
Friend0=Alice

[KeyMaps]
Forward=W
"""


def _lines() -> list[str]:
    return SAMPLE.splitlines()


def test_section_bounds_finds_middle_section() -> None:
    start, end = section_bounds(_lines(), "Friends")
    assert _lines()[start] == "[Friends]"
    assert _lines()[end] == "[KeyMaps]"


def test_section_bounds_last_section_runs_to_eof() -> None:
    lines = _lines()
    start, end = section_bounds(lines, "KeyMaps")
    assert lines[start] == "[KeyMaps]"
    assert end == len(lines)


def test_section_bounds_missing_section_is_none() -> None:
    assert section_bounds(_lines(), "Socials") is None


def test_section_bounds_is_case_insensitive() -> None:
    assert section_bounds(_lines(), "friends") == section_bounds(_lines(), "Friends")


def test_section_body_excludes_the_header() -> None:
    assert section_body(_lines(), "Friends") == ["Friend0=Alice", ""]
    assert section_body(_lines(), "Socials") == []


def test_replace_section_splices_and_preserves_neighbours() -> None:
    result = replace_section(_lines(), "Friends", ["Friend0=Zed"])
    text = "\n".join(result)
    assert "Friend0=Zed" in text
    assert "Friend0=Alice" not in text
    assert "[Defaults]" in text and "Version=1" in text
    assert "[KeyMaps]" in text and "Forward=W" in text


def test_replace_section_appends_with_blank_separator_when_missing() -> None:
    result = replace_section(["[Defaults]", "Version=1"], "Socials", ["Page1Button1Name=Hi"])
    assert result == ["[Defaults]", "Version=1", "", "[Socials]", "Page1Button1Name=Hi"]


def test_replace_section_appends_to_empty_file_without_leading_blank() -> None:
    assert replace_section([], "Socials", ["A=1"]) == ["[Socials]", "A=1"]


def test_split_key_value_parses_and_rejects() -> None:
    assert split_key_value("Friend0=Alice") == ("Friend0", "Alice")
    assert split_key_value("  Spaced = value  ") == ("Spaced", "value")
    assert split_key_value("Empty=") == ("Empty", "")
    for junk in ("", "   ", "; comment", "# comment", "[Section]", "novalue", "=orphan"):
        assert split_key_value(junk) is None


def test_character_ini_files_filters_ui_and_server(tmp_path: Path) -> None:
    (tmp_path / "Xantik_P1999Green.ini").write_text("[Friends]\n")
    (tmp_path / "Beeta_P1999Green.ini").write_text("[Friends]\n")
    (tmp_path / "UI_Xantik_P1999Green.ini").write_text("[UI]\n")
    (tmp_path / "Xantik_P1999PVP.ini").write_text("[Friends]\n")

    names = [p.name for p in character_ini_files(tmp_path, "P1999Green")]
    assert names == ["Beeta_P1999Green.ini", "Xantik_P1999Green.ini"]
    red = character_ini_files(tmp_path, SERVER_SUFFIXES["P1999Red"])
    assert [p.name for p in red] == ["Xantik_P1999PVP.ini"]
    assert character_ini_files(tmp_path / "missing", "P1999Green") == []


def test_character_name_strips_the_suffix() -> None:
    assert character_name(Path("Xantik_P1999Green.ini"), "P1999Green") == "Xantik"
    # A file that doesn't carry the suffix keeps its stem rather than being mangled.
    assert character_name(Path("Loose.ini"), "P1999Green") == "Loose"


def test_read_lines_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_lines(tmp_path / "nope.ini") == []


def test_read_write_round_trips_non_utf8_bytes(tmp_path: Path) -> None:
    path = tmp_path / "odd.ini"
    original = b"[Defaults]\nName=caf\xe9\n"  # cp1252 e-acute: not valid UTF-8
    path.write_bytes(original)
    write_lines(path, read_lines(path))
    assert path.read_bytes() == original


def test_detect_newline(tmp_path: Path) -> None:
    crlf = tmp_path / "crlf.ini"
    crlf.write_bytes(b"[Defaults]\r\nVersion=1\r\n")
    lf = tmp_path / "lf.ini"
    lf.write_bytes(b"[Defaults]\nVersion=1\n")
    assert detect_newline(crlf) == "\r\n"
    assert detect_newline(lf) == "\n"
    assert detect_newline(tmp_path / "missing.ini") == "\n"


def test_write_lines_honours_the_requested_newline(tmp_path: Path) -> None:
    path = tmp_path / "out.ini"
    write_lines(path, ["[Defaults]", "Version=1"], newline="\r\n")
    assert path.read_bytes() == b"[Defaults]\r\nVersion=1\r\n"


def test_backup_once_copies_once_and_never_clobbers(tmp_path: Path) -> None:
    path = tmp_path / "Xantik_P1999Green.ini"
    path.write_text("original\n")
    backup_once(path, "socials_backup")
    backup = tmp_path / "socials_backup" / "Xantik_P1999Green.ini"
    assert backup.read_text() == "original\n"

    path.write_text("modified\n")
    backup_once(path, "socials_backup")
    assert backup.read_text() == "original\n"


def test_preflight_reports_each_failure(tmp_path: Path) -> None:
    assert preflight(None) == "Set the EQ install directory first."
    assert "Not a directory" in preflight(tmp_path / "missing")
    assert "eqgame.exe" in preflight(tmp_path)
    (tmp_path / "eqgame.exe").write_text("")
    assert "uifiles" in preflight(tmp_path)
    (tmp_path / "uifiles").mkdir()
    assert preflight(tmp_path) is None
