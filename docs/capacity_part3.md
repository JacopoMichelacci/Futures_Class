# Capacity handling across AUM (Part 3)

Addresses GitHub Issue #15. This analysis compares the four locked residual-allocation rules while holding the frozen Section 0 primary contract path, timing, target weights, benchmark, accounting and nearest-half-up rounding fixed.

## Real-data result

The analysis uses the merged Section 0 Phase B handoff: 148 common CME return dates from 2 March through 30 September 2026, with a fixed $1bn capacity-selection AUM. All 148 dates pass the frozen common mask.

| Capacity method | Tracking RMSE at $1bn (daily bp) |
| --- | ---: |
| Hard truncate | 87.981363 |
| Next-deferred overflow | 66.999580 |
| Sequential two-deferred overflow | 57.282698 |
| ADV-weighted two-deferred overflow | 57.305883 |

`overflow_sequential_two` is selected. ADV-weighted overflow is 0.023185 bp worse, which is strictly below the locked 0.1 bp tie threshold. Sequential overflow is simpler, so it resolves the tie. No second metric was used.

## Locked design

- Core AUM grid: $1m, $10m, $100m and $1bn.
- Capacity reference AUM: $1bn. Section 0 confirmed capacity binds there, so the $2.5bn fallback is inactive.
- Capacity ceiling: `floor(2.5% × lagged ADV20 × one day)`.
- ADV20: exactly 20 verified preceding sessions, publication-safe and excluding the decision session.
- Ranking: exact-contract realised-return Tracking RMSE in daily basis points, with one fixed AUM denominator and the frozen common mask.

At the lower grid AUMs, capacity does not bind, so every residual-allocation method has the same holdings and RMSE. The methods diverge at $1bn, where all 444 primary target rows bind.

## Reproduce

```bash
uv sync --locked
uv run --no-sync python -B scripts/run_person3.py --out outputs/person3
uv run --no-sync python -B -m unittest discover -s tests/unit -v
```

The runner reads only the committed processed Section 0 handoff. It makes no network request and does not need a Databento key.

Outputs:

- `outputs/person3/capacity_summary.csv`: four methods at the $1bn selection AUM.
- `outputs/person3/capacity_by_aum.csv`: the full core AUM grid.
- `outputs/person3/capacity_diagnostics.csv`: date-level caps, residual allocations and flags.
- `outputs/person3/tracking_rmse_vs_aum.png`: main figure, including a $1bn comparison panel.
- `outputs/person3/HANDOFF.md`: frozen inputs, selected rule and limitations for Section 5.

The retained `outputs/person3/synthetic_fixture/` files are a previous plumbing check. They are not empirical results.
