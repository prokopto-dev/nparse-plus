# Plugin security & trust

## The trust model, plainly

An nParse+ plugin is ordinary Python code that runs **inside the app, with
the same permissions as the app** — it can read and write your files, use
your network, and do anything else nParse+ could do. Python offers no real
in-process sandbox, and nParse+ does not pretend to provide one.

That means the security model is **trust in the author**, supported by
guardrails:

- **The whole subsystem is opt-in.** `settings.plugins.enabled` defaults to
  `False`. Until you tick *Settings > Advanced > Enable plugins (add-ons)*
  and restart, no plugin is discovered, imported, or run — the plugin
  machinery is not even imported by the app. If you never wanted add-ons,
  you carry none of their risk.
- **Nothing loads silently.** A plugin never seen before triggers a consent
  dialog (name, version, author, where it came from) before it is activated,
  and your answer is remembered. Declining keeps it installed but inert.
- **Per-plugin disable + kill switch.** Any plugin can be disabled in
  Settings > Plugins; `NPARSEPLUS_NO_PLUGINS=1` skips all plugin loading.
  The environment variable is a veto only — it can force plugins off, never
  on.
- **Failure isolation.** A plugin that crashes — at import, activation, in
  an event handler, in a parser, or in its window — is contained and logged;
  it cannot take the app down or block other plugins.
- **A plugin cannot freeze the app indefinitely.** Periodic callbacks
  registered with `ctx.add_tick` are timed by the log driver and evicted
  after two consecutive runs over 250 ms (`core/driver.py`). The rest of the
  plugin keeps running and Settings > Plugins annotates the row *tick
  disabled (too slow)*. This is a robustness guardrail, not a security
  boundary — a plugin can still block inside an event handler.
- **Careful installs.** The in-app installer refuses unsafe archives (path
  traversal, absolute paths, symlink members, member floods) and enforces a
  size cap on the bytes *actually written* during extraction, not just the
  sizes the archive declares. It validates that the plugin loads before
  moving it into place.
- **Hardened downloads.** URL and registry installs are https-only and
  re-assert https on **every redirect hop** — an https link that bounces to
  http is refused rather than quietly downloaded in the clear. The response
  body is streamed and aborted the moment it passes the size budget, so an
  endless response can't be buffered whole before anyone checks its length.
- **Advisory static scan.** Installing (and `nparseplus-plugin validate`)
  runs a source scan that flags patterns worth a second look: `exec`/`eval`,
  spawning processes, raw sockets or HTTP outside the provided clients,
  file deletion, native code via `ctypes`.

!!! warning "What the scan is NOT"
    The static scan is a courtesy heads-up, **not a security guarantee**.
    Malicious code can trivially evade static analysis, and plenty of
    legitimate code trips these patterns. A "clean" scan does not mean a
    plugin is safe; a warning does not mean it is malicious. The decision
    that matters is whether you trust the author.

## Where the checksum applies (and where it doesn't)

There are three install channels and they do not offer the same protection:

| Channel | What is verified |
| --- | --- |
| *Browse registry…* | https + size cap + **sha256 pinned by the index that listed it**, checked before extraction and before any of the plugin's code runs |
| *Install from URL…* | https on every hop + size cap. **No hash** — there is nothing to compare against |
| *Install from file…* | The bytes you chose. Their hash is recorded as provenance, not checked against anything |

For a URL install the URL *is* the trust decision. If the host is
compromised, or the author replaces the release asset, you get the new bytes
with no warning. That is the gap the [registry](registry.md) exists to
close.

### …and who supplied the checksum

A pinned hash answers "are these the bytes that registry meant?" — never
"is this code safe?", and never "was whoever listed it entitled to?" The
same document supplies the download URL *and* the hash it is checked
against, so the guarantee is only ever as good as the registry it came
from.

That is true of the built-in catalogue as much as any other. It is served
by a live registry server (<https://nparseplugins.prokopto.dev/index.json>)
rather than the static file it began as, and that changes nothing about
where the boundary sits: the app fetches over https, re-asserts it on every
redirect hop, and checks the bytes against the digest that document carries.
What a host can do is serve a different document; what it cannot do is make
nParse+ accept an artifact that does not hash to what the document says.

That matters because the registry list is yours to extend: nParse+ ships
with one built-in catalogue and merges in any you add under **Settings >
Plugins > Plugin registries**. Adding one is a trust decision a level above
installing a plugin — it decides which add-ons you are ever offered, and
everything from it arrives pre-verified. The app confirms it with a warning
that defaults to Cancel, marks third-party sources in the Browse table,
records which registry vouched for each installed plugin, and refuses to
treat another registry's build of the same plugin id as an update to yours.
The full argument, the merged-browse behaviour, and why the built-in row
can be unticked but never deleted are in
[Using another registry](registry.md#using-another-registry).

## Consent runs late — two honest caveats

**Reading a plugin's metadata means importing it.** `PluginMeta` lives
inside the plugin's own module, so nParse+ has to execute that module's
top-level code before it can tell you what the plugin claims to be. For a
plugin you copied into the plugins folder yourself, that import happens
during discovery, *before* the consent dialog. Consent gates `activate()` —
it does not gate the import.

**Installing runs more than the import.** The installer validates a
candidate with the SDK's `validate_plugin`, which imports the plugin **and
calls `activate()`** against a fake context
(`sdk/src/nparseplus_sdk/validate.py`). So by the time an install succeeds,
the plugin has already had its module body and its activation path executed
once — on a worker thread, against a context wired to nothing, but executed.

Treat **installing** a plugin as the trust decision, not the consent click.
The dialog's job is narrower than it looks: it stops something you didn't
knowingly install from quietly registering handlers and doing work every
session. (A declarative metadata file read before any import would close the
first caveat; it is on the roadmap.)

## A version bump does not re-ask

Consent is recorded against the plugin's **id**, not its version. When a
plugin's `meta.version` changes, `PluginHost._load_one` updates the recorded
`last_version` and loads it — no new dialog. An author you approved once can
ship arbitrary new code under the same id and it runs on the next launch.

That is a deliberate trade (a prompt on every patch release trains people to
click through), but it means your trust decision is in the *author*, not in
the particular build you looked at. Taking an update from Settings > Plugins
follows the same rule and keeps your consent — that is what makes it an
update rather than a fresh install. If you want to re-evaluate, uninstall
and reinstall: uninstalling forgets the consent record, so the reinstall
asks again.

**What does re-ask is a change of *source*.** If an update is offered by a
registry other than the one that supplied your copy — or by any source at
all, when nParse+ has no record of where your copy came from — you get a
confirmation naming both ends before anything is downloaded. Same plugin id,
different publisher, possibly unrelated code: that is a new trust decision,
not a version bump, and the app refuses to let it be one click.

Note also that installing *or updating* runs the candidate's module-level
code and its `activate()` during validation, before the swap. That is the
same trust boundary installing has always had, but on the update path it
means the new version executes once before you have run it — a reason to
care who is publishing, not only what the version number says.

## Self-published update feeds

A plugin may declare `PluginMeta.update_url`, an index the app polls for
that add-on's own releases. It exists so an add-on distributed outside any
registry can still be updated in place. What it is *not* is a review:

- The author supplies both the artifact URL and the sha256 it is checked
  against. The pin still works — a tampered download is refused — but it
  proves the bytes match what that author published, and nothing more. A
  registry pin at least means someone else chose the hash.
- A feed can only ever offer the id of the plugin that declared it. Any
  other listing in that document is discarded, so one add-on cannot publish
  "updates" for another.
- A feed never appears in **Browse registry…**. It updates something you
  already installed; it does not advertise anything new.
- If your copy came from a registry, that registry's offer wins, and the
  feed's offer counts as a source change (see above).
- Feeds are only polled for add-ons that are approved **and** enabled, and
  not at all if you untick *Check for plugin updates shortly after launch*.
  Bear in mind that when the box is ticked, a request goes to a URL the
  author chose each time nParse+ starts — which reveals your IP and roughly
  when you play. If that matters to you, untick it and check by hand.

## What uninstalling actually removes

*Uninstall* moves the plugin's code into `plugins/trash/`, then
`PluginHost.forget` deletes its entry from `settings.json` and moves
`plugin-data/<id>/` into `plugins/trash/plugin-data/`. Nothing is deleted
outright — you can recover a mistake — but nothing live survives either.

The reason is a consent bypass that would otherwise exist: if the approval
record outlived the code, the next thing to claim that plugin id, from any
source, would load pre-approved and inherit the previous plugin's stored
data. It no longer can.

## Practical advice

- Prefer plugins with public source you (or someone you trust) can read.
- Prefer registry installs over raw URLs when a plugin is listed — the hash
  pins the bytes a human reviewed.
- Keep the registry list short. Every extra registry is another party that
  can offer you code; untick or remove any you no longer have a reason to
  trust (plugins already installed from it stay installed, and the Source
  column keeps naming it).
- Be suspicious of plugins that ask for your account credentials — nothing
  in the plugin API needs them.
- If a plugin misbehaves, disable it, grab `nparseplus.log`, and report it
  to the author — and to the
  [nParse+ issue tracker](https://github.com/prokopto-dev/nparse-plus/issues)
  if it circumvented the guardrails above.
