"""Shared fixtures for parser tests."""

from collections.abc import Callable

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.lineinfo import LineInfo, parse_line
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.player import ActivePlayer

_PREFIX = "[Wed Jul 08 21:59:36 2026] "


@pytest.fixture
def ctx() -> ParseContext:
    return ParseContext(bus=EventBus(), player=ActivePlayer())


@pytest.fixture
def make_line() -> Callable[..., LineInfo]:
    def _make(message: str, line_number: int = 1) -> LineInfo:
        info = parse_line(_PREFIX + message, line_number)
        assert info is not None
        return info

    return _make


@pytest.fixture
def spy(ctx: ParseContext) -> Callable[[type], list]:
    def _spy(event_type: type) -> list:
        events: list = []
        ctx.bus.subscribe(event_type, events.append)
        return events

    return _spy
