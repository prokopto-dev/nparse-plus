"""Log filename parsing — ports EQtoolsTests/LogFileNameTests.cs — and the tail."""

from pathlib import Path

import pytest

from nparseplus.core.enums import Server
from nparseplus.core.logfile import LogTail, parse_log_filename, server_from_log_token


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


# -- tailing -----------------------------------------------------------------


def _line(n: int) -> str:
    return f"[Wed Jul 15 21:00:00 2026] line {n}\n"


def _padded_to(text: str, size: int) -> str:
    """`text` grown to exactly `size` bytes, still newline-terminated."""
    assert len(text) <= size
    if len(text) == size:
        return text
    return text + "x" * (size - len(text) - 1) + "\n"


def test_attach_starts_after_the_last_session_marker(tmp_path: Path) -> None:
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text(_line(1) + "[Wed Jul 15 21:00:01 2026] Welcome to EverQuest!\n" + _line(2))
    tail = LogTail.attach(log)
    # History before the marker is not replayed; what follows it is.
    assert tail.poll() == [_line(2).rstrip("\n")]


def test_poll_returns_only_what_is_new(tmp_path: Path) -> None:
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text(_line(1))
    tail = LogTail.attach(log)
    assert tail.poll() == []

    with log.open("a") as f:
        f.write(_line(2))
    assert tail.poll() == [_line(2).rstrip("\n")]
    assert tail.poll() == []


def test_poll_holds_a_partial_line_until_it_terminates(tmp_path: Path) -> None:
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text(_line(1))
    tail = LogTail.attach(log)

    with log.open("a") as f:
        f.write("[Wed Jul 15 21:00:02 2026] half a li")
    assert tail.poll() == []
    with log.open("a") as f:
        f.write("ne\n")
    assert tail.poll() == ["[Wed Jul 15 21:00:02 2026] half a line"]


def test_poll_restarts_when_the_file_shrinks(tmp_path: Path) -> None:
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text("".join(_line(n) for n in range(50)))
    tail = LogTail.attach(log)

    log.write_text(_line(99))
    assert tail.poll() == [_line(99).rstrip("\n")]


def test_poll_restarts_when_the_file_regrew_past_our_offset(tmp_path: Path) -> None:
    """The archive sweep empties the log from another thread (see #87).

    If the client refills it past our old offset before the next 100 ms poll,
    the file is no longer *smaller* than our position — resuming there would
    silently skip every line from offset 0 up to it.
    """
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text("".join(_line(n) for n in range(200)))
    tail = LogTail.attach(log)
    was = tail.position
    assert was > 0

    refilled = "".join(_line(n) for n in range(1000, 1400))
    assert len(refilled) > was  # regrown past where we left off
    log.write_text(refilled)

    lines = tail.poll()
    assert lines[0] == _line(1000).rstrip("\n")  # nothing skipped at the top
    assert lines[-1] == _line(1399).rstrip("\n")
    assert len(lines) == 400


def test_poll_restarts_when_the_file_refilled_to_exactly_our_offset(tmp_path: Path) -> None:
    """The size can come back to precisely where we left off, at which point
    it is not evidence of anything — hence the check on every poll."""
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text("".join(_line(n) for n in range(200)))
    tail = LogTail.attach(log)
    assert tail.poll() == []

    body = _line(1000)
    while len(body) + len(_line(1001)) < tail.position:
        body += _line(1001)
    refilled = _padded_to(body, tail.position)
    assert len(refilled) == tail.position  # same length, different content
    log.write_text(refilled)

    lines = tail.poll()
    assert lines[0] == _line(1000).rstrip("\n")  # nothing skipped at the top
    assert tail.poll() == []  # and we are caught up again, not looping


def test_poll_is_quiet_while_the_file_does_not_change(tmp_path: Path) -> None:
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text("".join(_line(n) for n in range(20)))
    tail = LogTail.attach(log)
    at = tail.position
    for _ in range(5):
        assert tail.poll() == []
    assert tail.position == at


def test_poll_survives_a_shrink_between_the_stat_and_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The sweep truncates on its own thread, so it can land mid-poll: the
    # stat sees the old size and the read finds an emptied file.
    log = tmp_path / "eqlog_Tanky_P1999Green.txt"
    log.write_text("".join(_line(n) for n in range(50)))
    tail = LogTail.attach(log)
    with log.open("a") as f:
        f.write(_line(51))

    real_open = Path.open

    def truncate_then_open(self: Path, *args: object, **kwargs: object):
        monkeypatch.setattr(Path, "open", real_open)  # once, and not re-entrant
        if self == log:
            log.write_text(_line(77))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", truncate_then_open)
    assert tail.poll() == [_line(77).rstrip("\n")]
    assert tail.poll() == []
