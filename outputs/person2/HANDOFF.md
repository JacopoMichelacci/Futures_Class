# Person 2: independent empirical roll-timing analysis

**PERSON 2 TIMING HANDOFF READY**

Real Databento GLBX.MDP3-derived frozen Section 0 inputs, not synthetic fixture results.

## Frozen experiment

- Issue #7. Only roll timing changes. The 10 baseline outgoing/incoming pairs, next-deferred destination, baseline capacity and nearest rounding (shared `holdings_from_contract_path`), weights, benchmark and accounting are fixed.
- 148 common return dates (2 March–30 September 2026; first decision 27 February), survival 1; gates (≥100 dates, ≥0.70) pass.
- Reference AUM $10,000,000: Section 0's activated timing fallback (capacity binds at $100m).
- Fixed -5 reproduces the frozen Section 0 baseline holdings and daily returns exactly; crossover decisions reconcile to `data/section0/timing_inputs.parquet`.
- Crossover search (Issue #23): first session the outgoing contract is held through strictly before the -5 session, first strictly-greater publication-safe observation, flagged -5 fallback.

## Results

| Rule | Tracking RMSE (daily bp) | Δ from minimum | Fallbacks to -5 | Mean sessions before anchor |
|---|---:|---:|---:|---:|
| Fixed -5 (baseline) | 7.258071 | 0.988638 | 0/10 | 5.00 |
| Fixed -10 | 6.383168 | 0.113735 | 0/10 | 10.00 |
| Volume crossover | 6.630368 | 0.360935 | 4/10 | 13.00 |
| OI crossover | 6.269433 | 0.000000 | 2/10 | 16.50 |

## Selection

Preferred timing rule at the $10m reference: **OI crossover**.
Margin over the runner-up (Fixed -10): 0.113735 daily bp. Tie rule triggered: **False** (ties are strictly below 0.1 bp; order agreed before the real run: -5, -10, volume, OI).

## Interpretation and limitations

- Every alternative tracked the 60-session benchmark more closely than rolling at -5, consistent with earlier rolls moving holdings toward the benchmark's longer target maturity sooner.
- 4 crossover roll(s) fired on the first permitted session: Volume crossover ZCU6→ZCZ6 (48 sessions early), Volume crossover ZSU6→ZSX6 (26 sessions early), OI crossover ZCU6→ZCZ6 (48 sessions early), OI crossover ZSU6→ZSX6 (26 sessions early). The incoming contract already out-traded the thin September contract, so these rules largely bypass it. This follows the pre-agreed rule, but it overlaps with destination behaviour and contributes to the crossover results.
- Timing is evaluated with destination fixed at next deferred; interaction with other destination rules is not tested here (Section 5 combines strands once, without re-optimising).
- Findings are conditional on this frozen sample; no transaction costs or collateral yield; no other strand's preferred method is imported.

## Reproduction and outputs

```sh
uv sync --locked
uv run --no-sync python -B scripts/run_person2.py --real
```

PNGs are browser exports: `--chart-html DIR` writes offline pages; use each page's camera button, then `--chart-png PATH [PATH]` to register them. The receipt detects later figure or image changes.

Canonical notebook: [Section 2.ipynb](../../Section%202%20-%20Roll%20Timing%20(Lucie)/Section%202.ipynb).
- `roll_timing_summary.csv`: per-rule RMSE, coverage, fallback and tie/selection flags.
- `roll_events_by_rule.csv`: every roll decision, window and trigger evidence.
- `roll_timing_daily.csv`: daily returns and tracking differences on the common mask.
- `tracking_rmse_by_roll_rule.png`, `roll_trigger_distribution.png`: charts (+ `.json` specs, `chart_export.json` receipt).
- `analysis_manifest.json`: frozen input hashes, selection and output hashes.

`outputs/person2/synthetic_fixture/` is a separate synthetic harness demonstration, not a finding.
