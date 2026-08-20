# Performance

!!! info "Generated page"
    Written by `tools/perf_report.py render` from the nightly
    benchmark run (`.github/workflows/performance-nightly.yml`) and
    redeployed to the `dev` docs version. Editing it by hand is
    pointless — the next nightly overwrites it.

This is Phase 0 of [#131](https://github.com/prokopto-dev/nparse-plus/issues/131):
the measurement the later phases are supposed to be argued from. It is a
dashboard, not a gate — nothing in CI fails because a number here moved,
because hosted runners are shared VMs and a nightly that cried wolf would
be muted inside a week. Read it for trends, not for single runs.

## Latest run

- **When**: 2026-08-20T18:00:23.371329+00:00
- **Commit**: `188767e1fb98` (refs/heads/master)
- **Runner**: seed (local, macOS arm64), Python 3.12.11
- **Recorded runs in history**: 1

This is the seed run committed with #132 — the trend charts appear
once the nightly has recorded a second one.

Percentages compare against the baseline recorded on **seed (local, macOS arm64)** (2026-08-20, `188767e1fb98`).

## EventBus.publish

The floor under everything else. `publish` snapshots both subscriber lists defensively, which is the allocation #131 Phase 1 proposes to replace with copy-on-write — the 0-subscriber row is what that would be measured against.

| Benchmark | Mean | vs baseline | |
| --- | --- | --- | --- |
| `test_bench_bus_publish[0]` | 160 ns | +0.0% |  |
| `test_bench_bus_publish[10]` | 499 ns | +0.0% |  |
| `test_bench_bus_publish[1]` | 184 ns | +0.0% |  |
| `test_bench_bus_publish[50]` | 1.84 µs | +0.0% |  |
| `test_bench_bus_publish_to_firehose` | 182 ns | +0.0% |  |

## LogPipeline.process (full backend)

One replay of 60 in-game seconds of traffic through the parser chain and every handler subscribed to it. Divide by the line count in the table for per-line cost; the driver thread has 100 ms per poll to absorb whatever arrived in it.

| Benchmark | Mean | vs baseline | |
| --- | --- | --- | --- |
| `test_bench_pipeline_cold_backend` | 115.29 ms | +0.0% |  |
| `test_bench_pipeline_corpus` | 1.56 ms | +0.0% |  |
| `test_bench_pipeline_profile[group]` | 9.97 ms | +0.0% |  |
| `test_bench_pipeline_profile[raid]` | 27.03 ms | +0.0% |  |
| `test_bench_pipeline_profile[solo]` | 3.81 ms | +0.0% |  |

Per line: `test_bench_pipeline_cold_backend`: 633 lines, 182.14 µs/line; `test_bench_pipeline_corpus`: 93 lines, 16.77 µs/line; `test_bench_pipeline_profile[group]`: 440 lines, 22.67 µs/line; `test_bench_pipeline_profile[raid]`: 1264 lines, 21.38 µs/line; `test_bench_pipeline_profile[solo]`: 167 lines, 22.84 µs/line.

## Plugin subscriber dispatch

The same publish, but through `HostPluginContext` — so the per-plugin try/except guard and the telemetry gate are both inside the number. `off` and `on` are collection disabled and enabled: the gap is the entire cost of #132's measurement on the dispatch path.

| Benchmark | Mean | vs baseline | |
| --- | --- | --- | --- |
| `test_bench_plugin_dispatch[1-off]` | 203 ns | +0.0% |  |
| `test_bench_plugin_dispatch[1-on]` | 484 ns | +0.0% |  |
| `test_bench_plugin_dispatch[10-off]` | 745 ns | +0.0% |  |
| `test_bench_plugin_dispatch[10-on]` | 3.32 µs | +0.0% |  |
| `test_bench_plugin_dispatch[50-off]` | 3.06 µs | +0.0% |  |
| `test_bench_plugin_dispatch[50-on]` | 16.64 µs | +0.0% |  |

## Plugin parser in the chain

A plugin parser appended after the built-in chain, over a raid burst. It never consumes a line, which is the worst case: every line reaches it.

| Benchmark | Mean | vs baseline | |
| --- | --- | --- | --- |
| `test_bench_plugin_parser[off]` | 18.94 ms | +0.0% |  |
| `test_bench_plugin_parser[on]` | 14.36 ms | +0.0% |  |

Per line: `test_bench_plugin_parser[off]`: 633 lines, 29.93 µs/line; `test_bench_plugin_parser[on]`: 633 lines, 22.69 µs/line.

## Qt bridge (buffer + coalesced flush)

1000 events published from a worker thread and delivered in one coalesced GUI-thread flush. `burst` has a per-event slot connected, `batch_only` just the bulk signal.

| Benchmark | Mean | vs baseline | |
| --- | --- | --- | --- |
| `test_bench_qt_bridge_batch_only` | 1.05 ms | +0.0% |  |
| `test_bench_qt_bridge_burst` | 1.19 ms | +0.0% |  |

## Map canvas render

The heaviest widget in the app, coalescing 120 location fixes into one render.

| Benchmark | Mean | vs baseline | |
| --- | --- | --- | --- |
| `test_bench_map_location_burst` | 348.55 µs | +0.0% |  |

## End-to-end latency (log append -> UI slot)

A real `LogDriver` tailing a real file, so this includes the 100 ms poll interval it is dominated by. That is the honest end-to-end figure — it is what the user waits.

| Benchmark | Mean | vs baseline | |
| --- | --- | --- | --- |
| `test_bench_latency_append_to_ui` | 102.05 ms | +0.0% |  |
| `test_bench_latency_parse_to_ui` | 72.24 µs | +0.0% |  |

## Running it yourself

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -m benchmark --benchmark-only
```

Benchmarks are excluded from the default `uv run pytest` (see the
`addopts` in `pyproject.toml`) because timing assertions in CI are noise.
The fixtures they replay are in `tests/perf/profiles.py`: solo, group and
raid traffic composed from the `EQtoolsTests` line corpus.
