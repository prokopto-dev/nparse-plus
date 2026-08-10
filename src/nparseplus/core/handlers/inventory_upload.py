"""InventoryUploadHandler — send a fresh character dump to the chosen site.

Descendant of EQTool's Services/InventoryWatcherService.cs, restructured. The
C# service is a FileSystemWatcher that both *notices* the dump and *uploads*
it to pigparse.org. Here noticing is somebody else's job —
:class:`~nparseplus.core.dumps.DumpWatcher` polls the EQ directory once for
the whole app — and this decides where the result goes. One watcher tick over
the EQ directory, one parse, two consumers.

The trigger is that watcher's ``on_fresh_dump`` hook, **not** the
``CharacterDump*`` bus events. Those describe local history ("a snapshot was
stored"), which is what a plugin wants and is the wrong question here; see
:meth:`InventoryUploadHandler.on_fresh_dump` for the two bugs that
conflating them caused.

**One destination at a time** (``dumps.upload_target``), because both
destinations publish the same character to a different website and picking
is a decision the user should make once, in one control:

``pigparse``
    The EQTool behavior: POST the parsed items to pigparse.org's character
    browser under a Bearer token from the Discord login. **Inventory only** —
    pigparse.org has no spellbook endpoint, so a spellbook dump stays in the
    local library.

``p99planner``
    A handoff, not an upload. POST the *raw export text* — no credentials of
    any kind — and get back a claim URL the player opens and approves. We
    hold that claim open and PUT later exports into it, so a five-mule
    session is one link, not five. Takes **both** dump kinds; a character's
    inventory and spellbook are reviewed as one entry. See ``core.p99planner``
    for why the URL is treated as a secret.

Which kinds each destination accepts is :data:`UPLOAD_KINDS`, and it is the
one place that asymmetry is written down — the window and the automatic path
both route through it rather than each carrying their own copy of "pigparse
means inventory".

Three behaviors that differ from the C# on purpose:

* **Stale dumps.** The old watcher primed its mtimes at startup so a dump
  from a previous session could not upload. The library deliberately does not
  prime (collecting what is already there is the point of a library), so the
  gate moved here and got stated directly: only dumps *captured* after this
  session started upload automatically. Same intent, and truer — it keys on
  when the player took the dump rather than on which files happened to exist
  at launch. A manual upload ignores this entirely; that is what manual means.
* **Re-running /outputfile with nothing changed** no longer re-uploads: the
  watcher compares content digests, so an identical dump is not fresh.
* **Whose inventory it is.** The C# uploads under the *active* character; the
  dump names the character its file belongs to, which is the same character
  in the normal case and correct in the case where it isn't. The server still
  comes from ActivePlayer — a P99 dump filename carries none, and only
  pigparse needs one.

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
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.inventory import InventoryItem
from nparseplus.core.p99planner import ClaimLink, ImportFile, P99PlannerApi
from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer

logger = logging.getLogger(__name__)


#: What each destination can actually take. pigparse.org's character browser
#: has an inventory endpoint and nothing for spellbooks; p99planner classifies
#: a file by its contents and accepts either.
#:
#: Kept as data, and looked up rather than branched on, so that adding a
#: destination (or a third dump kind) does not mean hunting for every
#: ``is DumpKind.INVENTORY`` in the app.
UPLOAD_KINDS: dict[str, frozenset[DumpKind]] = {
    "pigparse": frozenset({DumpKind.INVENTORY}),
    "p99planner": frozenset({DumpKind.INVENTORY, DumpKind.SPELLBOOK}),
}


def accepts(target: str, kind: DumpKind) -> bool:
    """Whether ``target`` takes dumps of this kind (False for "off"/unknown)."""
    return kind in UPLOAD_KINDS.get(target, frozenset())


def export_filename(dump: CharacterDump) -> str:
    """The name the game would have written, which is what p99planner reads
    the character out of — an export named anything else creates a duplicate
    character instead of updating the one the player already has.

    ``DumpKind.label`` is "Inventory"/"Spellbook", which is exactly the
    client's own spelling, so this stays one line for both kinds.
    """
    return f"{dump.character}-{dump.kind.label}.txt"


class InventoryUploadHandler(BaseHandler):
    """Routes dump snapshots to whichever site the user picked."""

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

    # -- the automatic path ------------------------------------------------

    def on_fresh_dump(self, dump: CharacterDump) -> None:
        """The EQ directory just produced a dump with new content.

        Wired to ``DumpWatcher(on_fresh_dump=...)``, deliberately NOT to the
        ``CharacterDump*`` bus events. Those mean "the library stored a
        snapshot" — a fact about local history — and using them as the upload
        trigger coupled two unrelated things in both directions:

        * ``auto_update`` off suppressed the event for a changed dump, so a
          single stale snapshot imported at startup silenced every upload for
          the rest of the session. Retention is a local choice and must not
          decide what leaves the machine.
        * A hand-picked ``Import file…`` published the same event, so filing
          away a backup — or another player's dump — sent it straight to
          pigparse or p99planner under their character's name.

        Runs on the driver thread, like everything else off the watcher tick.
        """
        if not dump.entry_count:
            return
        if not accepts(self._get_target(), dump.kind):
            # A spellbook with pigparse.org picked: kept locally, sent nowhere.
            return
        if dump.captured_at < self._session_start:
            return  # taken before we started; see the module docstring
        self.upload_now([dump])

    # -- the manual path ---------------------------------------------------

    def upload_now(self, dumps: list[CharacterDump]) -> str:
        """Send these snapshots to the configured site.

        Takes a mixed list and filters it against :data:`UPLOAD_KINDS`, so the
        caller never has to know that pigparse.org does not do spellbooks —
        the window hands over what the selection means and gets back a line
        saying what happened to it.

        Safe from any thread: the work happens inside a ``submit`` fetch.
        Returns a one-line description of what was *started*, for a status
        label — the outcome lands in :meth:`status_text` when it completes.
        """
        target = self._get_target()
        if target == "off":
            return "Dump upload is off — pick a destination in Settings > Sharing."
        if target not in UPLOAD_KINDS:
            return f"Unknown upload destination {target!r}."
        usable = [dump for dump in dumps if accepts(target, dump.kind) and dump.entry_count]
        if not usable:
            return _nothing_to_send(target, dumps)
        if self.submit is None:
            return "The network worker is not running."

        if target == "pigparse":
            return self._start_pigparse(usable)
        return self._start_planner(usable)

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
        names = ", ".join(_characters(dumps))
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
        files = _planner_files(dumps)
        names = ", ".join(_characters(dumps))
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
        if not self._try_open(link.url):  # type: ignore[union-attr]
            # Do NOT send them back to Review import…, which is the same
            # browser call that just failed. Copying the link is the path
            # that still works on a machine with no usable default browser.
            self._set_status(
                "p99planner: staged, but no browser would open — right-click "
                "Review import… and choose Copy review link."
            )
            return
        self._set_status(_staged_status(link))

    def _try_open(self, url: str) -> bool:
        """Hand ``url`` to a browser; True only if one actually took it.

        ``webbrowser.open`` **returns False** when it has nowhere to go — it
        does not raise. That is the normal outcome on a headless box, a
        locked-down desktop, or EQ under Wine with no browser registered,
        which is precisely the situation Copy review link exists for. Treating
        only the raising case as failure left the recovery hint unreachable in
        the common case.

        ``is False`` rather than falsiness on purpose: an injected opener that
        returns None is not reporting a failure.
        """
        try:
            opened = self._open_browser(url)
        except Exception:
            logger.warning("could not open the p99planner review page in a browser")
            self._browser_ok = False
            return False
        if opened is False:
            logger.warning("no browser was available for the p99planner review page")
            self._browser_ok = False
            return False
        self._browser_ok = True  # a browser that works again clears the hint
        return True

    # -- claim state -------------------------------------------------------

    def _current_claim(self) -> ClaimLink | None:
        """The live claim, or None when there is none or it has expired.

        Deliberately a pure read: the GUI thread calls this (via
        ``claim_summary`` / ``has_claim``) on every status repaint, and the
        module contract is that claim state moves only on the net worker.
        An expired claim is simply reported as absent — the next send
        overwrites it, so nothing has to clear it here.
        """
        with self._lock:
            claim = self._claim
        if claim is None:
            return None
        if claim.expires is not None and datetime.now() >= claim.expires:
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
        """Re-open the pending review page.

        False when there is no claim, or when no browser took the URL — see
        :meth:`_try_open` for why the latter is not just the raising case.
        """
        url = self.claim_url()
        if not url:
            return False
        return self._try_open(url)

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


def _planner_files(dumps: list[CharacterDump]) -> list[ImportFile]:
    """The batch to hand p99planner, **inventory before spellbook** per character.

    A spellbook only applies to a character the planner already has, so an
    ordering that puts one ahead of the inventory that would create that
    character is the one arrangement that can turn a good batch into a
    half-skipped one. Grouping by character in the order they arrived keeps
    the review page reading like the roster the user selected.
    """
    order = {DumpKind.INVENTORY: 0, DumpKind.SPELLBOOK: 1}
    seen: dict[str, int] = {}
    for dump in dumps:
        seen.setdefault(dump.character, len(seen))
    ranked = sorted(dumps, key=lambda dump: (seen[dump.character], order.get(dump.kind, 9)))
    return [ImportFile(name=export_filename(dump), text=render_dump_text(dump)) for dump in ranked]


def _characters(dumps: list[CharacterDump]) -> list[str]:
    """The character names in these dumps, deduped, in order.

    A character sending both an inventory and a spellbook is two dumps and
    one name; the status line should not say "Xantik, Xantik".
    """
    names: list[str] = []
    for dump in dumps:
        if dump.character and dump.character not in names:
            names.append(dump.character)
    return names


def _nothing_to_send(target: str, dumps: list[CharacterDump]) -> str:
    """Why the selection produced nothing this destination could take.

    Worth distinguishing: "you have no snapshots" and "this site does not do
    spellbooks" are different problems with different fixes, and the second
    one is new — before spellbook support, every dump the button could reach
    was one pigparse would take.
    """
    if target == "pigparse" and any(dump.kind is DumpKind.SPELLBOOK for dump in dumps):
        return (
            "pigparse.org takes inventory dumps only — switch to p99planner.com "
            "in Settings > Sharing to upload a spellbook."
        )
    return "No snapshot to upload."


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
