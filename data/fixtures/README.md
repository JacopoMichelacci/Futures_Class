# Phase A synthetic fixtures

[phase_a.json](phase_a.json) is entirely synthetic and suitable for inclusion in the shared repository. It contains no Databento observations, credentials, September-cache extracts or CME calendar claims. It is included for reproducible fixture-based team development.

Its format marker is `section0_phase_a_cases_v1`. Sections such as `contracts`, `observations` and `holdings` are fixture-constructor inputs, not claims to materialise the similarly named Phase B processed tables. The loader rejects files without this marker. `Observation.view()` follows the separate `statistic_observations` schema; `HoldingsBook.to_records()` follows the unified Section 36 holdings schema. The dedicated `benchmark_holdings` export with `fractional_contracts` and `interpolation_weight` remains deferred.

The fixture provides:

- nine explicitly identified contracts, three maturities for each of ZC, ZS and ZW;
- an explicit synthetic session calendar, a non-session holiday and a shortened-session example;
- volume/OI history and a deliberately late revision for testing as-of selection and ADV20;
- five settlement dates for all nine contracts, supporting four consecutive return intervals;
- eligible-curve rows, eight explicit baseline decision curves with expected active contracts, a hand-specified expected roll event and benchmark interpolation cases;
- long-form benchmark, identical implementation and 50%-size implementation holdings;
- a separate post-capacity fractional-quantity input case for downstream rounding tests;
- named missing-settlement and missing-volume perturbations, applied in memory by tests.

The accounting holdings are hand specified to isolate P&L and RMSE. They are **not** the output of a complete equal-notional constant-maturity benchmark or baseline state machine. Supplied capacity ceilings in the hand-input cases are test constraints, not outputs from a capacity algorithm. Downstream engines should add their own small synthetic method-specific edge cases.

Separately, `baseline_path_case` is input to the pure baseline state machine. It generates the nearest-contract entry, hold and next-deferred transition on 1 February 2030, matching the hand event five supplied sessions before the outgoing anchor. Generated contract positions are dated by the following return-interval end and are not sized holdings. The fixture demo prints them separately from the unchanged accounting example.

The hand-calculated benchmark P&L is `$200, $500, $50, $450` at a fixed `$1,000,000` AUM. The identical implementation has zero tracking RMSE. The half-sized implementation has `sqrt(3.09375) = 1.7589059099...` daily basis points of tracking RMSE. No method is selected by this demonstration.

Load from the repository root:

```python
from src.data.fixtures import load_fixture

fixture = load_fixture("data/fixtures/phase_a.json")
calendar = fixture.calendar
catalogue = fixture.catalogue
observations = fixture.observations
holdings = fixture.holdings
eligible_curve = fixture.raw["eligible_curve"]
roll_events = fixture.raw["roll_events"]
post_capacity_targets = fixture.raw["post_capacity_targets"]
```

Decimal values are strings in the JSON. The loader converts accounting inputs to `Decimal`. Calendar times are explicit fixture inputs; they must not be reused as a real exchange schedule. The shared loader has no fallback to a local cache or a data service.

Schema names and readiness states have one registry in [schemas.py](../../src/validation/schemas.py). Phase A daily comparison records reserve `implemented_notional_share` and `quality_flags` as null because those diagnostics are not calculated here; null is neither zero implementation nor a passed quality check.
