"""``tools/perf_report.py`` — the nightly dashboard's normalize/compare/render.

Exercised end to end against a miniature pytest-benchmark document, because
the failure mode that matters is not a wrong number: it is a nightly that
silently writes an empty page, or one that reports a regression against a
baseline recorded on different hardware without saying so.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import perf_report  # noqa: E402


def _raw(means: dict[str, float], *, group: str = "bus") -> dict:
    return {
        "datetime": "2026-08-20T04:10:00+00:00",
        "machine_info": {"python_version": "3.12.11", "node": "runner-7"},
        "benchmarks": [
            {
                "name": name,
                "group": group,
                "stats": {
                    "mean": mean,
                    "median": mean,
                    "stddev": mean / 100,
                    "min": mean * 0.9,
                    "max": mean * 1.2,
                    "rounds": 100,
                },
                "extra_info": {"lines": 100},
            }
            for name, mean in means.items()
        ],
    }


def _record(means: dict[str, float], **kwargs) -> dict:
    defaults = {
        "commit": "a" * 40,
        "ref": "refs/heads/master",
        "runner": "ubuntu-latest",
        "run_id": "",
    }
    return perf_report.normalize(_raw(means), **{**defaults, **kwargs}).to_json()


def test_normalize_keeps_only_what_the_history_needs() -> None:
    run = _record({"test_bench_bus_publish[1]": 1.5e-7})
    assert run["schema"] == perf_report.SCHEMA
    assert run["runner"] == "ubuntu-latest"
    assert run["run_id"]  # always has an identity of its own
    assert run["python"] == "3.12.11"
    entry = run["benchmarks"]["test_bench_bus_publish[1]"]
    assert entry["group"] == "bus"
    assert entry["mean"] == 1.5e-7
    assert entry["extra"] == {"lines": 100}


def test_normalize_falls_back_to_the_machine_node_for_the_runner() -> None:
    run = perf_report.normalize(_raw({"a": 1.0}), commit="x", ref="", runner="").to_json()
    assert run["runner"] == "runner-7"


def test_nightly_runs_of_one_commit_all_survive() -> None:
    """The regression this history exists to catch.

    A scheduled run measures whatever the default branch is at 04:10, so the
    commit is identical every night until the next merge. Keying on it would
    leave one point per merge — and throw away exactly the repeated
    measurements that separate runner noise from a real regression.
    """
    history: list[dict] = []
    for night in range(5):
        history = perf_report.append_history(
            history, _record({"a": 1.0 + night / 100}, commit="unchanged", run_id=f"r{night}")
        )
    assert len(history) == 5
    assert {run["commit"] for run in history} == {"unchanged"}
    assert [run["benchmarks"]["a"]["mean"] for run in history] == [1.0, 1.01, 1.02, 1.03, 1.04]


def test_append_replaces_only_a_rerun_of_the_same_workflow_run() -> None:
    first = _record({"a": 1.0}, run_id="run-1")
    second = _record({"a": 2.0}, run_id="run-2")
    rerun = _record({"a": 1.5}, run_id="run-1")

    history = perf_report.append_history([], first)
    history = perf_report.append_history(history, second)
    history = perf_report.append_history(history, rerun)

    # Re-running one workflow run corrects its own entry rather than adding a
    # third point, and lands at the end as the newest thing recorded.
    assert [run["run_id"] for run in history] == ["run-2", "run-1"]
    assert history[-1]["benchmarks"]["a"]["mean"] == 1.5


def test_run_identity_falls_back_to_the_timestamp() -> None:
    """A local run has no run id, and a record written before it existed
    has no field — both still need an identity of their own."""
    local = _record({"a": 1.0})
    assert local["run_id"] == local["timestamp"]
    legacy = {"timestamp": "2026-08-01T04:10:00+00:00", "benchmarks": {}}
    assert perf_report.run_identity(legacy) == "2026-08-01T04:10:00+00:00"


def test_append_trims_from_the_front() -> None:
    history: list[dict] = []
    for index in range(perf_report.HISTORY_LIMIT + 5):
        history = perf_report.append_history(history, _record({"a": 1.0}, run_id=f"r{index}"))
    assert len(history) == perf_report.HISTORY_LIMIT
    assert history[-1]["run_id"] == f"r{perf_report.HISTORY_LIMIT + 4}"


def test_compare_classifies_against_the_threshold() -> None:
    baseline = _record({"steady": 1.0, "slow": 1.0, "fast": 1.0})
    run = _record({"steady": 1.05, "slow": 1.5, "fast": 0.5, "brand_new": 1.0})
    verdicts = {c.name: c.verdict for c in perf_report.compare(run, baseline)}
    assert verdicts == {
        "steady": "steady",
        "slow": "slower",
        "fast": "faster",
        "brand_new": "new",
    }


def test_compare_against_no_baseline_calls_everything_new() -> None:
    run = _record({"a": 1.0})
    assert [c.verdict for c in perf_report.compare(run, {})] == ["new"]


def test_durations_are_formatted_at_a_readable_scale() -> None:
    assert perf_report._fmt_duration(1.5) == "1.50 s"
    assert perf_report._fmt_duration(0.0015) == "1.50 ms"
    assert perf_report._fmt_duration(1.5e-6) == "1.50 µs"
    assert perf_report._fmt_duration(1.5e-9) == "2 ns"


def test_page_names_regressions_up_front() -> None:
    baseline = _record({"test_bench_bus_publish[1]": 1.0})
    run = _record({"test_bench_bus_publish[1]": 2.0})
    page = perf_report.render_page([run], baseline, asset_dir="../assets/perf", charts={})
    assert "1 benchmark(s) more than" in page
    assert "test_bench_bus_publish[1]" in page
    assert "+100.0%" in page


def test_page_warns_when_the_baseline_is_from_another_machine() -> None:
    """The one thing that would make every percentage on the page a lie."""
    baseline = _record({"a": 1.0}, runner="seed (local, macOS arm64)")
    run = _record({"a": 1.0}, runner="ubuntu-latest")
    page = perf_report.render_page([run], baseline, asset_dir="x", charts={})
    assert "Baseline is from a different machine" in page

    same = perf_report.render_page([run], _record({"a": 1.0}), asset_dir="x", charts={})
    assert "Baseline is from a different machine" not in same


def test_page_survives_an_empty_history() -> None:
    page = perf_report.render_page([], {}, asset_dir="x", charts={})
    assert "No runs recorded yet" in page


def test_page_reports_per_line_cost_where_a_line_count_was_recorded() -> None:
    run = _record(
        {"test_bench_pipeline_profile[raid]": 0.02},
    )
    page = perf_report.render_page([run], run, asset_dir="x", charts={})
    assert "Per line:" in page
    assert "µs/line" in page


def test_chart_needs_two_runs_and_then_draws_one_line_per_benchmark() -> None:
    runs = [
        _record({"a": 1.0, "b": 2.0}, run_id="r1"),
        _record({"a": 1.1, "b": 1.8}, run_id="r2"),
    ]
    assert perf_report.render_chart(runs[:1], "bus") == ""
    svg = perf_report.render_chart(runs, "bus")
    assert svg.startswith("<svg")
    assert svg.count("<path") == 2
    # Ratios, so the reference line is 1.00x and both series start there.
    assert "1.00x" in svg


def test_chart_tolerates_a_benchmark_that_only_exists_in_later_runs() -> None:
    runs = [
        _record({"a": 1.0}, run_id="r1"),
        _record({"a": 1.0, "b": 1.0}, run_id="r2"),
        _record({"a": 1.0, "b": 1.2}, run_id="r3"),
    ]
    svg = perf_report.render_chart(runs, "bus")
    assert svg.count("<path") == 2


def test_cli_round_trip(tmp_path: Path) -> None:
    """record -> baseline -> append -> compare -> render, as the nightly runs it."""
    raw = tmp_path / "perf-raw.json"
    raw.write_text(json.dumps(_raw({"test_bench_bus_publish[1]": 1.5e-7})), encoding="utf-8")
    run = tmp_path / "run.json"
    baseline = tmp_path / "baseline.json"
    history = tmp_path / "history.json"
    page = tmp_path / "performance.md"
    assets = tmp_path / "assets"

    assert (
        perf_report.main(
            ["record", str(raw), "--out", str(run), "--commit", "abc", "--run-id", "999"]
        )
        == 0
    )
    assert perf_report.main(["baseline", str(run), "--out", str(baseline)]) == 0
    assert perf_report.main(["append", str(run), "--history", str(history)]) == 0
    assert perf_report.main(["compare", str(run), "--baseline", str(baseline)]) == 0
    assert (
        perf_report.main(
            [
                "render",
                "--history",
                str(history),
                "--baseline",
                str(baseline),
                "--out",
                str(page),
                "--assets",
                str(assets),
            ]
        )
        == 0
    )
    assert "# Performance" in page.read_text(encoding="utf-8")
    recorded = json.loads(history.read_text(encoding="utf-8"))["runs"][0]
    assert recorded["commit"] == "abc"
    assert recorded["run_id"] == "999"


def test_cli_refuses_a_missing_input(tmp_path: Path) -> None:
    assert perf_report.main(["record", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o")])


def test_compare_only_fails_when_asked_to(tmp_path: Path) -> None:
    """The nightly passes no --fail-over, which is the point of the flag."""
    baseline = tmp_path / "baseline.json"
    run = tmp_path / "run.json"
    baseline.write_text(json.dumps(_record({"a": 1.0})), encoding="utf-8")
    run.write_text(json.dumps(_record({"a": 5.0})), encoding="utf-8")

    assert perf_report.main(["compare", str(run), "--baseline", str(baseline)]) == 0
    assert perf_report.main(["compare", str(run), "--baseline", str(baseline), "--fail-over", "50"])


def test_compare_can_write_a_job_summary(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    run = tmp_path / "run.json"
    summary = tmp_path / "summary.md"
    baseline.write_text(json.dumps(_record({"a": 1.0})), encoding="utf-8")
    run.write_text(json.dumps(_record({"a": 1.0})), encoding="utf-8")

    perf_report.main(["compare", str(run), "--baseline", str(baseline), "--summary", str(summary)])
    assert "Benchmark vs baseline" in summary.read_text(encoding="utf-8")


def test_the_committed_baseline_is_a_valid_run_record() -> None:
    """The seed shipped with #132 has to be readable by the tool that made it."""
    baseline = json.loads(
        (REPO_ROOT / "tests" / "perf" / "baseline.json").read_text(encoding="utf-8")
    )
    assert baseline["schema"] == perf_report.SCHEMA
    assert baseline["benchmarks"]
    groups = {entry["group"] for entry in baseline["benchmarks"].values()}
    # Every group the docs page has prose for should be represented, or the
    # page describes measurements nobody takes.
    assert set(perf_report.GROUP_TITLES) <= groups


def test_every_group_the_suite_produces_has_prose() -> None:
    """The reverse guard: a new benchmark group must not render unexplained."""
    baseline = json.loads(
        (REPO_ROOT / "tests" / "perf" / "baseline.json").read_text(encoding="utf-8")
    )
    groups = {entry["group"] for entry in baseline["benchmarks"].values()}
    assert groups <= set(perf_report.GROUP_NOTES)
