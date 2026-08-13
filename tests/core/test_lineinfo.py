"""core.lineinfo — the timestamp split every log line goes through."""

import locale
from collections.abc import Iterator
from datetime import datetime

import pytest

from nparseplus.core import lineinfo
from nparseplus.core.lineinfo import parse_line, parse_timestamp

LINE = "[Wed Jul 15 12:00:00 2026] You begin casting Clarity."
STAMP = datetime(2026, 7, 15, 12, 0, 0)


def test_parses_the_logs_own_clock() -> None:
    info = parse_line(LINE, 7)
    assert info is not None
    assert info.timestamp == STAMP
    assert info.message == "You begin casting Clarity."
    assert info.line_number == 7
    assert info.timestamp.tzinfo is None  # naive local, everywhere


def test_strips_a_leading_bom() -> None:
    info = parse_line("﻿" + LINE, 1)
    assert info is not None
    assert info.timestamp == STAMP


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no brackets here at all, just a long line of text",
        "[Wed Jul 15 12:00:00 2026] ",
        "[Wed Jul 15 12:00:00 2026",
    ],
)
def test_rejects_lines_without_a_stamp_and_a_message(raw: str) -> None:
    assert parse_line(raw, 1) is None


def test_malformed_stamp_falls_back_to_now() -> None:
    # Deliberate tolerance, ported from EQTool: a corrupt line still parses.
    fallback = datetime(2026, 1, 1, 3, 4, 5)
    info = parse_line("[not a timestamp here!!] You have entered Kael Drakkel.", 1, now=fallback)
    assert info is not None
    assert info.timestamp == fallback


@pytest.mark.parametrize(
    "stamp",
    [
        "Wed Jul 15 12:00:00 2026",
        "Sun Feb 29 23:59:59 2032",  # a real leap day
    ],
)
def test_parse_timestamp_accepts_real_stamps(stamp: str) -> None:
    assert parse_timestamp(stamp) is not None


@pytest.mark.parametrize(
    "stamp",
    [
        "Wed Jul 15 12:00:00 202",  # too short
        "Wed Jul 15 12:00:00 20265",  # too long
        "Wed Zzz 15 12:00:00 2026",  # not a month
        "Wed Jul 15 12-00-00 2026",  # wrong separators
        "Wed Jul xx 12:00:00 2026",  # non-numeric
        "Wed Feb 31 12:00:00 2026",  # not a real date
    ],
)
def test_parse_timestamp_rejects_junk(stamp: str) -> None:
    assert parse_timestamp(stamp) is None


class _NoStrptime(datetime):
    """A datetime that refuses the locale-sensitive parse."""

    @classmethod
    def strptime(cls, *args: object, **kwargs: object) -> datetime:  # pragma: no cover
        raise AssertionError("parse_line must not go through strptime")


def test_strptime_is_not_on_the_hot_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lineinfo, "datetime", _NoStrptime)
    info = parse_line(LINE, 1)
    assert info is not None
    assert info.timestamp == STAMP


@pytest.fixture
def restore_lc_time() -> Iterator[None]:
    previous = locale.setlocale(locale.LC_TIME)
    try:
        yield
    finally:
        locale.setlocale(locale.LC_TIME, previous)


@pytest.mark.parametrize("name", ["de_DE.UTF-8", "fr_FR.UTF-8", "ru_RU.UTF-8", "ja_JP.UTF-8"])
def test_parses_under_a_non_english_lc_time(name: str, restore_lc_time: None) -> None:
    # %a/%b are locale-dependent; the EQ client always writes English. Under
    # strptime this returned the wall clock for every line in the session.
    try:
        locale.setlocale(locale.LC_TIME, name)
    except locale.Error:
        pytest.skip(f"locale {name} is not installed on this runner")
    info = parse_line(LINE, 1)
    assert info is not None
    assert info.timestamp == STAMP
