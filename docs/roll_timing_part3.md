# Roll-timing analysis (Part 3)

Resolves [GitHub Issue #7](https://github.com/JacopoMichelacci/Futures_Class/issues/7) (roadmap `[10]`). It builds on the Part 2 engine from [#24](https://github.com/JacopoMichelacci/Futures_Class/pull/24) / Issue #23.

## Status

**Complete on the real, frozen Section 0 handoff** (#29; Databento GLBX.MDP3 CME data). The results, selection and limitations are in [outputs/person2/HANDOFF.md](../outputs/person2/HANDOFF.md). The readable analysis is in the canonical notebook [Section 2.ipynb](../Section%202%20-%20Roll%20Timing%20(Lucie)/Section%202.ipynb). Section 0 is consumed, never rebuilt.

## What changes and what is fixed

Only timing varies. Each of the four pre-specified rules (`fixed_-5` baseline, `fixed_-10`, `volume_crossover`, `oi_crossover`) is applied to the 10 unchanged baseline outgoing/incoming pairs. The crossover search window is the boundary agreed on #23: from the first session the outgoing contract is held, up to but excluding the -5 session, with a flagged -5 fallback.

`load_timing_experiment` ([handoff.py](../src/rolling/handoff.py)) verifies the handoff:
- the output hashes, the spec version and the $10m timing reference;
- the 148 frozen dates and the timing common mask;
- the calendar and catalogue identities.

It then builds a `TimingExperiment` ([analysis.py](../src/rolling/analysis.py)) whose fixed inputs are identical across rules:

| Input | Source |
| --- | --- |
| calendar, catalogue, publication-aware observations | `load_inputs(data/section0/inputs)` |
| baseline pairs and path | `roll_events.parquet`, `baseline_contract_path.parquet` |
| benchmark holdings | `benchmark_holdings.parquet` at $10m |
| sizing (`size_path`) | shared `holdings_from_contract_path`: target notional → 2.5% ADV20 capacity → nearest rounding |
| fixed AUM and common mask | `freeze.json` timing reference ($10m, Section 0's activated fallback) and the `timing` mask |

Accounting and the metric are the shared `portfolio_pnl` (with the frozen mask as `decision_validity`) and `tracking_rmse_bps`.

**Reconciliation checks**, run on every real analysis:
- `fixed_-5` must reproduce the frozen Section 0 baseline holdings and daily returns exactly;
- the crossover decisions are checked in tests against an independent recomputation from `timing_inputs.parquet`.

## Outputs (`outputs/person2/`)

- **`roll_timing_summary.csv`:** per rule: Tracking RMSE (daily bp), difference from the minimum, coverage, fallbacks, mean sessions before anchor, and the tie/selected flags.
- **`roll_events_by_rule.csv`:** every roll decision with its search window, fallback flag, trigger evidence, and whether it fired on the first permitted session.
- **`roll_timing_daily.csv`:** daily returns and tracking differences on the common mask.
- **Charts:** `tracking_rmse_by_roll_rule.png` and `roll_trigger_distribution.png`, plus their Plotly `.json` specs and the `chart_export.json` receipt.
- **`HANDOFF.md`** (generated) and **`analysis_manifest.json`** (input and output hashes).

### Selection rule

The lowest daily-bp RMSE wins. Differences **strictly below** 0.1 bp are ties, and ties go to the simplest rule in this order: baseline `fixed_-5`, then `fixed_-10`, then `volume_crossover`, then `oi_crossover`. The order was agreed on #27 (proposed by Varshini) and committed in `3615c81` **before any real-data run**. Volume ranks before OI because it reflects actual trading activity and shifts sooner than open positions. There is no second metric.

**No selection when a coverage gate fails.** If the ≥100 common-date or ≥70% survival gate fails, RMSE is still reported, but `preferred_rule` is `None` with `blocked_reason="coverage_gate_failed"`.

## Running

```bash
uv sync --locked
uv run --no-sync python -B -m unittest discover -s tests/unit -v
uv run --no-sync python -B scripts/run_person2.py --real                     # writes outputs/person2/
uv run --no-sync python -B scripts/run_person2.py --real --chart-html personal/charts
uv run --no-sync python -B scripts/run_person2.py --real --chart-png ~/Downloads/tracking_rmse_by_roll_rule.png ~/Downloads/roll_trigger_distribution.png
uv run --no-sync python -B scripts/run_person2.py --fixtures --out outputs/person2/synthetic_fixture   # synthetic demo only
```

The PNGs are browser exports: the project has Plotly but no static image exporter, and no dependency was added. Open each offline HTML page, click its camera button, then register the downloaded files with `--chart-png`. The receipt detects any later change to a figure or image.
