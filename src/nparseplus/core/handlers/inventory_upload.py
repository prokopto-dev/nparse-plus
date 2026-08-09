"""InventoryUploadHandler — send a fresh inventory dump to the chosen site.

Descendant of EQTool's Services/InventoryWatcherService.cs, restructured
twice. The C# service is a FileSystemWatcher that both *notices* the dump and
*uploads* it to pigparse.org. Here noticing is somebody else's job —
:class:`~nparseplus.core.dumps.DumpWatcher` polls the EQ directory once for
the whole app — and this is a bus subscriber that uploads what the library
just took in. One watcher tick over the EQ directory, one parse, and this
module deciding where the result goes.

**One destination at a time** (``dumps.upload_target``), because both
destinations publish the same character to a different website and picking
is a decision the user should make once, in one control:

``pigparse``
    The EQTool behavior: POST the parsed items to pigparse.org's character
    browser under a Bearer token from the Discord login.

``p99planner``
    A handoff, not an upload. POST the *raw export text* — no credentials of
    any kind — and get back a claim URL the player opens and approves. We
    hold that claim open and PUT later exports into it, so a five-mule
    session is one link, not five. See ``core.p99planner`` for why the URL is
    treated as a secret.

Three behaviors that differ from the C# on purpose:

* **Stale dumps.** The old watcher primed its mtimes at startup so a dump
  from a previous session could not upload. The library deliberately does not
  prime (collecting what is already there is the point of a library), so the
  gate moved here and got stated directly: only dumps *captured* after this
  session started upload automatically. Same intent, and truer — it keys on
  when the player took the dump rather than on which files happened to exist
  at launch. A manual upload ignores this entirely; that is what manual means.
* **Re-running /outputfile with nothing changed** no longer re-uploads: the
  library dedupes by content digest, so an identical dump raises no event.
* **Whose inventory it is.** The C# uploads under the *active* character; the
  event names the character the dump file belongs to, which is the same
  character in the normal case and correct in the case where it isn't. The
  server still comes from ActivePlayer — a P99 dump filename carries none,
  and only pigparse needs one.

Threading: every send runs inside a ``submit`` fetch, i.e. on the single net
worker thread. The claim state is read and written *only* there, which is
what serializes two dumps landing back to back into one POST and then a PUT
rather than two competing POSTs. :meth:`upload_now` is safe to call from the
GUI thread precisely because all it does is submit.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from collections.abc import Callable
from datetime import datetime

from nparseplus.core.bus import EventBus
from nparseplus.core.dumps import CharacterDump, DumpKind, DumpLibrary, render_dump_text
from nparseplus.core.events import (
    CharacterDumpEvent,
    CharacterDumpImportedEvent,
    CharacterDumpUpdatedEvent,
)
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.inventory import InventoryItem
from nparseplus.core.p99planner import ClaimLink, ImportFile, P99PlannerApi
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer

logger = logging.getLogger(__name__)


def export_filename(dump: CharacterDump) -> str:
    """The name the game would have written, which is what p99planner reads
    the character out of — an export named anything else creates a duplicate
    character instead of updating the one the player already has."""
    return f"{dump.character}-Inventory.txt"


class InventoryUploadHandler(BaseHandler):
    """Routes inventory snapshots to whichever site the user picked."""

    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        library: DumpLibrary,
        *,
        get_target: Callable[[], str],
        get_token: Callable[[], str],
        session_start: datetime,
        api: PigParseApi | None = None,
        planner: P99PlannerApi | None = None,
        submit: SubmitFn | None = None,
        open_browser: Callable[[str], object] = webbrowser.open,
    ) -> None:
        super().__init__(bus, player)
        self.library = library
        self._get_target = get_target
        self._get_token = get_token
        self._session_start = session_start
        self.api = api
        self.planner = planner
        self.submit = submit
        self._open_browser = open_browser
        # Claim state. Written only on the net worker thread (see the module
        # docstring); the lock is for the GUI reading `status_text`.
        self._lock = threading.Lock()
        self._claim: ClaimLink | None = None
        self._status = ""
        #: False once opening a browser has failed, so the UI can keep
        #: offering the copy-the-link route instead of a dead retry.
        self._browser_ok = True
        bus.subscribe(CharacterDumpImportedEvent, self._on_dump)
        bus.subscribe(CharacterDumpUpdatedEvent, self._on_dump)

    # -- the automatic path ------------------------------------------------

    def _on_dump(self, event: CharacterDumpEvent) -> None:
        if event.kind != str(DumpKind.INVENTORY):
            return
        if event.captured_at < self._session_start:
            return  # taken before we started; see the module docstring
        dump = self.library.load_path(event.path)
        if dump is None:
            return
        # The event names the character; the stored snapshot may not, if it
        # was hand-imported from a file with an odd name.
        self.upload_now([dump.model_copy(update={"character": event.character or dump.character})])

    # -- the manual path ---------------------------------------------------

    def upload_now(self, dumps: list[CharacterDump]) -> str:
        """Send these snapshots to the configured site.

        Safe from any thread: the work happens inside a ``submit`` fetch.
        Returns a one-line description of what was *started*, for a status
        label — the outcome lands in :meth:`status_text` when it completes.
        """
        target = self._get_target()
        usable = [dump for dump in dumps if dump.kind is DumpKind.INVENTORY and dump.items]
        if target == "off":
            return "Inventory upload is off — pick a destination in Settings > Sharing."
        if not usable:
            return "No inventory snapshot to upload."
        if self.submit is None:
            return "The network worker is not running."

        if target == "pigparse":
            return self._start_pigparse(usable)
        if target == "p99planner":
            return self._start_planner(usable)
        return f"Unknown upload destination {target!r}."

    # -- pigparse ----------------------------------------------------------

    def _start_pigparse(self, dumps: list[CharacterDump]) -> str:
        api, token, server = self.api, self._get_token(), self.player.server
        if api is None:
            return "pigparse.org is unavailable."
        if not token:
            return "Log in to pigparse.org first (Settings > Sharing)."
        if server is None:
            return "Waiting to see which server you are on."
        server_int = int(server)
        names = ", ".join(dump.character for dump in dumps)
        # Set before submitting, not after: the send can complete (and post
        # its own outcome) before this function returns, and a "starting"
        # line must never overwrite the answer.
        self._set_status(f"Uploading {names} to pigparse.org…")

        for dump in dumps:
            character, items = dump.character or self.player.name, _wire_items(dump)
            if not character:
                continue

            def fetch(character: str = character, items: list = items) -> None:
                api.upload_inventory(
                    character_name=character, server=server_int, items=items, api_token=token
                )

            self.submit(fetch, None)  # type: ignore[misc]
        return f"Uploading {names} to pigparse.org…"

    # -- p99planner --------------------------------------------------------

    def _start_planner(self, dumps: list[CharacterDump]) -> str:
        if self.planner is None:
            return "p99planner.com is unavailable."
        files = [
            ImportFile(name=export_filename(dump), text=render_dump_text(dump)) for dump in dumps
        ]
        names = ", ".join(dump.character for dump in dumps)
        # Set before submitting — see _start_pigparse.
        self._set_status(f"Staging {names} for p99planner.com…")
        self.submit(lambda: self._send_to_planner(files), None)  # type: ignore[misc]
        return f"Staging {names} for p99planner.com…"

    def _send_to_planner(self, files: list[ImportFile]) -> None:
        """Runs on the net worker thread — the only place claim state moves.

        Adds to the open claim when there is one, so the player is handed one
        URL per session rather than one per mule; mints a fresh claim when
        there is none, when the old one expired, or when the server says the
        old one is gone (410 — already approved, or swept).
        """
        planner = self.planner
        if planner is None:  # pragma: no cover - guarded by the caller
            return
        claim = self._current_claim()

        if claim is not None:
            outcome = planner.add(claim.token, files)
            if outcome.ok:
                self._store_claim(outcome.link)
                self._set_status(_added_status(outcome.link))
                return
            if not outcome.gone:
                self._set_status(f"p99planner: {outcome.error}")
                return
            # Claimed or expired: fall through and mint a new one.
            self._store_claim(None)

        outcome = planner.stage(files)
        if not outcome.ok:
            self._set_status(f"p99planner: {outcome.error}")
            return
        link = outcome.link
        self._store_claim(link)
        # Open it rather than print it: the URL is a bearer secret, and the
        # player has to visit it anyway to approve the import.
        try:
            self._open_browser(link.url)  # type: ignore[union-attr]
        except Exception:
            self._browser_ok = False
            logger.warning("could not open the p99planner review page in a browser")
            # Do NOT send them back to Review import…, which is the same
            # webbrowser call that just failed. Copying the link is the path
            # that still works on a machine with no usable default browser.
            self._set_status(
                "p99planner: staged, but no browser would open — right-click "
                "Review import… and choose Copy review link."
            )
            return
        self._browser_ok = True  # a browser that works again clears the hint
        self._set_status(_staged_status(link))

    # -- claim state -------------------------------------------------------

    def _current_claim(self) -> ClaimLink | None:
        with self._lock:
            claim = self._claim
        if claim is None:
            return None
        if claim.expires is not None and datetime.now() >= claim.expires:
            self._store_claim(None)
            return None
        return claim

    def _store_claim(self, link: ClaimLink | None) -> None:
        with self._lock:
            self._claim = link

    def claim_url(self) -> str:
        """The open review URL, or "". Handled as a secret — the UI opens it,
        and nothing logs it."""
        claim = self._current_claim()
        return claim.url if claim is not None else ""

    def has_claim(self) -> bool:
        """Whether a staged import is still waiting to be approved.

        The predicate the UI asks, so that showing a Review button never
        requires handing the URL to a widget that might render it.
        """
        return self._current_claim() is not None

    def claim_summary(self) -> str:
        """A describable form of the pending claim — never the URL itself.

        Carries the recovery hint while the browser is refusing to open,
        because this line is the steady state the UI shows: a one-shot
        "could not open a browser" message would be replaced by it within
        the second, leaving the user stuck with no visible way forward.
        """
        claim = self._current_claim()
        if claim is None:
            return ""
        count = claim.files
        what = f"{count} export{'s' if count != 1 else ''}"
        summary = f"{what} waiting for approval at p99planner.com"
        if claim.expires is not None:
            summary += f" (link expires {claim.expires:%a %H:%M})"
        if not self._browser_ok:
            return (
                f"{summary} — no browser would open; right-click "
                "Review import… and choose Copy review link."
            )
        return summary + "."

    def open_claim(self) -> bool:
        """Re-open the pending review page. False when there isn't one."""
        url = self.claim_url()
        if not url:
            return False
        try:
            self._open_browser(url)
        except Exception:
            self._browser_ok = False
            logger.warning("could not open the p99planner review page in a browser")
            return False
        self._browser_ok = True
        return True

    def forget_claim(self) -> None:
        """Release the staged copy and drop the link (the user cancelling)."""
        claim = self._current_claim()
        self._store_claim(None)
        if claim is not None and self.planner is not None and self.submit is not None:
            self.submit(lambda: self.planner.release(claim.token), None)  # type: ignore[misc,union-attr]
        self._set_status("p99planner handoff cancelled.")

    # -- status ------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        with self._lock:
            self._status = text

    def status_text(self) -> str:
        """One line for the Character Dumps window. Never contains the URL."""
        with self._lock:
            return self._status


def _staged_status(link: ClaimLink | None) -> str:
    if link is None:  # pragma: no cover - defensive
        return "p99planner: staged."
    return (
        f"p99planner: {link.files} export(s) staged — approve the import in your browser. "
        "Later dumps join the same link."
    )


def _added_status(link: ClaimLink | None) -> str:
    if link is None:  # pragma: no cover - defensive
        return "p99planner: added to the open link."
    return f"p99planner: added to the open review link ({link.files} export(s) waiting)."


def _wire_items(dump: CharacterDump) -> list[InventoryItem]:
    """The stored snapshot back in the shape the pigparse REST client sends."""
    return [
        InventoryItem(
            location=entry.location,
            name=entry.name,
            item_id=entry.item_id,
            count=entry.count,
            slots=entry.slots,
        )
        for entry in dump.items
    ]
