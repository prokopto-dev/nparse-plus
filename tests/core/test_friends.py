"""core.friends — EQ client [Friends] ini sync (EQTool SettingsGeneral port)."""

from pathlib import Path

from nparseplus.core.friends import (
    BACKUP_DIR_NAME,
    FRIEND_SLOTS,
    SERVER_SUFFIXES,
    friend_ini_files,
    merged_friends,
    normalize_names,
    push_friends,
    read_friends,
    write_friends,
)

INI_WITH_FRIENDS = """[Defaults]
Version=1

[Friends]
Friend0=Alice
Friend1=bob
Friend2=*NULL*
Friend3=Cara

[KeyMaps]
Forward=W
"""


def _eq_dir(tmp_path: Path) -> Path:
    (tmp_path / "Xantik_P1999Green.ini").write_text(INI_WITH_FRIENDS)
    (tmp_path / "Beeta_P1999Green.ini").write_text("[Friends]\nFriend0=Dan\nFriend1=ALICE\n")
    (tmp_path / "UI_Xantik_P1999Green.ini").write_text("[UI]\n")  # layout file: excluded
    (tmp_path / "Xantik_P1999PVP.ini").write_text("[Friends]\nFriend0=Redguy\n")
    return tmp_path


def test_friend_ini_files_filters_ui_and_server(tmp_path: Path) -> None:
    eq_dir = _eq_dir(tmp_path)
    names = [p.name for p in friend_ini_files(eq_dir, "P1999Green")]
    assert names == ["Beeta_P1999Green.ini", "Xantik_P1999Green.ini"]
    # Red display name maps to the P1999PVP file suffix.
    red = friend_ini_files(eq_dir, SERVER_SUFFIXES["P1999Red"])
    assert [p.name for p in red] == ["Xantik_P1999PVP.ini"]
    assert friend_ini_files(eq_dir / "missing", "P1999Green") == []


def test_read_friends_skips_nulls_and_other_sections(tmp_path: Path) -> None:
    eq_dir = _eq_dir(tmp_path)
    assert read_friends(eq_dir / "Xantik_P1999Green.ini") == ["Alice", "bob", "Cara"]


def test_merged_friends_dedupes_case_insensitively(tmp_path: Path) -> None:
    eq_dir = _eq_dir(tmp_path)
    merged = merged_friends(friend_ini_files(eq_dir, "P1999Green"))
    # "ALICE"/"Alice" collapse (first spelling seen wins), sorted case-insensitively.
    assert [n.lower() for n in merged] == ["alice", "bob", "cara", "dan"]


def test_normalize_names_cleans_and_dedupes() -> None:
    assert normalize_names(["  Zed ", "", "*null*", "zed", "Abe"]) == ["Abe", "Zed"]


def test_normalize_names_caps_at_slot_count() -> None:
    # Sorted first, then capped — same as EQTool's OrderBy().Take(100).
    cleaned = normalize_names([f"x{i}" for i in range(150)])
    assert len(cleaned) == FRIEND_SLOTS


def test_write_friends_replaces_section_with_100_slots(tmp_path: Path) -> None:
    eq_dir = _eq_dir(tmp_path)
    path = eq_dir / "Xantik_P1999Green.ini"
    write_friends(path, ["Ann", "Bob"])
    text = path.read_text()
    lines = text.splitlines()
    assert "Friend0=Ann" in lines and "Friend1=Bob" in lines
    assert "Friend2=*NULL*" in lines and "Friend99=*NULL*" in lines
    assert "Friend100" not in text
    # Surrounding sections intact.
    assert "[Defaults]" in text and "[KeyMaps]" in text and "Forward=W" in text
    assert read_friends(path) == ["Ann", "Bob"]


def test_write_friends_appends_missing_section(tmp_path: Path) -> None:
    path = tmp_path / "New_P1999Green.ini"
    path.write_text("[Defaults]\nVersion=1\n")
    write_friends(path, ["Ann"])
    assert read_friends(path) == ["Ann"]
    assert path.read_text().startswith("[Defaults]")


def test_push_friends_backs_up_once_and_writes_all(tmp_path: Path) -> None:
    eq_dir = _eq_dir(tmp_path)
    files = friend_ini_files(eq_dir, "P1999Green")
    original = (eq_dir / "Beeta_P1999Green.ini").read_text()

    assert push_friends(files, ["Zed", "ann"]) == []
    for path in files:
        assert read_friends(path) == ["ann", "Zed"]

    # Backup holds the pristine original and never gets clobbered.
    backup = eq_dir / BACKUP_DIR_NAME / "Beeta_P1999Green.ini"
    assert backup.read_text() == original
    assert push_friends(files, ["Other"]) == []
    assert backup.read_text() == original
