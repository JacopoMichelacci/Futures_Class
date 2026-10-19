# Roll-timing analysis (Part 3)

Addresses [GitHub Issue #7](https://github.com/JacopoMichelacci/Futures_Class/issues/7) (roadmap `[10]`). It builds on the Part 2 engine in [#24](https://github.com/JacopoMichelacci/Futures_Class/pull/24) / Issue #23.

## Status

**The analysis pipeline is complete. Real-data results are pending the frozen Section 0 handoff (Issues #4/#5).** That handoff does not exist yet: [#26](https://github.com/JacopoMichelacci/Futures_Class/pull/26) is the validation layer only. Per the team plan, Section 0 is not rebuilt here. Until the handoff lands, the whole comparison runs on an entirely synthetic matched experiment. **Synthetic results are not findings.**

## What changes and what is fixed

Only timing varies. Each of the four pre-specified rules (`fixed_-5` baseline, `fixed_-10`, `volume_crossover`, `oi_crossover`) is applied to the unchanged baseline outgoing/incoming pairs. The crossover search window is the boundary agreed on #23: from the first session the outgoing contract is held, up to but excluding the -5 session, with a flagged -5 fallback.

Everything else is an input to `TimingExperiment` ([analysis.py](../src/rolling/analysis.py)) and is identical across rules:

| Input | Source in production |
| --- | --- |
| calendar, catalogue, observation store, baseline path | Section 0 handoff |
| benchmark holdings | Section 0 historical benchmark (#5) |
| `quantity_for(commodity, contract, decision_date)` | Section 0 baseline sizing: target notional → capacity (#13) → nearest rounding (#6) |
| fixed AUM | `selection_roll_reference_aum_usd` = $100m (or the $10m fallback if Section 0 activates it) |

Accounting and the metric are the shared `portfolio_pnl` and `tracking_rmse_bps`: exact-contract settlements, one fixed AUM, and one common date mask for the benchmark and all four rules.

## Outputs (Issue #7 acceptance criteria)

`compare_timing_rules` returns, and `scripts/run_person2.py` writes:

- **`roll_timing_summary.csv`:** per rule, Tracking RMSE (daily bp), common valid dates, candidate dates, survival, roll events, fallback events and frequency, mean sessions before anchor, and the tie/preferred flags.
- **`roll_events_by_rule.csv`:** every roll decision with its search window, fallback flag and trigger evidence (source date and both values).
- **Gates:** the minimum common-date count and candidate survival from `config/project.yaml` (100 and 70%). A failure is reported, not hidden.

### Selection rule

The lowest daily-bp RMSE wins. Differences **strictly below** 0.1 bp are ties, and ties go to the simplest rule in this order: baseline `fixed_-5`, then `fixed_-10`, then the two crossover rules. **A tie only between volume and OI crossover has no agreed order**, so it is reported as `resolved=False` rather than picked. The team needs to decide that if it ever occurs. There is no second metric.

## Running

```bash
uv run --no-sync python -B -m unittest discover -s tests/unit -v
uv run --no-sync python -B scripts/run_person2.py --fixtures --out outputs/person2/synthetic_fixture
```

Without `--fixtures` the script exits and explains that real data awaits the handoff.

## Remaining once the handoff exists

1. Add a loader that builds a `TimingExperiment` from the frozen handoff. This is the only new input code; the analysis is unchanged.
2. Run at the reference AUM and commit `outputs/person2/roll_timing_summary.csv` and `roll_events_by_rule.csv` with the real results.
3. Add the two figures and the notebook from the issue. Figures need a plotting dependency agreed through the shared lockfile; none is added here.
4. Record the preferred rule in `outputs/person2/HANDOFF.md` for Section 5 (#16).
