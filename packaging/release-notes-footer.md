<!--
Appended verbatim to every GitHub release body by .github/workflows/release.yml.

It lives here rather than in a changelog entry because it is not news about a
version — it is what somebody standing on the download page needs to know, and
that is true of every release (#122). Keep it short; the long form belongs in
the docs it links to.
-->

---

### Windows: SmartScreen and antivirus warnings

The Windows build is **not code-signed yet**
([#19](https://github.com/prokopto-dev/nparse-plus/issues/19)), so SmartScreen
shows "Windows protected your PC" on first launch — **More info → Run anyway**.

Some antivirus engines (AVG and Avast most often) also flag PyInstaller-packaged
apps under a generic name like `Win64:Evo-gen`. **It is a false positive**: every
PyInstaller build shares one small C launcher, malware authors use the tool too,
and engines match on those bytes. nParse+ compiles its own launcher in CI rather
than shipping the stock one, which helps but cannot settle it on its own. How to
verify the download, restore the file, and report it to the vendor:
[Troubleshooting → Antivirus flagged the
download](https://prokopto-dev.github.io/nparse-plus/latest/troubleshooting/#antivirus-flagged-the-download).

### Verifying a download

Every asset above has a sha256 published by GitHub itself — see `assets[].digest`
in the [release
API](https://api.github.com/repos/prokopto-dev/nparse-plus/releases/latest) and
compare it with `Get-FileHash` (Windows) or `shasum -a 256` (macOS/Linux). That
proves the transfer was clean; it is not a signature.
