#!/usr/bin/env python3
"""Normalize, archive and render the Phase 0 benchmark results (#132).

``pytest-benchmark --benchmark-json`` writes a large, version-specific
document full of per-round timings and machine detail. None of that survives
usefully in a history file that grows one entry a night, so this tool sits
between the two:

    record   pytest-benchmark JSON -> one compact run record
    append   run record            -> the persistent history (newest last)
    compare  run record            -> a markdown regression table vs a baseline
    render   history + baseline    -> docs/development/performance.md + SVGs
    baseline run record            -> promote a run to the committed baseline

Kept deliberately dependency-free (stdlib only, SVG written by hand) for the
reason ``tools/gen_icons.py`` is: this runs in CI and in a docs job whose
environment installs the docs group and nothing else.

**Nothing here fails a build on a slow number.** CI timing is noisy enough
that ``pyproject`` already deselects the benchmark marker from the default
run; a nightly that cried wolf would be muted within a week. ``compare``
reports, and only ``--fail-over`` — which nothing passes today — would make
it exit non-zero.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = 1

# How many run records the history keeps. A year of nightlies; past that the
# trend charts are unreadable and the file is doing nothing but growing.
HISTORY_LIMIT = 365

# What counts as a regression worth naming in the page. Below this, CI noise
# and a real change are indistinguishable — the runners are shared VMs.
REGRESSION_PCT = 15.0
IMPROVEMENT_PCT = -15.0

GROUP_TITLES = {
    "bus": "EventBus.publish",
    "pipeline": "LogPipeline.process (full backend)",
    "plugin-dispatch": "Plugin subscriber dispatch",
    "plugin-parser": "Plugin parser in the chain",
    "qt-bridge": "Qt bridge (buffer + coalesced flush)",
    "maps": "Map canvas render",
    "latency": "End-to-end latency (log append -> UI slot)",
}

GROUP_NOTES = {
    "bus": (
        "The floor under everything else. `publish` snapshots both subscriber "
        "lists defensively, which is the allocation #131 Phase 1 proposes to "
        "replace with copy-on-write — the 0-subscriber row is what that would "
        "be measured against."
    ),
    "pipeline": (
        "One replay of 60 in-game seconds of traffic through the parser chain "
        "and every handler subscribed to it. Divide by the line count in the "
        "table for per-line cost; the driver thread has 100 ms per poll to "
        "absorb whatever arrived in it."
    ),
    "plugin-dispatch": (
        "The same publish, but through `HostPluginContext` — so the per-plugin "
        "try/except guard and the telemetry gate are both inside the number. "
        "`off` and `on` are collection disabled and enabled: the gap is the "
        "entire cost of #132's measurement on the dispatch path."
    ),
    "plugin-parser": (
        "A plugin parser appended after the built-in chain, over a raid burst. "
        "It never consumes a line, which is the worst case: every line reaches "
        "it."
    ),
    "qt-bridge": (
        "1000 events published from a worker thread and delivered in one "
        "coalesced GUI-thread flush. `burst` has a per-event slot connected, "
        "`batch_only` just the bulk signal."
    ),
    "maps": "The heaviest widget in the app, coalescing 120 location fixes into one render.",
    "latency": (
        "A real `LogDriver` tailing a real file, so this includes the 100 ms "
        "poll interval it is dominated by. That is the honest end-to-end "
        "figure — it is what the user waits."
    ),
}


# --- record -----------------------------------------------------------------
@dataclass
class RunRecord:
    timestamp: str
    commit: str
    ref: str
    runner: str
    python: str
    benchmarks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "timestamp": self.timestamp,
            "commit": self.commit,
            "ref": self.ref,
            "runner": self.runner,
            "python": self.python,
            "benchmarks": self.benchmarks,
        }


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return out.stdout.strip()


def normalize(raw: dict[str, Any], *, commit: str, ref: str, runner: str) -> RunRecord:
    """Reduce a pytest-benchmark document to the few numbers worth keeping.

    ``name`` is the parametrized test id, which is what makes a series
    comparable across runs — so a renamed benchmark starts a new series
    rather than silently continuing an old one. That is the right behaviour:
    the two were not measuring the same thing.
    """
    machine = raw.get("machine_info", {})
    benchmarks: dict[str, dict[str, Any]] = {}
    for entry in raw.get("benchmarks", []):
        stats = entry.get("stats", {})
        benchmarks[entry["name"]] = {
            "group": entry.get("group") or "",
            "mean": stats.get("mean", 0.0),
            "median": stats.get("median", 0.0),
            "stddev": stats.get("stddev", 0.0),
            "min": stats.get("min", 0.0),
            "max": stats.get("max", 0.0),
            "rounds": stats.get("rounds", 0),
            "extra": entry.get("extra_info", {}),
        }
    return RunRecord(
        timestamp=raw.get("datetime") or datetime.now(UTC).isoformat(timespec="seconds"),
        commit=commit or _git_commit(),
        ref=ref,
        runner=runner or machine.get("node", ""),
        python=machine.get("python_version", platform.python_version()),
        benchmarks=benchmarks,
    )


# --- history ----------------------------------------------------------------
def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    runs = data.get("runs") if isinstance(data, dict) else data
    return list(runs) if isinstance(runs, list) else []


def append_history(history: list[dict[str, Any]], run: dict[str, Any]) -> list[dict[str, Any]]:
    """Append, then trim from the FRONT so the newest run is always kept.

    A same-commit re-run replaces the previous entry rather than doubling it:
    a re-run is a correction, and two points at one commit make the trend
    chart lie about how often the suite ran.
    """
    commit = run.get("commit")
    kept = [entry for entry in history if not (commit and entry.get("commit") == commit)]
    kept.append(run)
    return kept[-HISTORY_LIMIT:]


# --- comparison -------------------------------------------------------------
@dataclass(frozen=True)
class Comparison:
    name: str
    group: str
    current: float
    baseline: float | None

    @property
    def delta_pct(self) -> float | None:
        if not self.baseline:
            return None
        return (self.current - self.baseline) / self.baseline * 100.0

    @property
    def verdict(self) -> str:
        delta = self.delta_pct
        if delta is None:
            return "new"
        if delta >= REGRESSION_PCT:
            return "slower"
        if delta <= IMPROVEMENT_PCT:
            return "faster"
        return "steady"


def compare(run: dict[str, Any], baseline: dict[str, Any]) -> list[Comparison]:
    base = baseline.get("benchmarks", {})
    out = []
    for name, entry in sorted(run.get("benchmarks", {}).items()):
        reference = base.get(name)
        out.append(
            Comparison(
                name=name,
                group=entry.get("group", ""),
                current=entry.get("mean", 0.0),
                baseline=reference.get("mean") if reference else None,
            )
        )
    return out


def _fmt_duration(seconds: float) -> str:
    if seconds >= 1.0:
        return f"{seconds:.2f} s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.2f} ms"
    if seconds >= 1e-6:
        return f"{seconds * 1e6:.2f} µs"
    return f"{seconds * 1e9:.0f} ns"


def _fmt_delta(comparison: Comparison) -> str:
    delta = comparison.delta_pct
    if delta is None:
        return "new"
    return f"{delta:+.1f}%"


def comparison_table(comparisons: list[Comparison]) -> str:
    lines = ["| Benchmark | Mean | vs baseline | |", "| --- | --- | --- | --- |"]
    marks = {"slower": "⚠️", "faster": "✅", "steady": "", "new": "🆕"}
    for item in comparisons:
        lines.append(
            f"| `{item.name}` | {_fmt_duration(item.current)} | "
            f"{_fmt_delta(item)} | {marks[item.verdict]} |"
        )
    return "\n".join(lines)


# --- charts -----------------------------------------------------------------
# Hand-written SVG: the docs job installs mkdocs and mike and nothing else,
# and a plotting dependency for six sparklines is not a trade worth making.
_CHART_W = 720
_CHART_H = 220
_PAD_L = 46
_PAD_R = 150
_PAD_T = 16
_PAD_B = 28

# Picked for legibility on both the light and dark Material palettes.
_SERIES_COLORS = (
    "#c8a45c",
    "#7fa8c9",
    "#b9846b",
    "#8fb08a",
    "#a891c0",
    "#c98f9c",
    "#89a9a3",
    "#bfae7f",
)


def _series_for_group(history: list[dict[str, Any]], group: str) -> dict[str, list[float | None]]:
    """One ratio-to-first series per benchmark in ``group``.

    Ratios rather than absolute times because a single group can span three
    orders of magnitude (a 1 µs publish beside a 40 ms replay) and one shared
    y-axis would flatten every series but the biggest. A ratio chart answers
    the question the dashboard exists for: has this moved?
    """
    names: list[str] = []
    for run in history:
        for name, entry in run.get("benchmarks", {}).items():
            if entry.get("group") == group and name not in names:
                names.append(name)
    series: dict[str, list[float | None]] = {}
    for name in sorted(names):
        firsts = [
            run["benchmarks"][name]["mean"]
            for run in history
            if name in run.get("benchmarks", {}) and run["benchmarks"][name].get("mean")
        ]
        if not firsts:
            continue
        reference = firsts[0]
        series[name] = [
            run["benchmarks"][name]["mean"] / reference
            if name in run.get("benchmarks", {})
            else None
            for run in history
        ]
    return series


def _short_label(name: str) -> str:
    """Trim the shared ``test_bench_`` prefix; keep the parameters."""
    return name.removeprefix("test_bench_")


def render_chart(history: list[dict[str, Any]], group: str) -> str:
    series = _series_for_group(history, group)
    if not series or len(history) < 2:
        return ""
    values = [value for points in series.values() for value in points if value is not None]
    top = max(1.25, math.ceil(max(values) * 4) / 4)
    bottom = min(0.75, math.floor(min(values) * 4) / 4)
    span = top - bottom or 1.0
    width = _CHART_W - _PAD_L - _PAD_R
    height = _CHART_H - _PAD_T - _PAD_B
    steps = max(len(history) - 1, 1)

    def x_at(index: int) -> float:
        return _PAD_L + width * index / steps

    def y_at(value: float) -> float:
        return _PAD_T + height * (top - value) / span

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_CHART_W} {_CHART_H}" '
        f'role="img" aria-label="{GROUP_TITLES.get(group, group)} trend, '
        f'relative to the first recorded run">',
        "<style>text{font:11px system-ui,sans-serif;fill:currentColor}"
        ".grid{stroke:currentColor;stroke-opacity:.18}"
        ".base{stroke:currentColor;stroke-opacity:.45;stroke-dasharray:4 3}</style>",
    ]
    for tick in (bottom, 1.0, top):
        y = y_at(tick)
        css = "base" if abs(tick - 1.0) < 1e-9 else "grid"
        parts.append(
            f'<line class="{css}" x1="{_PAD_L}" y1="{y:.1f}" x2="{_PAD_L + width}" y2="{y:.1f}"/>'
        )
        parts.append(f'<text x="4" y="{y + 4:.1f}">{tick:.2f}x</text>')
    for index, (name, points) in enumerate(sorted(series.items())):
        color = _SERIES_COLORS[index % len(_SERIES_COLORS)]
        drawn = [(x_at(i), y_at(v)) for i, v in enumerate(points) if v is not None]
        if len(drawn) >= 2:
            path = " ".join(
                f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(drawn)
            )
            parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.6"/>')
        legend_y = _PAD_T + 12 + index * 14
        parts.append(
            f'<rect x="{_PAD_L + width + 12}" y="{legend_y - 8}" '
            f'width="9" height="9" fill="{color}"/>'
        )
        parts.append(f'<text x="{_PAD_L + width + 26}" y="{legend_y}">{_short_label(name)}</text>')
    first = history[0].get("timestamp", "")[:10]
    last = history[-1].get("timestamp", "")[:10]
    parts.append(f'<text x="{_PAD_L}" y="{_CHART_H - 8}">{first}</text>')
    parts.append(f'<text x="{_PAD_L + width}" y="{_CHART_H - 8}" text-anchor="end">{last}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _baseline_provenance(latest: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Say what the percentages are measured against, and when they can't be.

    A benchmark mean is a fact about a machine, so a baseline recorded on
    different hardware makes every delta on the page noise. That is not
    something to bury: the nightly promotes the first run on each runner to
    be that runner's own baseline (see `performance-nightly.yml`), and until
    it has, the page has to admit what it is comparing.
    """
    if not baseline:
        return ["No baseline recorded yet — this run will become one.", ""]
    when = (baseline.get("timestamp") or "?")[:10]
    runner = baseline.get("runner") or "?"
    lines = [
        f"Percentages compare against the baseline recorded on **{runner}** "
        f"({when}, `{(baseline.get('commit') or '?')[:12]}`).",
        "",
    ]
    if latest.get("runner") and runner != latest["runner"]:
        lines[0:0] = [
            f'!!! warning "Baseline is from a different machine ({runner})"',
            "    Every percentage below is dominated by the hardware difference,",
            "    not by a code change. The first nightly run on a given runner",
            "    becomes that runner's baseline; this page predates that.",
            "",
        ]
    return lines


# --- the page ---------------------------------------------------------------
def render_page(
    history: list[dict[str, Any]],
    baseline: dict[str, Any],
    *,
    asset_dir: str,
    charts: dict[str, str],
) -> str:
    latest = history[-1] if history else {}
    comparisons = compare(latest, baseline) if latest else []
    by_group: dict[str, list[Comparison]] = {}
    for item in comparisons:
        by_group.setdefault(item.group or "ungrouped", []).append(item)

    out = [
        "# Performance",
        "",
        '!!! info "Generated page"',
        "    Written by `tools/perf_report.py render` from the nightly",
        "    benchmark run (`.github/workflows/performance-nightly.yml`) and",
        "    redeployed to the `dev` docs version. Editing it by hand is",
        "    pointless — the next nightly overwrites it.",
        "",
        "This is Phase 0 of [#131](https://github.com/prokopto-dev/nparse-plus/issues/131):",
        "the measurement the later phases are supposed to be argued from. It is a",
        "dashboard, not a gate — nothing in CI fails because a number here moved,",
        "because hosted runners are shared VMs and a nightly that cried wolf would",
        "be muted inside a week. Read it for trends, not for single runs.",
        "",
    ]
    if not latest:
        out += ["No runs recorded yet.", ""]
        return "\n".join(out)

    out += [
        "## Latest run",
        "",
        f"- **When**: {latest.get('timestamp', '?')}",
        f"- **Commit**: `{(latest.get('commit') or '?')[:12]}`"
        + (f" ({latest['ref']})" if latest.get("ref") else ""),
        f"- **Runner**: {latest.get('runner') or '?'}, Python {latest.get('python') or '?'}",
        f"- **Recorded runs in history**: {len(history)}",
        "",
    ]
    if len(history) == 1:
        out += [
            "This is the seed run committed with #132 — the trend charts appear",
            "once the nightly has recorded a second one.",
            "",
        ]
    out += _baseline_provenance(latest, baseline)

    regressions = [item for item in comparisons if item.verdict == "slower"]
    if regressions:
        out += [
            f'!!! warning "{len(regressions)} benchmark(s) more than '
            f'{REGRESSION_PCT:.0f}% slower than baseline"',
        ]
        out += [f"    - `{item.name}` ({_fmt_delta(item)})" for item in regressions]
        out += [""]

    for group in [g for g in GROUP_TITLES if g in by_group] + [
        g for g in sorted(by_group) if g not in GROUP_TITLES
    ]:
        out += [f"## {GROUP_TITLES.get(group, group)}", ""]
        note = GROUP_NOTES.get(group)
        if note:
            out += [note, ""]
        out += [comparison_table(by_group[group]), ""]
        lines_note = _lines_note(latest, by_group[group])
        if lines_note:
            out += [lines_note, ""]
        if group in charts:
            out += [
                f"![{GROUP_TITLES.get(group, group)} trend]({asset_dir}/{group}.svg)",
                "",
                "*Mean duration relative to the first recorded run; 1.00x is that run.*",
                "",
            ]

    out += [
        "## Running it yourself",
        "",
        "```bash",
        "QT_QPA_PLATFORM=offscreen uv run pytest -m benchmark --benchmark-only",
        "```",
        "",
        "Benchmarks are excluded from the default `uv run pytest` (see the",
        "`addopts` in `pyproject.toml`) because timing assertions in CI are noise.",
        "The fixtures they replay are in `tests/perf/profiles.py`: solo, group and",
        "raid traffic composed from the `EQtoolsTests` line corpus.",
        "",
    ]
    return "\n".join(out)


def _lines_note(latest: dict[str, Any], comparisons: list[Comparison]) -> str:
    """Per-line cost, for the groups whose benchmarks replay a line count."""
    rows = []
    for item in comparisons:
        entry = latest.get("benchmarks", {}).get(item.name, {})
        count = entry.get("extra", {}).get("lines")
        if not count:
            continue
        rows.append(f"`{item.name}`: {count} lines, {_fmt_duration(item.current / count)}/line")
    if not rows:
        return ""
    return "Per line: " + "; ".join(rows) + "."


# --- CLI --------------------------------------------------------------------
def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _cmd_record(args: argparse.Namespace) -> int:
    raw = _read_json(args.source)
    if not raw:
        print(f"no benchmark JSON at {args.source}")
        return 1
    record = normalize(raw, commit=args.commit, ref=args.ref, runner=args.runner)
    _write_json(args.out, record.to_json())
    print(f"{len(record.benchmarks)} benchmarks -> {args.out}")
    return 0


def _cmd_append(args: argparse.Namespace) -> int:
    run = _read_json(args.run)
    if not run:
        print(f"no run record at {args.run}")
        return 1
    history = append_history(load_history(args.history), run)
    _write_json(args.history, {"schema": SCHEMA, "runs": history})
    print(f"history now holds {len(history)} run(s) -> {args.history}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    run = _read_json(args.run)
    baseline = _read_json(args.baseline)
    comparisons = compare(run, baseline)
    print(comparison_table(comparisons))
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write("### Benchmark vs baseline\n\n")
            handle.write(comparison_table(comparisons) + "\n")
    if args.fail_over is None:
        return 0
    over = [c for c in comparisons if (c.delta_pct or 0.0) >= args.fail_over]
    if over:
        print(f"\n{len(over)} benchmark(s) over the {args.fail_over:.0f}% threshold")
        return 1
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    history = load_history(args.history)
    baseline = _read_json(args.baseline)
    charts: dict[str, str] = {}
    args.assets.mkdir(parents=True, exist_ok=True)
    groups = {
        entry.get("group", "")
        for run in history
        for entry in run.get("benchmarks", {}).values()
        if entry.get("group")
    }
    for group in sorted(groups):
        svg = render_chart(history, group)
        if not svg:
            continue
        (args.assets / f"{group}.svg").write_text(svg, encoding="utf-8")
        charts[group] = svg
    page = render_page(history, baseline, asset_dir=args.asset_url, charts=charts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"{args.out} ({len(charts)} chart(s))")
    return 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    run = _read_json(args.run)
    if not run:
        print(f"no run record at {args.run}")
        return 1
    _write_json(args.out, run)
    print(f"baseline <- {args.run}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="pytest-benchmark JSON -> run record")
    record.add_argument("source", type=Path)
    record.add_argument("--out", type=Path, required=True)
    record.add_argument("--commit", default="")
    record.add_argument("--ref", default="")
    record.add_argument("--runner", default="")
    record.set_defaults(func=_cmd_record)

    append = sub.add_parser("append", help="run record -> persistent history")
    append.add_argument("run", type=Path)
    append.add_argument("--history", type=Path, required=True)
    append.set_defaults(func=_cmd_append)

    compare_cmd = sub.add_parser("compare", help="run record vs baseline")
    compare_cmd.add_argument("run", type=Path)
    compare_cmd.add_argument("--baseline", type=Path, required=True)
    compare_cmd.add_argument("--summary", type=Path, default=None)
    compare_cmd.add_argument("--fail-over", type=float, default=None)
    compare_cmd.set_defaults(func=_cmd_compare)

    render = sub.add_parser("render", help="history -> docs page + SVG charts")
    render.add_argument("--history", type=Path, required=True)
    render.add_argument("--baseline", type=Path, required=True)
    render.add_argument("--out", type=Path, required=True)
    render.add_argument("--assets", type=Path, required=True)
    render.add_argument(
        "--asset-url",
        default="../assets/perf",
        help="how the page refers to --assets (relative to the page)",
    )
    render.set_defaults(func=_cmd_render)

    promote = sub.add_parser("baseline", help="promote a run record to the baseline")
    promote.add_argument("run", type=Path)
    promote.add_argument("--out", type=Path, required=True)
    promote.set_defaults(func=_cmd_baseline)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
