"""Qt-free typing surface for the p99planner.com import API.

Same split as :mod:`nparseplus.core.pigparse`: core depends on this Protocol
and the DTOs below, never on ``nparseplus.net``; the implementation is
``net.p99planner.P99PlannerClient``.

**The model is a handoff, not an upload.** Your tool POSTs the raw
``/outputfile`` text and gets back a *claim URL*; the player opens it, sees
exactly what would change, and approves. Nothing is applied without that.
Which is why there are no credentials here — the endpoint is anonymous, and
the token IS the capability.

That last part matters: a claim URL is a bearer secret for one player's
character data. **Never log it, never put it in an error message, never
publish it to chat.** Prefer opening it in the player's browser over showing
it. The exposure if it leaks is bounded (one export batch, 24 hours, and
opening the link burns it), but it is still theirs, not ours.

**Both dump kinds ride in the same ``files`` array.** An inventory export and
a spellbook export are not labelled and there is no ``kind`` field — the
review page classifies each file by its *contents*, and groups a character's
inventory and spellbook into one row. Two consequences for callers:

* A spellbook only applies to a character the planner already has, and only
  for a casting class; one for an unknown character is listed as skipped
  while the rest of the batch imports. So send a character's inventory
  first — see ``handlers.inventory_upload._planner_files``, which orders
  every batch that way.
* Nothing here needs to know which kind a file is. The name is what the
  server derives the *character* from, which is the only thing we have to
  get right.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

#: Server-side limits (HTTP 413 past either). Enforced client-side too, so a
#: too-big export fails locally with a clear reason instead of a 413.
MAX_FILE_BYTES = 128 * 1024
MAX_REQUEST_BYTES = 1_500_000

#: The API counts "a small per-file allowance" on top of name + text, without
#: saying how small. Estimated high enough to keep our budget check on the
#: conservative side of the server's — being told locally that a batch is too
#: big is a better failure than a 413 — and small enough to be noise next to
#: the 1.5 MB request budget. It matters more now that a character can send
#: two files (inventory *and* spellbook) instead of one.
PER_FILE_OVERHEAD_BYTES = 64


class ImportFile(BaseModel):
    """One ``/outputfile`` export, as the API wants it.

    ``name`` is optional to the API but we always send it: the character is
    derived from the filename, and that is what makes an export *update* a
    character the player already has instead of creating a duplicate. It is
    NOT how the server decides whether a file is an inventory or a spellbook
    — that is read out of the contents — so a misnamed file imports under the
    wrong character rather than being rejected.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    text: str

    @property
    def size(self) -> int:
        """Rough wire cost of this file, for the request budget."""
        return (
            len(self.name.encode("utf-8"))
            + len(self.text.encode("utf-8"))
            + PER_FILE_OVERHEAD_BYTES
        )


class ClaimLink(BaseModel):
    """A staged import waiting for the player to approve it.

    ``url`` is the secret described in the module docstring.
    """

    model_config = ConfigDict(frozen=True)

    token: str
    url: str
    expires: datetime | None = None
    files: int = 0

    def __str__(self) -> str:  # pragma: no cover - defensive
        # Whatever happens, a stray f-string or log call must not print the
        # token. Reaching for the URL has to be deliberate.
        return f"<ClaimLink {self.files} file(s)>"

    __repr__ = __str__


class UploadOutcome(BaseModel):
    """What a stage/add attempt did.

    Richer than the ``None``-on-failure convention the pigparse client uses,
    because one failure here is not like the others: ``gone`` (HTTP 410) means
    the claim was approved or expired, and the correct response is to mint a
    fresh one rather than retry.
    """

    model_config = ConfigDict(frozen=True)

    link: ClaimLink | None = None
    gone: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.link is not None


class P99PlannerApi(Protocol):
    def stage(self, files: list[ImportFile]) -> UploadOutcome:
        """POST /api/import — mint a claim link for these exports."""
        ...

    def add(self, token: str, files: list[ImportFile]) -> UploadOutcome:
        """PUT /api/import/:token — add to a link already handed over.

        A file whose name matches one already staged replaces it, so
        re-sending a character whose export changed again is safe.

        The size limits apply to the **merged** result, not to what this call
        sends, so a long session can fill a link that every individual batch
        fits inside. We do not track the staged total to predict it (the
        server owns that arithmetic); a 413 here is reported as the link
        being full, and approving or cancelling it starts a fresh one.
        """
        ...

    def release(self, token: str) -> None:
        """DELETE /api/import/:token — cancel a handoff early."""
        ...
