# Capacity handling across AUM (Part 3)

Addresses GitHub Issue #15. This analysis compares only the four capacity residual-allocation methods required by the Bible:

1. `hard_truncate`
2. `overflow_next`
3. `overflow_sequential_two`
4. `overflow_adv_weighted`

## Status

**The analysis harness is complete. Real empirical results are blocked until the frozen Section 0 Phase B handoff exists.** The repository currently contains only synthetic fixtures. It has no verified historical calendar, contract/FND metadata, real benchmark and baseline holdings, real eligible curves, or historical settlement and ADV20 inputs. The harness therefore refuses real-data mode rather than creating a fake result.

The synthetic run is a plumbing check only. It is not evidence for choosing a capacity method.

## Fixed inputs

`CapacityExperiment` accepts the frozen Section 0 objects rather than rebuilding them:

| Input | Required production source |
| --- | --- |
| Calendar, exact contract catalogue and as-of observations | Section 0 Phase B |
| Primary targets and eligible curves | Section 0 baseline path, with fixed contract choices |
| Fractional benchmark holdings | Section 0 historical benchmark |
| Rounding method | Frozen shared rounding choice |
| AUM denominator | One fixed AUM for each comparison |

For each method, capacity produces fractional allocations, then the same rounding method is applied through the shared rounding engine. The shared P&L and Tracking RMSE functions use exact contract settlements and one common valid-date mask.

## AUM and selection rules

The output grid is the required `$1m`, `$10m`, `$100m`, and `$1bn`. The preflight first tests whether primary capacity binds at `$1bn`; only if it does not, it activates the configured `$2.5bn` fallback reference. Every result records the binding count and residual notional.

Tracking RMSE is daily basis points with no annualisation. The lowest RMSE is preferred. Any difference **strictly below 0.1 bp** is a tie, resolved by the simpler method in this order: hard truncate, next-deferred overflow, sequential two-deferred overflow, ADV-weighted two-deferred overflow.

The output keeps the 100 common-date and 70% candidate-survival gates visible. A gate failure is reported, never dropped from the comparison, and blocks a preferred-method selection. If the `$1bn` preflight is nonbinding, the `$2.5bn` fallback is checked once; if that is also nonbinding, the output records a capacity limitation rather than changing the 2.5% participation assumption.

## Run the synthetic harness

```bash
uv run --no-sync python -B -m unittest tests.unit.test_capacity_analysis -v
uv run --no-sync python -B scripts/run_capacity_analysis.py --fixtures --out outputs/person3/synthetic_fixture
```

This writes:

- `capacity_aum_summary.csv`: AUM, method, Tracking RMSE, shared-mask coverage, binding count, residual notional and selection fields.
- `capacity_diagnostics.csv`: date-level primary capacity, residual allocation and flags for audit.

Without `--fixtures`, the command stops and states that it needs the frozen Section 0 handoff.

## To produce the real Part 3 result

1. Receive the verified Phase B calendar, exact contracts/FND anchors, observations, eligible curve, benchmark holdings and fixed primary targets from Section 0.
2. Build a `CapacityExperiment` adapter from that handoff, with no alternative contract path or reoptimisation.
3. Run the preflight, the AUM grid and the coverage gate; preserve the common mask.
4. Commit the real summary, diagnostics and required figures, then record the selected method and limitations for Section 5.
