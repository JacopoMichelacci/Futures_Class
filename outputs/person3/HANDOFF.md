# Part 3 capacity/AUM handoff (Issue #15)

## Status

The repository has a tested capacity/AUM comparison harness and synthetic audit output only. **It has no real empirical result and no capacity method has been selected for the project.**

`synthetic_fixture/capacity_aum_summary.csv` and `synthetic_fixture/capacity_diagnostics.csv` are generated from synthetic observations solely to verify the end-to-end path: capacity allocation → fixed nearest rounding → exact-contract P&L → common-mask daily-bp Tracking RMSE.

The synthetic harness produces 105 valid dates and 100% survival so it exercises the coverage gates. It also verifies that the `$1bn` preflight binds in the fixture, so the `$2.5bn` fallback is not activated there. If either production gate fails, the harness reports the diagnostics but blocks method selection. If `$1bn` and then `$2.5bn` are both nonbinding, it records a limitation rather than tuning participation. Those values and the fixture's preferred method are not market findings.

## Required before the real report

Section 0 Phase B must supply the verified historical calendar and contract/FND metadata, real as-of settlement and ADV20 inputs, frozen eligible curves and primary targets, and historical fractional benchmark holdings. Once that handoff exists, adapt it into `CapacityExperiment`, run the unchanged harness, preserve its common mask, and replace these synthetic artifacts with the real AUM table, diagnostics and required figures.
