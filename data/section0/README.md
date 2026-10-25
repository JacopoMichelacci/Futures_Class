# Frozen Section 0 bundle

Version 8; 148 dates from 2 March to 30 September 2026, initial decision 27 February.
Destination/timing reference $10m, capacity $1bn, rounding $1m. The core grid is
$1m/$10m/$100m/$1bn. This approximately 1.7 MB processed bundle needs no network or
credentials. Raw downloads remain local and were not committed.

| File | Contents |
|---|---|
| `contracts.parquet` | All 26 identities, quote-value metadata, sourced FND and separate activation/expiration |
| `eligible_curve.parquet` | Complete active-catalogue rows per decision, including explicit ineligibility |
| `baseline_contract_path.parquet` | Nearest-entry/next-deferred contract state for 444 root/date rows |
| `roll_events.parquet` | Ten generated baseline -5 decision events |
| `decision_inputs.parquet` | As-of prices, independent volume/OI, ADV20 windows and consumed observation IDs |
| `timing_inputs.parquet` | Aligned pair observations separately for volume and OI; no signals |
| `benchmark_holdings.parquet` | Daily fractional notional interpolation, no capacity/rounding, all core AUMs |
| `baseline_holdings.parquet` | Pre-existing integer baseline holdings, provenance and capacity metadata |
| `post_capacity_targets.parquet` | Pre-capacity targets and baseline-constrained fractional targets; no dummy metadata |
| `contract_returns.parquet` | Exact same-contract consecutive settlement endpoints, multiplier and price-relative return |
| `baseline_daily.parquet` | Benchmark/baseline dollar P&L and fixed-AUM daily returns; no RMSE aggregation |
| `common_masks.parquet` | Four identical 148-date masks at final strand references |
| `tracking_coverage_report.csv` | Candidate/survival gates for each strand |
| `freeze.json` | Frozen dates, activated references, feasibility counts and calendar/catalogue IDs |
| `provenance.json` | Raw-source/reference hashes and accepted timing/FND conventions |
| `output_hashes.json` | Expected hashes of generated output files |
| `inputs/catalogue.json` | Reproduction catalogue |
| `inputs/calendar.json` | Explicit historical cut-offs and separate forward maturity dates |
| `inputs/scope.json` | Frozen scope and accepted destination-reach bounds |
| `inputs/consumed_observations.parquet` | Exact consumed versions for this freeze; not a replacement for full raw history |
| `inputs/hashes.json` | Integrity checks for reproduction inputs |

Decimal quantities/prices are strings to preserve precision. No field is silently
forward-filled. Future method outputs must satisfy these frozen interfaces.

Run from repository root:

```sh
uv run --no-sync python -B scripts/run_section0.py --reproduce --output-dir personal/reproduced
```

Compare `personal/reproduced/output_hashes.json` to `data/section0/output_hashes.json`.
For API examples, boundaries and missing-data handling, see
[the Phase B consumer guide](../../docs/section0_phase_b.md).
