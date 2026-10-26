# Part 3 capacity handoff (Issue #15)

**HANDOFF READY.** This real-data analysis consumes the merged frozen Section 0 Phase B bundle.

## Frozen inputs

- Returns: 2 March–30 September 2026
- Common valid dates: 148/148 (1)
- Capacity selection AUM: $1,000,000,000
- Core reporting grid: $1m, $10m, $100m, $1bn
- Participation / liquidation horizon: 2.5% / one day
- Rounding: unchanged nearest half-up after capacity

## Selection

The selected capacity rule is **overflow_sequential_two** at the frozen $1,000,000,000 reference AUM, with **57.282698 daily bp** Tracking RMSE.

The tied-with-best methods are: overflow_sequential_two, overflow_adv_weighted. `overflow_adv_weighted` has 57.305883 daily bp RMSE, only 0.023185 bp above `overflow_sequential_two`. This is strictly below the 0.1 daily-bp tie threshold. The simpler tied method is selected. No second ranking metric was used.

## Output files

- `capacity_summary.csv`: the four reference-AUM methods and selection fields.
- `capacity_by_aum.csv`: all four methods at each core AUM.
- `capacity_diagnostics.csv`: capacity-binding/residual-allocation diagnostics.
- `capacity_allocation_summary.csv`: residual recovery and deferred-notional exposure by method/AUM.
- `capacity_maturity_exposure.csv`: post-capacity notional by frozen maturity rank.
- `tracking_rmse_vs_aum.png`: primary presentation figure.
- `deferred_exposure_vs_aum.png`: explanatory capacity diagnostic; it is not a ranking metric.

The retained `synthetic_fixture/` directory is a prior plumbing check and is not an empirical finding.
