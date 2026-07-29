"""core.socials — the EQ client's [Socials] macro buttons."""

from pathlib import Path

from nparseplus.core.socials import (
    BACKUP_DIR_NAME,
    DEFAULT_BUTTONS,
    DEFAULT_COLOR,
    DEFAULT_PAGES,
    MAX_LINES,
    DuplicateKind,
    Placement,
    Social,
    SocialGrid,
    copy_socials,
    find_duplicates,
    normalize_socials,
    place_socials,
    push_socials,
    read_socials,
    write_socials,
)

INI = """[Defaults]
Version=1

[Socials]
Page1Button1Name=Assist
Page1Button1Color=13
Page1Button1Line1=/assist
Page1Button1Line2=/pet attack
Page1Button2Name=*NULL*
Page2Button3Name=Sit
Page2Button3Color=4
Page2Button3Line1=/sit

[Friends]
Friend0=Alice

[KeyMaps]
Forward=W
"""


def _ini(tmp_path: Path, name: str = "Xantik_P1999Green.ini", text: str = INI) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


# -- Reading -----------------------------------------------------------------


def test_read_socials_parses_slots_names_colours_and_lines(tmp_path: Path) -> None:
    grid = read_socials(_ini(tmp_path))
    assert [s.slot for s in grid.socials] == [(1, 1), (2, 3)]
    assist = grid.at(1, 1)
    assert assist.name == "Assist"
    assert assist.color == 13
    assert assist.lines == ["/assist", "/pet attack"]
    assert grid.at(2, 3).lines == ["/sit"]


def test_read_socials_treats_null_sentinel_as_empty(tmp_path: Path) -> None:
    grid = read_socials(_ini(tmp_path))
    assert grid.at(1, 2) is None


def test_read_socials_defaults_when_section_missing(tmp_path: Path) -> None:
    grid = read_socials(_ini(tmp_path, text="[Defaults]\nVersion=1\n"))
    assert grid.socials == []
    assert (grid.pages, grid.buttons_per_page) == (DEFAULT_PAGES, DEFAULT_BUTTONS)


def test_read_socials_tolerates_missing_file(tmp_path: Path) -> None:
    grid = read_socials(tmp_path / "nope.ini")
    assert grid.socials == []
    assert grid.lines_per_button == MAX_LINES


def test_read_socials_discovers_grid_extents(tmp_path: Path) -> None:
    text = "[Socials]\nPage3Button12Name=Wide\nPage3Button12Line6=/six\n"
    grid = read_socials(_ini(tmp_path, text=text))
    assert grid.buttons_per_page == 12  # grew past the 10-button floor
    assert grid.pages == DEFAULT_PAGES  # 3 pages seen, floor still wins
    assert grid.lines_per_button == 6


def test_read_socials_discovers_zero_based_origin(tmp_path: Path) -> None:
    text = "[Socials]\nPage0Button0Name=First\nPage0Button0Line1=/one\n"
    grid = read_socials(_ini(tmp_path, text=text))
    assert (grid.page_origin, grid.button_origin) == (0, 0)
    assert grid.contains_slot(0, 0)
    assert grid.at(0, 0).name == "First"


def test_read_socials_ignores_lookalike_keys_in_other_sections(tmp_path: Path) -> None:
    text = "[Friends]\nPage1Button1Name=NotAMacro\n\n[Socials]\nPage2Button2Name=Real\n"
    grid = read_socials(_ini(tmp_path, text=text))
    assert [s.name for s in grid.socials] == ["Real"]


def test_read_socials_bad_colour_falls_back(tmp_path: Path) -> None:
    text = "[Socials]\nPage1Button1Name=Odd\nPage1Button1Color=blue\n"
    grid = read_socials(_ini(tmp_path, text=text))
    assert grid.at(1, 1).color == DEFAULT_COLOR


# -- Normalizing -------------------------------------------------------------


def test_normalize_socials_trims_drops_and_dedupes() -> None:
    cleaned = normalize_socials(
        [
            Social(page=1, button=1, name="  Assist  ", lines=[" /assist ", "", ""]),
            Social(page=1, button=1, name="Later", lines=["/later"]),  # same slot: last wins
            Social(page=1, button=2, name="   ", lines=["  "]),  # empty: dropped
            Social(page=1, button=3, name="Long", lines=[f"/l{i}" for i in range(9)]),
        ]
    )
    assert [s.slot for s in cleaned] == [(1, 1), (1, 3)]
    assert cleaned[0].name == "Later"
    assert len(cleaned[1].lines) == MAX_LINES


def test_normalize_socials_clamps_colour() -> None:
    cleaned = normalize_socials([Social(page=1, button=1, name="X", color=9999)])
    assert cleaned[0].color == 255


# -- Writing -----------------------------------------------------------------


def test_write_socials_preserves_other_sections(tmp_path: Path) -> None:
    path = _ini(tmp_path)
    write_socials(path, [Social(page=1, button=1, name="Renamed", lines=["/assist"])])
    text = path.read_text()
    assert "[Defaults]" in text and "Version=1" in text
    assert "[Friends]" in text and "Friend0=Alice" in text
    assert "[KeyMaps]" in text and "Forward=W" in text
    assert "Page1Button1Name=Renamed" in text
    # Untouched slots survive.
    assert "Page2Button3Name=Sit" in text


def test_write_socials_preserves_unknown_keys_inside_the_section(tmp_path: Path) -> None:
    text = (
        "[Socials]\n"
        "; a comment\n"
        "Page1Button1Name=Assist\n"
        "Page1Button1Hotkey=X\n"  # unknown key on a slot we rewrite
        "SomeFutureKey=1\n"  # unknown key we cannot attribute
        "\n"
    )
    path = _ini(tmp_path, text=text)
    write_socials(path, [Social(page=1, button=1, name="Assist", lines=["/assist"])])
    written = path.read_text()
    assert "; a comment" in written
    assert "SomeFutureKey=1" in written
    # We only own Name/Color/LineN. An unrecognised key on a slot we rewrite
    # is kept: we cannot tell whether it is stale, and dropping a key a future
    # client build cares about would be the worse failure.
    assert "Page1Button1Hotkey=X" in written


def test_write_socials_drops_stale_line_keys(tmp_path: Path) -> None:
    text = (
        "[Socials]\n"
        "Page1Button1Name=Long\n"
        "Page1Button1Line1=/one\n"
        "Page1Button1Line2=/two\n"
        "Page1Button1Line3=/three\n"
        "Page1Button1Line4=/four\n"
    )
    path = _ini(tmp_path, text=text)
    write_socials(path, [Social(page=1, button=1, name="Short", lines=["/one", "/two"])])
    written = path.read_text()
    assert "Page1Button1Line2=/two" in written
    assert "Line3" not in written and "Line4" not in written


def test_write_socials_appends_section_when_missing(tmp_path: Path) -> None:
    path = _ini(tmp_path, text="[Defaults]\nVersion=1\n")
    write_socials(path, [Social(page=1, button=1, name="New", lines=["/new"])])
    assert path.read_text().startswith("[Defaults]")
    assert read_socials(path).at(1, 1).name == "New"


def test_write_socials_clear_missing_omits_by_default(tmp_path: Path) -> None:
    path = _ini(tmp_path, text="[Socials]\nPage1Button1Name=Gone\nPage2Button3Name=Kept\n")
    write_socials(path, [Social(page=2, button=3, name="Kept")], clear_missing=True)
    written = path.read_text()
    assert "Page1Button1Name" not in written
    assert "Page2Button3Name=Kept" in written


def test_write_socials_clear_missing_matches_null_style(tmp_path: Path) -> None:
    path = _ini(
        tmp_path,
        text="[Socials]\nPage1Button1Name=Gone\nPage1Button2Name=*NULL*\nPage2Button3Name=Kept\n",
    )
    write_socials(path, [Social(page=2, button=3, name="Kept")], clear_missing=True)
    written = path.read_text()
    assert "Page1Button1Name=*NULL*" in written
    assert "Page2Button3Name=Kept" in written


def test_write_socials_preserves_crlf(tmp_path: Path) -> None:
    path = tmp_path / "Crlf_P1999Green.ini"
    path.write_bytes(INI.replace("\n", "\r\n").encode())
    write_socials(path, [Social(page=1, button=1, name="Assist", lines=["/assist"])])
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_read_write_round_trip_is_byte_identical(tmp_path: Path) -> None:
    """Saving what we just read must not perturb a single byte."""
    path = _ini(tmp_path)
    original = path.read_bytes()
    write_socials(path, read_socials(path).socials)
    assert path.read_bytes() == original


def test_round_trip_does_not_truncate_a_longer_line_build(tmp_path: Path) -> None:
    """A build allowing more than MAX_LINES per button must survive read+write."""
    text = "[Socials]\nPage1Button1Name=Six\n" + "".join(
        f"Page1Button1Line{i}=/cmd{i}\n" for i in range(1, 7)
    )
    path = _ini(tmp_path, text=text)
    grid = read_socials(path)
    assert len(grid.at(1, 1).lines) == 6
    assert grid.lines_per_button == 6

    original = path.read_bytes()
    write_socials(path, grid.socials)
    assert path.read_bytes() == original


# -- Push / copy -------------------------------------------------------------


def test_push_socials_backs_up_once(tmp_path: Path) -> None:
    path = _ini(tmp_path)
    original = path.read_text()
    assert push_socials([path], [Social(page=1, button=1, name="One", lines=["/one"])]) == []
    backup = tmp_path / BACKUP_DIR_NAME / path.name
    assert backup.read_text() == original

    assert push_socials([path], [Social(page=1, button=1, name="Two", lines=["/two"])]) == []
    assert backup.read_text() == original


def test_push_socials_returns_errors_and_never_raises(tmp_path: Path) -> None:
    directory = tmp_path / "Broken_P1999Green.ini"
    directory.mkdir()
    errors = push_socials([directory], [Social(page=1, button=1, name="X", lines=["/x"])])
    assert len(errors) == 1
    assert errors[0].startswith("Broken_P1999Green.ini: ")


def test_copy_socials_replicates_to_targets(tmp_path: Path) -> None:
    source = _ini(tmp_path)
    target = _ini(tmp_path, name="Beeta_P1999Green.ini", text="[Socials]\nPage9Button9Name=Old\n")
    assert copy_socials(source, [target]) == []
    grid = read_socials(target)
    assert [s.slot for s in grid.socials] == [(1, 1), (2, 3)]
    assert grid.at(9, 9) is None  # clear_missing defaults to True for copy


def test_copy_socials_can_filter_pages(tmp_path: Path) -> None:
    source = _ini(tmp_path)
    target = _ini(tmp_path, name="Beeta_P1999Green.ini", text="[Socials]\n")
    assert copy_socials(source, [target], pages=[2]) == []
    assert [s.slot for s in read_socials(target).socials] == [(2, 3)]


# -- Placement ---------------------------------------------------------------


def _grid(*socials: Social) -> SocialGrid:
    return SocialGrid(socials=list(socials))


def test_place_socials_exact_reports_conflicts() -> None:
    existing = _grid(Social(page=1, button=1, name="Mine", lines=["/mine"]))
    incoming = [
        Social(page=1, button=1, name="Theirs", lines=["/theirs"]),
        Social(page=1, button=2, name="Fits", lines=["/fits"]),
    ]
    result = place_socials(incoming, existing, strategy=Placement.EXACT)
    assert [s.name for s in result.conflicts] == ["Theirs"]
    assert [s.slot for s in result.placed] == [(1, 2)]


def test_place_socials_free_fills_empty_slots_in_order() -> None:
    existing = _grid(Social(page=1, button=1, name="Mine", lines=["/mine"]))
    incoming = [
        Social(page=5, button=5, name="A", lines=["/a"]),
        Social(page=5, button=6, name="B", lines=["/b"]),
    ]
    result = place_socials(incoming, existing, strategy=Placement.FREE)
    assert [s.slot for s in result.placed] == [(1, 2), (1, 3)]
    assert result.conflicts == [] and result.unplaceable == []


def test_place_socials_free_overflow_is_unplaceable() -> None:
    full = _grid(
        *[
            Social(page=p, button=b, name=f"{p}-{b}", lines=["/x"])
            for p in range(1, DEFAULT_PAGES + 1)
            for b in range(1, DEFAULT_BUTTONS + 1)
        ]
    )
    result = place_socials(
        [Social(page=1, button=1, name="Extra", lines=["/e"])], full, strategy=Placement.FREE
    )
    assert result.placed == []
    assert [s.name for s in result.unplaceable] == ["Extra"]


def test_place_socials_never_exceeds_the_grid() -> None:
    existing = _grid()
    off_grid = Social(page=99, button=99, name="Far", lines=["/far"])
    result = place_socials([off_grid], existing, strategy=Placement.EXACT)
    assert result.placed == [] and result.conflicts == []
    assert [s.name for s in result.unplaceable] == ["Far"]


# -- Duplicates --------------------------------------------------------------


def test_find_duplicates_detects_identical() -> None:
    socials = [
        Social(page=1, button=1, name="Assist", lines=["/assist"]),
        Social(page=2, button=1, name="assist", lines=[" /assist "]),
    ]
    groups = find_duplicates(socials)
    assert len(groups) == 1
    assert groups[0].kind is DuplicateKind.IDENTICAL
    assert [s.slot for s in groups[0].socials] == [(1, 1), (2, 1)]


def test_find_duplicates_detects_same_name_and_same_body() -> None:
    socials = [
        Social(page=1, button=1, name="Pull", lines=["/shout pulling"]),
        Social(page=1, button=2, name="Pull", lines=["/say pulling"]),
        Social(page=2, button=1, name="Sit", lines=["/sit"]),
        Social(page=2, button=2, name="Rest", lines=["/sit"]),
    ]
    kinds = {group.kind for group in find_duplicates(socials)}
    assert kinds == {DuplicateKind.SAME_NAME, DuplicateKind.SAME_BODY}


def test_find_duplicates_assigns_each_slot_to_one_group() -> None:
    # "Assist" appears three times; two are byte-identical. The identical pair
    # claims its slots first, so the third only forms a same-name group if it
    # has company — here it does not, so it is left out entirely.
    socials = [
        Social(page=1, button=1, name="Assist", lines=["/assist"]),
        Social(page=1, button=2, name="Assist", lines=["/assist"]),
        Social(page=1, button=3, name="Assist", lines=["/target"]),
    ]
    groups = find_duplicates(socials)
    assert len(groups) == 1
    assert groups[0].kind is DuplicateKind.IDENTICAL
    assert [s.slot for s in groups[0].socials] == [(1, 1), (1, 2)]


def test_find_duplicates_ignores_empty_and_clean_grids() -> None:
    assert find_duplicates([]) == []
    assert find_duplicates([Social(page=1, button=1, name="", lines=[])]) == []
    unique = [
        Social(page=1, button=1, name="A", lines=["/a"]),
        Social(page=1, button=2, name="B", lines=["/b"]),
    ]
    assert find_duplicates(unique) == []


def test_find_duplicates_does_not_group_name_only_macros_by_empty_body() -> None:
    socials = [
        Social(page=1, button=1, name="One", lines=[]),
        Social(page=1, button=2, name="Two", lines=[]),
    ]
    assert find_duplicates(socials) == []
