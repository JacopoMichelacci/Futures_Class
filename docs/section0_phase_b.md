# Section 0 Phase B: frozen empirical handoff

This contribution completes the real-data handoff for Issue #5 using the accepted
Issue #4 acquisition and Version 8 parameters. PR #26's validation foundation is
now merged. PR #29 has been revalidated against team main
`ed97dcc7694de469052502db6348d7dccb867b0e`, including the merged destination and
capacity engines. No strand engine or frozen empirical input was changed.

## Frozen experiment

- Returns: **2 March–30 September 2026**, 148 explicit CME sessions.
- First interval: **(27 February, 2 March]**. First positions use only information
  available by the 27 February decision cut-off. January/earlier February are
  warm-up only; no implementation path is carried from January.
- Roots/weights: ZC 0.333333, ZS 0.333333, ZW 0.333334, unchanged from configuration.
- Universe: 26 catalogue contracts, 22 statistics contracts. Missing prices do
  not imply ineligibility. The March contracts remain explicit ineligible rows
  until expiration; ZSK7 remains as intervening ordering metadata.
- FND is the anchor and remains separate from expiration. Direct CME dates and
  the accepted official-source-derived historical FNDs retain their provenance.
- All four masks pass **148/148, 100%**, exceeding both 100 dates and 70% survival
  (104 dates would be required for this 148-date candidate period).

The mask is a conservative data-availability mask: it checks every potentially
usable eligible contract in the frozen statistics universe, decision inputs,
aligned timing statistics and exact consecutive-session settlement endpoints.
It is evaluated after final reference AUMs and baseline holdings are constructed.
All core-grid baseline accounting results also pass the same mask. No method
performance was used to choose dates, AUMs or an alternative period.

An engine that emits an invalid path, changes the input contract/universe or needs
data outside the frozen scope must stop for reconciliation before comparisons.
Do not silently shorten a method's sample or convert missing observations to zero.

## AUM preflight

| Use | Default check | Final reference | Evidence |
|---|---|---:|---|
| Destination/timing | $100m binds in 586/1,582 contract-decision envelope rows | **$10m** | Declared fallback: 0/1,582 binding rows |
| Rounding | $1m is non-binding | **$1m** | 0/444 baseline target rows bind |
| Capacity | $1bn binds | **$1bn** | 444/444 baseline target rows bind |

The destination envelope uses the already accepted contract-reach inventory.
The timing envelope conservatively includes both members of each baseline pair
through its baseline deadline. It evaluates no crossover signals. The union is
deduplicated; these counts are feasibility checks, not comparisons of methods.

The core reporting grid stays **$1m, $10m, $100m, $1bn**. Neither $250k nor $2.5bn
is activated or added to that grid. Configuration retains the locked defaults and
fallback hierarchy; [freeze.json](../data/section0/freeze.json) records activation.

## Construction and timing

`constant_maturity_weights` supplies daily fractional **notional** weights around
60 observed-session distances. Exact 60 receives all weight; unbracketed dates
use only the locked nearest-to-60 fallback. Six commodity/date fallback records
occur: ZC and ZW on return dates 1–3 September. There is no capacity or integer
rounding in the mathematical benchmark.

Benchmark and baseline quantities use the latest valid settlement **published by
the decision cut-off**, multiplied by $50 per quoted-price unit. The current
interval's later-published settlement is valuation data, never a sizing input.
Quantities are retargeted daily against fixed AUM and the locked commodity weights.
The benchmark uses decision-date eligibility; valuation of a pre-existing
fractional reference position may include an FND endpoint. The physical baseline
rolls at -5 and never holds a contract on or after its FND.

The baseline enters the nearest eligible contract and generates ten next-deferred
roll events at -5 observed sessions. Its sizing layer applies the integer ceiling
`floor(0.025 * ADV20 * 1 day)` to the fractional target, leaves excess exposure
unimplemented, then applies nearest-half-up rounding. No alternative engine is
embedded in Section 0.

Normal-session decisions are at 13:15 America/Chicago, with DST-aware UTC times,
consistent with the accepted production audit. Good Friday and other source-
verified non-sessions are excluded even if the feed carries holiday statistics.
The explicit historical timestamp calendar ends on 1 October. A separate explicit
date-only extension through 30 June 2027 supports anchor distances. It adds **no
future decision cut-offs**, particularly no invented shortened-session times.
`days_to_anchor` uses that extension; `calendar.decision` still requires a real
timestamped historical session. Both calendar hashes are frozen separately.

The full raw production history remains local. The committed input projection
retains only the exact event versions consumed by the frozen decisions,
ADV20 windows, timing alignment and final valuation. It is not a claim to retain
the entire vendor stream or support other contexts. `FrozenStore` rejects new
decision contexts. Original raw hashes and source conventions are retained in
[provenance.json](../data/section0/provenance.json).

## Consumer interfaces

Use the existing locked environment; no new dependency is introduced:

```sh
uv sync --locked
uv run --no-sync python -B scripts/run_section0.py --reproduce --output-dir personal/section0-reproduced
uv run --no-sync python -B -m unittest discover -s tests/unit -v
```

The reproduction command requires an empty destination and makes no network/data
requests. Compare its `output_hashes.json` with the committed one. All generated
output hashes must agree. The fixture demonstration remains separately available
through `scripts/run_section0.py --fixtures`.

```python
from pathlib import Path
from src.section0.phase_b import load_inputs, read_table, holdings_from_contract_path
from src.validation.schemas import load_project_config
from src.portfolio.baseline import BaselinePath

config = load_project_config("config/project.yaml")
handoff = Path("data/section0")
shared = load_inputs(handoff / "inputs", config)
calendar, catalogue, observations = shared.calendar, shared.catalogue.contracts, shared.observations
curve = read_table(handoff / "eligible_curve.parquet")
baseline_path = BaselinePath(tuple(read_table(handoff / "baseline_contract_path.parquet")),
                             tuple(read_table(handoff / "roll_events.parquet")))
decision_inputs = read_table(handoff / "decision_inputs.parquet")
```

For destination/timing, pass the engine's implemented contract path into
`holdings_from_contract_path(..., strategy="your_rule")` at the frozen $10m AUM.
This reuses unchanged sizing, capacity and nearest rounding. The helper accepts
in-memory Decimal values or the exported exact-decimal strings. It raises on
missing/unimplemented inputs or a physical holding at/after FND; do not mask that
error by inserting a flat position.

| Strand | Consume | Boundary |
|---|---|---|
| Destination | Complete curves, catalogue/calendar, baseline path/events, shared sizing adapter | Change destination only; baseline event dates fixed |
| Timing | Baseline pairs/calendar, independent volume/OI inputs or `observations.latest_common`, shared sizing adapter | Change timing only; Issue #23 causal boundary; same pairs |
| Capacity | Baseline path, decision inputs, `pre_capacity_contracts` and original target notionals in target table | Change residual handling only; output fractional targets and integer ceilings |
| Rounding | Baseline post-capacity targets at each core AUM | Change rounding only; do not reapply capacity |

The target table retains original `target_weight` and `target_notional`; they are
never fabricated zeros. Capacity consumers use the **pre-capacity** column, not
the already truncated fractional output. Rounding consumers use
`post_capacity_fractional_contracts`, `capacity_limit_contracts`,
`dollar_contract_notional`, exact identity and maturity rank. Rank is deterministic
within the frozen root catalogue; deferred ordering is always relative to the
selected primary/anchor, not an absolute nearby-rank shortcut.

Timing's two statistics are independent. Rows in `timing_inputs.parquet` are
separate for cleared volume and OI; neither crossover requires both at a single
lookup. Respect the approved first-held search start, search strictly before -5,
and use the flagged -5 fallback. Section 0 supplies observations, not signals.

Monetary/quantity fields are exact decimal **strings** in Parquet; use `Decimal`
or shared loaders rather than float conversion. Select one `fixed_aum` before
building a `HoldingsBook`. Use the matching benchmark and frozen mask; never mix
different AUMs, dates, experiments or benchmark quantities. The same exact
contract supplies both P&L endpoints and the denominator remains fixed AUM.

## Data inventory and rebuilding from production

See [the bundle inventory](../data/section0/README.md). Initial construction can be
repeated by Alan using the already accepted local inputs, without reacquisition:

```sh
uv run --no-sync python -B scripts/run_section0.py \
  --production-dir "$SECTION0_PRODUCTION_DIR" \
  --inventory-dir "$SECTION0_INVENTORY_DIR" \
  --output-dir personal/section0-from-production
```

These environment variables are local directory paths, not credentials. Ordinary
teammates use the committed processed handoff and need neither raw files nor API
access. Hash mismatches fail closed. Keep original raw downloads, credentials and
all prior local work outside the contribution.

Part 3 alternatives, performance ranking, winner selection and Section 5 remain
outside this handoff. Baseline/benchmark daily accounting is supplied, but no
empirical RMSE aggregation or winning-rule output is produced.
