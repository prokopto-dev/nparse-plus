"""``nparseplus-plugin`` — developer CLI for nParse+ plugin authors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nparseplus_sdk import SDK_VERSION
from nparseplus_sdk.validate import NOT_A_GUARANTEE, validate_plugin


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_plugin(Path(args.path), app_version=args.app_version)
    if args.json:
        payload = {
            "path": str(report.path),
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
            "meta": report.meta.model_dump() if report.meta else None,
            "windows": report.window_count,
            "settings_pages": report.page_count,
            "parsers": report.parser_count,
            "subscriptions": report.subscription_count,
            "ticks": report.tick_count,
        }
        print(json.dumps(payload, indent=2))
        return 0 if report.ok else 1

    if report.meta is not None:
        m = report.meta
        print(f"{m.name} ({m.id}) v{m.version} — requires SDK {m.requires_sdk}")
    print(
        f"registered: {report.window_count} window(s), {report.page_count} settings "
        f"page(s), {report.parser_count} parser(s), {report.subscription_count} "
        f"subscription(s), {report.tick_count} tick(s)"
    )
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.warnings:
        print(f"\nAdvisory findings ({NOT_A_GUARANTEE})")
        for warning in report.warnings:
            print(f"  warning: {warning}")
    print(f"\n{'PASS' if report.ok else 'FAIL'} (validated against SDK {SDK_VERSION})")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nparseplus-plugin",
        description="Developer tools for nParse+ plugins.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser(
        "validate",
        help="check that a plugin loads correctly (plus advisory static checks)",
    )
    validate.add_argument("path", help="plugin .py file or package directory")
    validate.add_argument(
        "--app-version",
        default=None,
        help="also check the plugin's min_app_version against this app version",
    )
    validate.add_argument("--json", action="store_true", help="machine-readable output")
    validate.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
