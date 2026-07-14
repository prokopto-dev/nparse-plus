from nparseplus.core.events import LoadingPleaseWaitEvent
from nparseplus.core.parsers.loading_please_wait import LoadingPleaseWaitParser


def test_loading(ctx, make_line, spy):
    events = spy(LoadingPleaseWaitEvent)
    parser = LoadingPleaseWaitParser()
    assert parser.handle(make_line("LOADING, PLEASE WAIT..."), ctx)
    assert len(events) == 1


def test_non_loading(ctx, make_line, spy):
    events = spy(LoadingPleaseWaitEvent)
    parser = LoadingPleaseWaitParser()
    assert not parser.handle(make_line("LOADING"), ctx)
    assert events == []
