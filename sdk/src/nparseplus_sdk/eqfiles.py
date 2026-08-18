"""EQ install-file plumbing, re-exported from the running nParse+ host.

Anything that edits a file inside the user's EverQuest directory — a
character ``.ini``, ``eqhost.txt`` — owes that user the same three
guarantees the app gives itself: check it really *is* an install before
writing, keep a pristine copy of the first version seen, and leave every
byte outside the edited section alone. That cycle lives in the host's
``nparseplus.core.eqini`` and is re-exported here so a plugin follows the
convention instead of reinventing it.

Pair it with :attr:`~nparseplus_sdk.PluginContext.eq_dir` for the directory
and :meth:`~nparseplus_sdk.PluginContext.eq_is_running` for the "restart EQ
for this to take effect" warning an install edit owes the user.

The typical edit is read → splice one section → write::

    from nparseplus_sdk import eqfiles

    path = ctx.eq_dir / "eqhost.txt"
    reason = eqfiles.preflight(ctx.eq_dir)
    if reason is not None:
        raise ValueError(reason)
    eqfiles.backup_once(path, "myplugin_backup")
    newline = eqfiles.detect_newline(path)
    lines = eqfiles.replace_section(
        eqfiles.read_lines(path), "LoginServer", ["Host=127.0.0.1:5998"]
    )
    eqfiles.write_lines(path, lines, newline=newline)

``backup_once`` keeps only the *first* backup, so re-applying never
overwrites the pristine original with an already-modified copy — which is
what makes a revert to that copy meaningful.

Like ``nparseplus_sdk.events``, these names are forwarded lazily to the host
at attribute access, so importing this module never pulls Qt and the SDK
keeps no install-time dependency on the app. The forwarded set is an
explicit allowlist (``EXPORTS``): the additive-only 1.x promise covers the
names chosen here, not everything that happens to live in the host module.
"""

from __future__ import annotations

from typing import Any

#: The host names this module forwards. Anything else raises AttributeError.
EXPORTS = frozenset(
    {
        "NULL_SENTINEL",
        "backup_once",
        "detect_newline",
        "preflight",
        "read_lines",
        "replace_section",
        "section_body",
        "section_bounds",
        "split_key_value",
        "write_lines",
    }
)

_HOST_HINT = (
    "nparseplus_sdk.eqfiles re-exports the host app's EQ install-file helpers "
    "and needs nparseplus importable. Inside nParse+ this always works; for "
    "standalone development install the app from source: "
    "pip install git+https://github.com/prokopto-dev/nparse-plus"
)


def _host_eqini() -> Any:
    try:
        from nparseplus.core import eqini
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(_HOST_HINT) from exc
    return eqini


def __getattr__(name: str) -> Any:
    if name not in EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(_host_eqini(), name)


def __dir__() -> list[str]:
    return sorted(EXPORTS | {"EXPORTS"})
