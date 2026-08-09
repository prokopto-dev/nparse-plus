"""net.p99planner — the p99planner.com import API client."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from nparseplus.core.p99planner import MAX_FILE_BYTES, MAX_REQUEST_BYTES, ImportFile
from nparseplus.net.p99planner import BASE_URL, P99PlannerClient, check_budget

FILES = [ImportFile(name="Wermule-Inventory.txt", text="Location\tName\tID\tCount\tSlots\n")]

OK_BODY = {
    "ok": True,
    "token": "7f3c9a2e8b1d40561e9c4a0f2b8d6e31",
    "url": "https://p99planner.com/import/7f3c9a2e8b1d40561e9c4a0f2b8d6e31",
    "expires": "2026-08-09T18:22:00.000Z",
    "files": 1,
}


def client_for(handler, **kwargs) -> P99PlannerClient:
    transport = httpx.MockTransport(handler)
    return P99PlannerClient(
        client=httpx.Client(transport=transport, base_url=BASE_URL),
        sleep=lambda _s: None,
        **kwargs,
    )


def test_stage_posts_the_files_and_returns_the_claim() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    outcome = client_for(handler).stage(FILES)

    assert outcome.ok and not outcome.gone
    assert outcome.link.token == OK_BODY["token"]
    assert outcome.link.url == OK_BODY["url"]
    assert outcome.link.files == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/api/import"
    import json

    body = json.loads(request.content)
    assert body == {"files": [{"name": FILES[0].name, "text": FILES[0].text}]}
    # No auth of any kind: that is the API's design, not an oversight.
    assert "authorization" not in {key.lower() for key in request.headers}


def test_add_puts_to_the_same_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    outcome = client_for(handler).add("abc123", FILES)

    assert outcome.ok
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/api/import/abc123"


def test_410_reports_gone_rather_than_retrying() -> None:
    """A claimed or expired link cannot be retried into working — the caller
    has to mint a new one, so this must be distinguishable from a failure."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(410, json={"error": "gone"})

    outcome = client_for(handler).add("abc123", FILES)

    assert outcome.gone is True
    assert not outcome.ok
    assert calls == 1  # not retried


def test_502_is_retried_once_then_reported() -> None:
    """The API docs call 502 "staging failed — retry"."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, json={"error": "staging failed"})

    outcome = client_for(handler).stage(FILES)

    assert calls == 2  # initial + one retry
    assert not outcome.ok and not outcome.gone
    assert outcome.error


def test_a_transient_failure_that_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=OK_BODY)

    assert client_for(handler).stage(FILES).ok


def test_400_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "malformed body"})

    outcome = client_for(handler).stage(FILES)
    assert calls == 1
    assert "malformed" in outcome.error


def test_network_failure_degrades_to_an_outcome() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    outcome = client_for(handler).stage(FILES)
    assert not outcome.ok
    assert "could not reach" in outcome.error


def test_a_reply_missing_url_or_token_is_not_a_claim() -> None:
    for body in ({"ok": True}, {"ok": True, "token": "x"}, {"ok": True, "url": "y"}):
        outcome = client_for(lambda _r, b=body: httpx.Response(200, json=b)).stage(FILES)
        assert not outcome.ok
        assert outcome.error


def test_non_json_reply_is_not_a_claim() -> None:
    outcome = client_for(lambda _r: httpx.Response(200, text="<html>nope</html>")).stage(FILES)
    assert not outcome.ok


def test_expires_becomes_a_naive_local_datetime() -> None:
    """The whole pipeline compares naive datetimes — never introduce tz-aware."""
    outcome = client_for(lambda _r: httpx.Response(200, json=OK_BODY)).stage(FILES)
    expires = outcome.link.expires
    assert isinstance(expires, datetime)
    assert expires.tzinfo is None


def test_a_bad_expires_is_dropped_not_fatal() -> None:
    body = dict(OK_BODY, expires="whenever")
    outcome = client_for(lambda _r: httpx.Response(200, json=body)).stage(FILES)
    assert outcome.ok
    assert outcome.link.expires is None


def test_release_deletes_and_swallows_failure() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = client_for(handler)
    client.release("abc123")
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/api/import/abc123"

    client.release("")  # nothing to release
    assert len(seen) == 1

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client_for(boom).release("abc123")  # must not raise


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ([], ""),
        ([ImportFile(name="a.txt", text="x" * 100)], ""),
        ([ImportFile(name="big.txt", text="x" * (MAX_FILE_BYTES + 1))], "per-file limit"),
        (
            [ImportFile(name=f"m{i}.txt", text="x" * 100_000) for i in range(20)],
            "per-request limit",
        ),
    ],
)
def test_budget_is_checked_locally(files, expected) -> None:
    """Better a clear local message than a round trip that returns 413."""
    reason = check_budget(files)
    assert (expected in reason) if expected else (reason == "")


def test_an_oversized_file_never_reaches_the_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=OK_BODY)

    huge = [ImportFile(name="big.txt", text="x" * (MAX_FILE_BYTES + 1))]
    outcome = client_for(handler).stage(huge)

    assert calls == 0
    assert "per-file limit" in outcome.error


def test_the_request_budget_is_the_documented_one() -> None:
    assert MAX_FILE_BYTES == 128 * 1024
    assert MAX_REQUEST_BYTES == 1_500_000


def test_empty_upload_is_refused_without_a_call() -> None:
    outcome = client_for(lambda _r: httpx.Response(500)).stage([])
    assert "nothing to upload" in outcome.error


def test_add_without_a_token_is_refused_without_a_call() -> None:
    outcome = client_for(lambda _r: httpx.Response(500)).add("", FILES)
    assert not outcome.ok
