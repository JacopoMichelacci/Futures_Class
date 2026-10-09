# Section 0 Phase A interfaces and review boundary

Branch: `issue-5-section0-phase-a`, based on upstream main `0a51609ba5a621fd2e3b49ba919fd513b73bdc2f`.

This is the fixture-driven infrastructure portion of [GitHub Issue #5](https://github.com/JacopoMichelacci/Futures_Class/issues/5), whose title carries specification roadmap number `[4]`. The shared schema/configuration dependency is [GitHub Issue #11](https://github.com/JacopoMichelacci/Futures_Class/issues/11), roadmap item `[2]`. Those numbering systems must not be confused. Alan's later rounding contribution remains separate under GitHub Issue #6.

## Scope and status

The shared code provides explicit calendars and as-of observations, validated long-form holdings, exact-contract dollar P&L, fixed-AUM returns, and daily Tracking RMSE. It also supplies a pure benchmark interpolation helper, contract eligibility/maturity primitives, an executable fixture-driven baseline contract state machine and synthetic inputs for downstream engines.

There is no acquisition client, September loader, real-data calendar, full benchmark/baseline pipeline, strand rule, real-data preflight, empirical comparison or method selector in this delivery. Baseline contract selection and roll transitions are implemented; historical quantity sizing, capacity and rounding are not. This contribution is intended for team review; merging remains the repository owner's responsibility.

## Running the verified fixture workflow

From the repository root, using the existing environment:

```bash
uv run --no-sync python -B -m unittest discover -s tests/unit -v
uv run --no-sync python -B scripts/run_section0.py --fixtures
```

The shared core and test suite use the standard library. No dependency or lockfile changes are needed for this workflow. The tests use `unittest` and are also discoverable by pytest once that tool is available. Pytest, ruff and mypy were not installed or run in this environment. The course's general development-tool conventions can be adopted through the agreed shared tooling process.

The explicit `--fixtures` flag is required. Invoking the script without it exits with a Phase B explanation rather than accessing data. The demo writes JSON to standard output and does not create empirical outputs.

## Supported Phase A interfaces

| Module | Interface and responsibility |
| --- | --- |
| [schemas.py](../src/validation/schemas.py) | `Identity`, `ContractError`, decimal/UTC validation, locked configuration loading and one schema-name/readiness registry. |
| [calendar.py](../src/data/calendar.py) | `TradingSession(trading_date, settlement_at, decision_cutoff)` and `SessionCalendar`. Every date supplies its own times. Calendar identity includes dates and times. |
| [asof.py](../src/data/asof.py) | `Observation`, `ObservationStore`, `normalise`; preserve revisions and `ts_ref` dates, select only publications available by the decision cut-off, provide common-source-date lookups and ADV20. |
| [eligibility.py](../src/contracts/eligibility.py) | `Contract` metadata with explicit delivery anchor and dollar multiplier; `is_eligible` does not substitute expiration for first notice. |
| [maturity.py](../src/contracts/maturity.py) | `days_to_anchor` requires supplied calendar coverage; no generic business-day approximation. |
| [constant_maturity.py](../src/benchmark/constant_maturity.py) | `constant_maturity_weights` returns interpolation weights and a flagged nearest-contract fallback. It does not choose roll destinations. |
| [holdings.py](../src/portfolio/holdings.py) | `Holding`, `HoldingsBook`; unique date/strategy/commodity/contract rows, quantity and provenance checks, immutable snapshots and long-form records. |
| [baseline.py](../src/portfolio/baseline.py) | `BASELINE`, `BaselineSpec` and `build_baseline_path`; generate active exact contracts and deterministic roll events from explicit per-decision curves. No quantity sizing or real-data pipeline. |
| [portfolio_pnl.py](../src/metrics/portfolio_pnl.py) | `portfolio_pnl` evaluates all compared books on one common date mask and denominator. `daily_records` exports column-compatible daily comparisons; invalid returns and uncomputed diagnostics remain null. |
| [tracking_rmse_bps.py](../src/metrics/tracking_rmse_bps.py) | `tracking_rmse_bps` aggregates the common valid sample without annualisation or method selection. Empty samples fail rather than score zero. |
| [fixtures.py](../src/data/fixtures.py) | `load_fixture` returns a synthetic `FixtureBundle`; no credential, network or private-cache dependency. |

Minimal comparison:

```python
from src.data.fixtures import load_fixture
from src.metrics.portfolio_pnl import portfolio_pnl, daily_records
from src.metrics.tracking_rmse_bps import tracking_rmse_bps

f = load_fixture("data/fixtures/phase_a.json")
daily = portfolio_pnl(
    f.holdings, f.observations,
    f.raw["return_dates"], f.raw["strategies"],
    f.raw["fixed_aum"], f.raw["snapshot_end_exclusive"],
)
scores = tracking_rmse_bps(daily)
records = daily_records(daily, f.raw["strategies"])
```

## Timing, holdings and invalid-date obligations

- A holdings row's `date` is the ending date of `(t-1,t]`. Its quantities must already be fixed at the preceding session's cut-off. The book verifies `inputs_as_of <= formed_at <= decision_cutoff` and matches the cut-off to the supplied calendar. The producer must report truthful provenance and obtain inputs through the as-of interface.
- Benchmark rows use `benchmark_fractional_contracts` and no implementation quantity. Implementation rows use non-negative integer `implemented_contracts`; they may also retain the benchmark quantity as a descriptive reference. That reference never replaces the implemented quantity in P&L.
- Supply a complete explicit snapshot for every compared portfolio/date. Missing snapshots are invalid, not implicitly flat. Zero holdings are explicit and require no price.
- Supply `decision_validity[(date, strategy)]` when a computed engine can encounter unavailable inputs. A missing or false entry in a supplied validity map invalidates that date for all compared portfolios. The hand-authored fixture snapshots are known constants and need no computed-input map.
- Prices are FINAL, non-intraday exact-contract settlements selected from a declared later valuation snapshot. ACTUAL/THEORETICAL quality is retained. Missing required endpoints invalidate the shared date; no zero filling, cross-maturity splicing or multi-session bridging occurs.
- The AUM argument is one positive fixed value for all portfolios and dates. Return equals exact-contract dollar P&L divided by that AUM. The metric reports `10000 * sqrt(mean((implemented_return - benchmark_return)^2))` on the common valid dates.
- ADV20 excludes the decision session even if its volume has already been published. Its 20 consecutive verified sessions end no later than the immediately preceding session. Every selected revision must be published by the decision cut-off; no missing interior member is skipped. Endpoint age remains measured from the decision session, with the configured maximum of one session. If that prior session's volume is unavailable, ADV20 is unavailable rather than using older data. General `latest_available` and `latest_common` lookups retain their separate as-of semantics; a same-session observation being available does not admit it to ADV20. Real-data timing verification remains a separate Phase B requirement.

## Fixture-driven baseline contract path

`build_baseline_path(eligible_curve, decision_dates, commodities, calendar, catalogue)` consumes explicit consecutive decision-session curves in the existing eligible-curve shape. Curve identities, anchors and session distances must reconcile with the supplied catalogue/calendar. Producers must supply curves and metadata known at each decision cut-off. Missing curves, skipped decision sessions, duplicate contracts or a required but unavailable deferred destination raise `ContractError` instead of silently carrying a position.

The initial active contract is the nearest eligible maturity. It remains active until five observed sessions before its anchor, then transitions to the first eligible contract with a strictly later anchor. It does not reselect a nearer contract between events. A contract losing eligibility is exited; a nearest contract already inside its roll deadline at sample entry is immediately rolled forward. An incoming contract already due for its own roll is advanced again. These safety cases never retain an ineligible contract or invent an unavailable destination.

Roll-event `date` is the decision session. A generated position's `date` is the next session, the ending date of the return interval for which its exact contract is already fixed; `decision_date` and `decision_cutoff` preserve that distinction. Position rows identify contracts, not sized canonical `Holding` rows. Downstream sizing remains separate.

The new `baseline_path_case` fixture supplies eight consecutive decision curves. The generated ZC path starts in `ZC_SYN_NEAR`, stays there before 1 February 2030, and rolls on 1 February into `ZC_SYN_NEXT`, five supplied sessions before the 8 February anchor. That destination applies to the interval ending 4 February. The pre-existing hand roll event is the independent expected result. The CLI prints the generated path and event separately from the unchanged hand-authored accounting holdings and P&L results.

## Issue #11 ownership and reconciliation

The live Issue #11 description names **Alan** as owner; its GitHub assignee field is empty. Issue #5 is assigned to `11aland11`. The inspected upstream main contains no configuration or source implementation, and no open PR supplied competing interfaces. We therefore found no evidence that another teammate's assigned implementation was overwritten or duplicated.

There is intentional cross-issue scope overlap: [project.yaml](../config/project.yaml) and [schemas.py](../src/validation/schemas.py) supply the minimum dependency subset needed by Phase A. **That subset is now reconciled for Phase A, but must not be presented as completion of Issue #11.** No second schema module or configuration was introduced.

Issue #11 requires the locked Version 8 configuration, explicit contract/commodity/date identifiers, long-form holdings, metadata conventions and schemas for the specified datasets. The existing configuration already matches the locked parameters and was left unchanged. It uses JSON syntax, which is valid YAML, so the standard-library loader needs no new parser dependency. The loader now explicitly locks the existing timing conventions: integer `max_liquidity_source_age_sessions = 1`, `decision_cutoff_policy = explicit_per_session_at_or_before_starting_settlement`, and `real_data_timing_status = phase_b_verification_required`. Missing fields, different values and different types fail validation; these are implementation conventions, not new research parameters. Changing the real-data status requires deliberate Phase B reconciliation, not an unvalidated config edit.

The representation boundaries are now explicit in the single registry:

| Representation | Contract and status |
| --- | --- |
| `statistic_observations` | Active Phase A revision/as-of records. Contains statistic type/value and publication/decision timestamps; it is not a processed daily row. The identifier root is explicit. |
| `daily_contract_data` | Reserved Version 8 wide row per trading date/commodity/contract, containing settlement, cleared volume, OI, eligibility, maturity and notional fields. Production construction and validation are deferred. |
| `contracts` | Reserves the Version 8 maturity, first-notice provenance, quotation and tick fields alongside identity/accounting fields. The Phase A `Contract` object remains a smaller input type, not a completed metadata export. |
| Unified holdings | `HOLDINGS_COLUMNS` remains the Section 36 engine representation used by `Holding` and `HoldingsBook.to_records()`. Baseline/strategy records use this superset; implementation quantities remain distinct from benchmark reference quantities. |
| `benchmark_holdings` | Reserves the dedicated Appendix B fields, including `fractional_contracts` and `interpolation_weight`. A later export must explicitly map the benchmark quantity from the engine representation; no interpolation weights are invented for hand-authored accounting fixtures. |
| `strategy_daily` | Phase A returns/errors are materialised with the required Version 8 column names. `implemented_notional_share` and `quality_flags` are explicit nulls until computed by later validated code. Null does not mean zero or a passed quality check. |
| `contract_returns` | Retains Appendix B's `contract_return` name as a deferred declaration. It is not silently relabelled `settlement_change`. Its later diagnostic definition/export must respect Version 8's accounting precedence; Phase A P&L directly uses exact-contract settlement differences and does not depend on this table. |

`TABLE_SCHEMA_STATUS` marks each reserved production declaration as deferred and distinguishes synthetic-only inputs from active Phase A interfaces. Column declarations are not full dtype/nullability/provenance validators. The fixture marker `section0_phase_a_cases_v1` makes its compact constructor inputs explicitly different from processed-table exports. In Phase A, `commodity` identifiers use the locked root codes ZC/ZS/ZW.

Remaining Issue #11 work is broader production-schema validation, full metadata/provenance conventions and real-data export adapters. Keep it open for that work; no Phase B processing was added merely to complete its checklist. The former naming/shape conflicts no longer block the scoped Phase A contribution. Teammates may start Part 2 against the documented typed interfaces and synthetic fixtures, while each strand retains responsibility for its own method-specific edge cases.

## Recommended PR structure and Part 2 evidence

Use **one shared-infrastructure PR** for Issue #5 Phase A, explicitly referencing the included prerequisite subset of Issue #11. Suggested scope wording: “Implements Phase A of #5 and the config/schema dependency subset of #11; leaves Phase B and the remaining #11 production contracts open.” Avoid automatic closing keywords for either full issue.

Do not split this small, incomplete Issue #11 subset into a second contribution merely to increase the PR count. The files are needed together for a runnable fixture demonstration, and the wider Issue #11 deliverable is intentionally unfinished. A separate schema PR would make sense later only for a separately complete, independently reviewable scope.

Version 8 requires a substantive Issue-linked Part 2 PR and reviews of teammate PRs; it does not award completion merely for creating more PRs. Under the fixed team plan, Alan's clearly identifiable graded Part 2 engine contribution remains the separate [Issue #6 rounding PR](https://github.com/JacopoMichelacci/Futures_Class/issues/6). The infrastructure PR enables that work and the other strands; it should not be represented as completing rounding. No grade outcome is inferred here.

## Phase B boundary

Real exchange calendars, shortened-session settlement times, dated definitions and historical first-notice completeness remain Phase B data requirements. The library deliberately requires caller-supplied times; the synthetic shortened-session test verifies the interface, not any real holiday schedule.

Historical benchmark/baseline construction and quantity sizing, the 100-date/70% gate, AUM/capacity preflight, real-data Parquet outputs and the empirical handoff remain unimplemented. The pure baseline contract state machine is implemented and fixture-tested. The remaining Phase B requirements do not block fixture-driven Part 2 engines, but must pass before Part 3 comparisons. No historical download is authorised by this handoff.

See [the Phase A handoff and acceptance mapping](../outputs/section0/HANDOFF.md) for results and the complete file inventory.
