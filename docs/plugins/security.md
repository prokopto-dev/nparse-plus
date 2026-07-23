# Plugin security & trust

## The trust model, plainly

An nParse+ plugin is ordinary Python code that runs **inside the app, with
the same permissions as the app** — it can read and write your files, use
your network, and do anything else nParse+ could do. Python offers no real
in-process sandbox, and nParse+ does not pretend to provide one.

That means the security model is **trust in the author**, supported by
guardrails:

- **Nothing loads silently.** A plugin never seen before triggers a consent
  dialog (name, version, author, where it came from) before it is activated,
  and your answer is remembered. Declining keeps it installed but inert.
- **Per-plugin disable + kill switch.** Any plugin can be disabled in
  Settings > Plugins; `NPARSEPLUS_NO_PLUGINS=1` skips all plugin loading.
- **Failure isolation.** A plugin that crashes — at import, activation, in
  an event handler, or in its window — is contained and logged; it cannot
  take the app down or block other plugins.
- **Careful installs.** The in-app installer refuses unsafe archives
  (path traversal, symlinks, oversize bombs) and validates that the plugin
  loads before enabling it.
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

## One honest caveat

To read a plugin's name and version, nParse+ has to import its module —
which executes the module's top-level code. So for a plugin you copied into
the plugins folder yourself, a small amount of its code runs *before* the
consent dialog. Treat *installing* a plugin as the trust decision, not the
consent click; the dialog exists so nothing you didn't knowingly install
can quietly start doing work. (A declarative metadata file that removes
this caveat is on the roadmap.)

## Practical advice

- Prefer plugins with public source you (or someone you trust) can read.
- Be suspicious of plugins that ask for your account credentials — nothing
  in the plugin API needs them.
- If a plugin misbehaves, disable it, grab `nparseplus.log`, and report it
  to the author — and to the
  [nParse+ issue tracker](https://github.com/prokopto-dev/nparse-plus/issues)
  if it circumvented the guardrails above.
