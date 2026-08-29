"""tools/convert_item_clickies.py — the failure path that must not write.

A partial scrape is the dangerous outcome: it looks exactly like a successful
run against a smaller wiki, so nothing downstream can tell a timed-out batch
from a genuine shrink. These tests are offline; the network is a fake.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_converter():
    path = REPO_ROOT / "tools" / "convert_item_clickies.py"
    spec = importlib.util.spec_from_file_location("convert_item_clickies", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


converter = _load_converter()


class _Response:
    def __init__(self, payload: dict, status: int = 200, headers: dict | None = None) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = RuntimeError(f"HTTP {self.status_code}")
            error.response = self  # what _retry_after_s reads
            raise error

    def json(self) -> dict:
        return self._payload


def _page_payload(titles: list[str]) -> dict:
    return {
        "query": {
            "pages": {
                str(i): {"title": t, "revisions": [{"*": f"content for {t}"}]}
                for i, t in enumerate(titles)
            }
        }
    }


class _Client:
    """Fails its first ``fail_times`` requests, then answers normally.

    Counted per REQUEST, not per batch: a retry is another request, which is
    exactly the axis these tests need to exercise.
    """

    def __init__(self, fail_times: int = 0, status: int = 500, retry_after: str | None = None):
        self.fail_times = fail_times
        self.status = status
        self.retry_after = retry_after
        self.calls = 0
        self.failures = 0

    def get(self, url, params=None):
        self.calls += 1
        if self.failures < self.fail_times:
            self.failures += 1
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return _Response({}, status=self.status, headers=headers)
        titles = (params or {}).get("titles", "").split("|")
        return _Response(_page_payload(titles))


def test_walk_retries_a_failing_batch_and_recovers(monkeypatch) -> None:
    monkeypatch.setattr(converter.time, "sleep", lambda _s: None)
    client = _Client(fail_times=converter.MAX_ATTEMPTS - 1)
    pages = converter._walk(client, ["A", "B"], "spells")
    assert set(pages) == {"A", "B"}
    assert client.calls == converter.MAX_ATTEMPTS  # two refusals then the answer


def test_walk_raises_when_a_batch_never_lands(monkeypatch) -> None:
    monkeypatch.setattr(converter.time, "sleep", lambda _s: None)
    client = _Client(fail_times=converter.MAX_ATTEMPTS)
    with pytest.raises(converter.ScrapeIncomplete) as excinfo:
        converter._walk(client, ["A", "B"], "spells")
    assert "failed after" in str(excinfo.value)
    # It gave up rather than retrying forever, and nothing was returned.
    assert client.calls == converter.MAX_ATTEMPTS


def test_a_429_is_retried_using_retry_after(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(converter.time, "sleep", lambda s: slept.append(s))
    client = _Client(fail_times=1, status=429, retry_after="7")
    converter._walk(client, ["A"], "spells")
    # 7 is the server's number; our own backoff would have been 2.0.
    assert 7.0 in slept


def test_refresh_does_not_write_when_the_scrape_is_incomplete(monkeypatch, tmp_path) -> None:
    """The whole point: the committed table survives a failed refresh."""
    target = tmp_path / "item_clickies.json"
    original = {"meta": {}, "clickies": {"Levitate": 30}, "sources": {}}
    target.write_text(json.dumps(original), encoding="utf-8")

    def _dies(limit=None):
        raise converter.ScrapeIncomplete("batch 7 died")

    monkeypatch.setattr(converter, "OUTPUT_PATH", target)
    monkeypatch.setattr(converter, "scrape", _dies)
    monkeypatch.setattr(sys, "argv", ["convert_item_clickies.py", "--refresh"])

    with pytest.raises(SystemExit) as excinfo:
        converter.main()

    assert excinfo.value.code != 0
    assert "left unchanged" in str(excinfo.value.code)
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_check_rejects_a_table_that_lost_most_of_its_entries() -> None:
    """The backstop behind ScrapeIncomplete: a short table is not a valid one.

    Before this, --check only required the table to be non-empty, so a
    partial scrape that still wrote could not be caught after the fact.
    """
    document = json.loads(converter.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert converter.validate(document) == []
    assert len(document["clickies"]) >= converter.MIN_EXPECTED_SPELLS

    gutted = {**document, "clickies": dict(list(document["clickies"].items())[:5])}
    problems = converter.validate(gutted)
    assert any("partial scrape" in p for p in problems), problems
