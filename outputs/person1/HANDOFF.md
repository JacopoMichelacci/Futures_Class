# Issue #14 — Contract destination analysis (Part 3)

**Status:** pre-handoff analysis framework; empirical evaluation PENDING.

## Scope
Four locked rules only: next deferred (baseline), second deferred,
45 observed-session target, 75 observed-session target. Roll-event dates,
benchmark, target weights, capacity, integer rounding and shared accounting
must be held fixed. Ranking uses realized daily return Tracking RMSE (bp).
Maturity exposure and switching dates are diagnostics only.

## Completed before Section 0 Phase B
- `src/selection/analysis.py`: four-rule path construction using the existing
  `rules.py` engine, fixed event/position date checks, one shared valid-date
  intersection, fixed-AUM and coverage checks, RMSE calculation, tie handling.
- `src/selection/exports.py`: output writers and required charts; refuses to
  label synthetic output as canonical empirical findings.
- `tests/unit/test_selection_analysis.py`: evaluation invariants and gates.
- `scripts/run_person1.py`: guarded entry point. Real historical adapter
  remains blocked; no empirical ranking is claimed.

## Required historical handoff and remaining integration
1. Merged Section 0 Phase B: verified calendar, catalogue, complete eligible
   curves, benchmark and baseline holdings, roll events, settlement changes,
   frozen sample/mask, and frozen reference AUM.
2. Wire Section 0's shared target-notional -> capacity -> nearest-integer
   rounding to each destination path; do not implement alternate sizing.
3. Use the same shared exact-contract consecutive-session P&L and return
   engine for all four strategies and benchmark.
4. Confirm Part V locked tie priority. Ties strictly below 0.1 bp are
   provisionally modeled based on team discussion, subject to verification.
5. Wire `run_person1.py` to the historical adapter and export:
   `contract_destination_summary.csv`, `contract_destination_daily.csv`,
   `tracking_rmse_by_rule.png`, `maturity_exposure.png`, `HANDOFF.md`.
6. Report empirical coverage, Tracking RMSE, diagnostics, selected rule,
   limitations, and reproducible invocation. Create new PR addressing #14.

## Limitations
No real Section 0 Phase B outputs are available in the provided materials.
This is not a completed Part 3 analysis. No real-data metrics, preferred
destination, or historical figures have been generated.
