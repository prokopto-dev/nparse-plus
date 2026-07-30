"""Plugin validation — the engine behind ``nparseplus-plugin validate``.

Two layers, kept deliberately distinct:

- **Load-correctness (errors)**: the plugin imports the way the host imports
  it, exposes ``create_plugin``, has valid metadata, is compatible with this
  SDK (and optionally a given app version), and ``activate`` succeeds against
  a :class:`~nparseplus_sdk.testing.FakePluginContext` with well-formed
  window/settings-page specs. These gate the exit code.

- **Advisory static scan (warnings)**: an AST pass flagging patterns that
  *can* be abused (``exec``/``eval``, subprocess, raw sockets, file
  deletion, ctypes). Plugins are ordinary in-process Python — this scan is
  a courtesy heads-up for reviewers, NOT a security guarantee, and never
  fails validation on its own.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from nparseplus_sdk import SDK_VERSION
from nparseplus_sdk.compat import check_compat
from nparseplus_sdk.loading import PluginLoadError, load_plugin_factory
from nparseplus_sdk.plugin import PLUGIN_ID_RE, PluginMeta
from nparseplus_sdk.testing import FakePluginContext

NOT_A_GUARANTEE = (
    "advisory only, not a security guarantee: static checks cannot prove a "
    "plugin is safe. Plugins run with the full permissions of nParse+ — only "
    "install code you trust."
)

_SUSPECT_IMPORTS = {
    "subprocess": "spawns external processes",
    "ctypes": "calls native code directly",
    "socket": "opens raw network sockets (plugins should use ctx.submit + ctx.pigparse/httpx)",
    "urllib.request": "performs raw HTTP (prefer ctx.submit with the provided clients)",
    "requests": "performs raw HTTP (prefer ctx.submit with the provided clients)",
}

_SUSPECT_CALLS = {
    "exec": "executes dynamically built code",
    "eval": "evaluates dynamically built code",
    "compile": "compiles dynamically built code",
    "os.system": "runs shell commands",
    "os.popen": "runs shell commands",
    "os.remove": "deletes files",
    "os.unlink": "deletes files",
    "os.rmdir": "deletes directories",
    "shutil.rmtree": "recursively deletes directories",
    "__import__": "imports modules dynamically",
}


@dataclass
class ValidationReport:
    path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: PluginMeta | None = None
    window_count: int = 0
    page_count: int = 0
    parser_count: int = 0
    subscription_count: int = 0
    tick_count: int = 0

    @property
    def ok(self) -> bool:
        """Load-correctness verdict; warnings never fail validation."""
        return not self.errors


def _call_name(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return f"{fn.value.id}.{fn.attr}"
    return None


def scan_source(source: str, filename: str) -> list[str]:
    """Advisory AST scan of one file; returns warning strings."""
    warnings: list[str] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [f"{filename}: could not parse for static scan: {exc}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                reason = _SUSPECT_IMPORTS.get(alias.name)
                if reason:
                    warnings.append(f"{filename}:{node.lineno}: imports {alias.name} — {reason}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module
            reason = _SUSPECT_IMPORTS.get(root)
            if reason:
                warnings.append(f"{filename}:{node.lineno}: imports {root} — {reason}")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name is not None:
                reason = _SUSPECT_CALLS.get(name)
                if reason:
                    warnings.append(f"{filename}:{node.lineno}: calls {name}() — {reason}")
    return warnings


def scan_plugin_sources(path: Path) -> list[str]:
    """Run the advisory scan over every .py file that makes up the plugin."""
    path = Path(path)
    files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    warnings: list[str] = []
    for file in files:
        if any(part.startswith(".") for part in file.parts):
            continue
        try:
            source = file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"{file}: unreadable for static scan: {exc}")
            continue
        warnings.extend(scan_source(source, str(file)))
    return warnings


def validate_plugin(path: Path, *, app_version: str | None = None) -> ValidationReport:
    """Validate the plugin at ``path`` (a .py file or package directory)."""
    path = Path(path)
    report = ValidationReport(path=path)

    try:
        factory = load_plugin_factory(path)
    except PluginLoadError as exc:
        report.errors.append(str(exc))
        return report
    except Exception as exc:
        report.errors.append(f"importing the plugin failed: {exc!r}")
        return report

    try:
        plugin = factory()
    except Exception as exc:
        report.errors.append(f"create_plugin() raised: {exc!r}")
        return report

    meta = getattr(plugin, "meta", None)
    if meta is None:
        report.errors.append("plugin object has no .meta (a nparseplus_sdk.PluginMeta)")
        return report
    try:
        meta = PluginMeta.model_validate(meta, from_attributes=True)
    except Exception as exc:
        report.errors.append(f"plugin metadata is invalid: {exc}")
        return report
    report.meta = meta

    reason = check_compat(meta, sdk_version=SDK_VERSION, app_version=app_version)
    if reason is not None:
        report.errors.append(f"incompatible: {reason}")

    ctx = FakePluginContext(meta)
    try:
        plugin.activate(ctx)
    except Exception as exc:
        report.errors.append(f"activate() raised against a fake context: {exc!r}")
        return report

    seen_keys: set[str] = set()
    for spec in ctx.windows:
        if not PLUGIN_ID_RE.match(spec.key):
            report.errors.append(f"window key {spec.key!r} must match {PLUGIN_ID_RE.pattern}")
        if spec.key in seen_keys:
            report.errors.append(f"duplicate window key {spec.key!r}")
        seen_keys.add(spec.key)
        if not callable(spec.factory):
            report.errors.append(f"window {spec.key!r} factory is not callable")
    for page in ctx.settings_pages:
        if not callable(page.builder):
            report.errors.append(f"settings page {page.title!r} builder is not callable")
        if page.apply is not None and not callable(page.apply):
            report.errors.append(f"settings page {page.title!r} apply is not callable")

    report.window_count = len(ctx.windows)
    report.page_count = len(ctx.settings_pages)
    report.parser_count = len(ctx.parsers)
    report.subscription_count = len(ctx.subscriptions)
    report.tick_count = len(ctx.ticks)

    try:
        plugin.deactivate()
    except Exception as exc:
        report.warnings.append(f"deactivate() raised: {exc!r}")

    report.warnings.extend(scan_plugin_sources(path))
    return report
