"""Log filename parsing — ports EQtoolsTests/LogFileNameTests.cs."""

from nparseplus.core.enums import Server
from nparseplus.core.logfile import parse_log_filename, server_from_log_token


def test_green_log_filename() -> None:
    char, token = parse_log_filename("eqlog_Vasanle_P1999Green.txt")
    assert char == "Vasanle"
    assert server_from_log_token(token) is Server.GREEN


def test_blue_log_filename() -> None:
    char, token = parse_log_filename("eqlog_Vasanle_project1999.txt")
    assert char == "Vasanle"
    assert server_from_log_token(token) is Server.BLUE


def test_red_log_filename() -> None:
    _char, token = parse_log_filename("eqlog_Grimrot_P1999PVP.txt")
    assert server_from_log_token(token) is Server.RED


def test_unknown_token_defaults_to_blue_like_csharp() -> None:
    # ActivePlayerInfo.cs:45 — anything not PVP/Green is Blue.
    assert server_from_log_token("SomeEmuServer") is Server.BLUE


def test_non_log_filename_rejected() -> None:
    assert parse_log_filename("dbg.txt") is None
    assert parse_log_filename("eqlog_Vasanle.txt") is None
