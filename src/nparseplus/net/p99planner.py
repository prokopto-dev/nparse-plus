"""p99planner.com import API client (Qt-free).

Same shape as :mod:`nparseplus.net.pigparse_api`: sync httpx with an
injectable client, callers run on the net worker thread, and failures
degrade to a reported outcome rather than an exception.

Two differences from that client, both forced by this API:

* **410 is not a failure to retry.** It means the claim link was approved or
  expired, and the caller must mint a fresh one. So the methods return an
  :class:`UploadOutcome` carrying ``gone`` instead of a bare ``None``.
* **Nothing here is logged with a token in it.** The claim URL is a bearer
  secret (see ``core.p99planner``); the log lines below name the operation
  and the status code and never the path, body, or response.

There is no authentication of any kind — that is the design, not an
oversight. The player approves the import in their own browser.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from nparseplus.core.p99planner import (
    MAX_FILE_BYTES,
    MAX_REQUEST_BYTES,
    ClaimLink,
    ImportFile,
    UploadOutcome,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://p99planner.com"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
ATTEMPTS = 2  # initial call + one retry
RETRY_DELAY_S = 0.5
# 502 is called out by the API docs as "staging failed — retry".
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
CLAIM_GONE_STATUS = 410


def check_budget(files: list[ImportFile]) -> str:
    """Why this batch would be rejected (HTTP 413), or "" if it fits.

    Checked here rather than left to the server so an oversized export fails
    with something a user can act on, and so we do not spend a round trip
    learning what we could have computed.
    """
    for file in files:
        if file.size > MAX_FILE_BYTES:
            return f"{file.name} is larger than the {MAX_FILE_BYTES // 1024} KB per-file limit"
    total = sum(file.size for file in files)
    if total > MAX_REQUEST_BYTES:
        return (
            f"{len(files)} files total {total // 1024} KB, over the "
            f"{MAX_REQUEST_BYTES // 1024} KB per-request limit"
        )
    return ""


class P99PlannerClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=TIMEOUT,
            # NOT follow_redirects. The claim token rides in the URL path and
            # there is no auth, so a redirect is a hop that could carry the
            # capability somewhere else — an http:// target would put it, and
            # the export, on the wire in clear. The endpoint is a fixed host
            # that does not redirect; if it ever starts, failing loudly is the
            # right answer, not following along. (Same instinct as the plugin
            # installer re-asserting https on every hop.)
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "nparseplus"},
        )
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- the three calls ---------------------------------------------------

    def stage(self, files: list[ImportFile]) -> UploadOutcome:
        """POST /api/import — mint a claim link for these exports."""
        return self._send("POST", "api/import", files, what="stage")

    def add(self, token: str, files: list[ImportFile]) -> UploadOutcome:
        """PUT /api/import/:token — add to an already-handed-over link."""
        if not token:
            return UploadOutcome(error="no claim to add to")
        return self._send("PUT", f"api/import/{token}", files, what="add")

    def release(self, token: str) -> None:
        """DELETE /api/import/:token — cancel a handoff early.

        Deleting an unknown token succeeds by design; the caller's goal is
        "it's gone", so nothing here reports failure either.
        """
        if not token:
            return
        try:
            self._client.request("DELETE", f"{self._base}/api/import/{token}")
        except Exception:
            logger.debug("p99planner release failed", exc_info=True)

    # -- plumbing ----------------------------------------------------------

    def _send(self, method: str, path: str, files: list[ImportFile], *, what: str) -> UploadOutcome:
        if not files:
            return UploadOutcome(error="nothing to upload")
        reason = check_budget(files)
        if reason:
            return UploadOutcome(error=reason)

        body = {"files": [{"name": file.name, "text": file.text} for file in files]}
        url = f"{self._base}/{path}"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                resp = self._client.request(method, url, json=body)
                resp.raise_for_status()
                return self._parse(resp, what)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == CLAIM_GONE_STATUS:
                    # Approved or expired: a retry cannot help, a new claim can.
                    return UploadOutcome(gone=True, error="that claim link is no longer valid")
                if status not in RETRYABLE_STATUS or attempt == ATTEMPTS:
                    # Deliberately no path/body/response in the log: the path
                    # carries the claim token.
                    logger.warning("p99planner %s failed (HTTP %s)", what, status)
                    return UploadOutcome(error=_status_reason(status))
            except httpx.RequestError:
                if attempt == ATTEMPTS:
                    logger.warning("p99planner %s failed (network)", what)
                    return UploadOutcome(error="could not reach p99planner.com")
            except Exception:
                logger.warning("p99planner %s failed", what)
                return UploadOutcome(error="upload failed")
            self._sleep(RETRY_DELAY_S)
        return UploadOutcome(error="upload failed")

    def _parse(self, resp: httpx.Response, what: str) -> UploadOutcome:
        try:
            data = resp.json()
        except ValueError:
            logger.warning("p99planner %s returned a non-JSON body", what)
            return UploadOutcome(error="p99planner sent an unreadable reply")
        if not isinstance(data, dict) or not data.get("url") or not data.get("token"):
            logger.warning("p99planner %s reply was missing url/token", what)
            return UploadOutcome(error="p99planner sent an unreadable reply")
        url = str(data["url"])
        if not url.startswith(f"{self._base}/"):
            # This URL is handed to a browser. Anything the server returns
            # that is not on the host we asked does not get opened — a
            # file://, javascript: or third-party link here would be a nasty
            # way to turn a dump upload into something else.
            logger.warning("p99planner %s returned a claim url off the expected host", what)
            return UploadOutcome(error="p99planner returned an unexpected review link")
        return UploadOutcome(
            link=ClaimLink(
                token=str(data["token"]),
                url=url,
                expires=_parse_expires(data.get("expires")),
                files=int(data.get("files") or 0),
            )
        )


def _status_reason(status: int) -> str:
    """A message for the user. Never includes anything token-shaped."""
    if status == 413:
        return "that export is too large for p99planner"
    if status == 400:
        return "p99planner rejected the export as malformed"
    return f"p99planner returned HTTP {status}"


def _parse_expires(raw: object) -> datetime | None:
    """The API sends UTC ISO-8601 with a ``Z``; we keep naive local, like the
    rest of the pipeline (which compares naive datetimes everywhere)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone().replace(tzinfo=None)
