# Section 0 handoff: Issue #5 Phase A only

**Status:** fixture-driven Phase A contribution on `issue-5-section0-phase-a`. This is not the Phase B empirical handoff. No real sample, coverage gate or reference-AUM feasibility claim is made.

Issue #5 Phase A calls for shared interfaces, deterministic fixtures and a P&L/Tracking RMSE engine for Part 2. This delivery provides those locally. The merge requirement remains outstanding until the team has reviewed and merged this contribution.

## Reproduction and verified results

Run from the repository root:

```bash
uv run --no-sync python -B -m unittest discover -s tests/unit -v
uv run --no-sync python -B scripts/run_section0.py --fixtures
```

Final suite after the Phase A review corrections: **60 tests passed, zero failures or errors**. Ten new tests cover the generated baseline path, strict ADV20 lag/freshness and timing-policy validation. The clean-copy demo test also checks the generated roll event. Existing accounting results are unchanged.

| Test module | Tests | Coverage |
| --- | ---: | --- |
| [test_accounting.py](../../tests/unit/test_accounting.py) | 22 | Exact-contract P&L, fixed denominator, zero/half-size tracking, common missing-data mask, holdings provenance, canonical rows and deferred diagnostics. |
| [test_asof.py](../../tests/unit/test_asof.py) | 14 | Publication cut-offs, strictly prior-session ADV20 even when same-session data are available, freshness, non-sessions, revisions/deletions, explicit session times and contract identities. |
| [test_benchmark.py](../../tests/unit/test_benchmark.py) | 12 | Hand interpolation/fallback cases, eligibility, session distances, generated baseline entry/hold/roll paths, deterministic ordering and invalid-input handling. |
| [test_fixtures.py](../../tests/unit/test_fixtures.py) | 12 | Configuration including timing-policy values/types/presence, synthetic provenance, deferred-contract endpoints, independent reproduction and Issue #11 schema-name compatibility. |

The clean-copy test copies only shared source, configuration, fixtures and scripts into a temporary directory and runs the demo with Python's isolated mode. It has no access to the local pilot directory or an installed project package.

The fixture has four valid evaluation dates and fixed AUM of $1,000,000. Hand benchmark P&L is $200, $500, $50 and $450. Identical fixture holdings produce **0 daily bp** Tracking RMSE; half-sized holdings produce **1.758905909933786... daily bp**. These are algebraic fixture results, not research-method results.

The fixture demo also generates eight baseline contract positions from explicit decision curves. Its sole ZC roll event is 1 February 2030, from `ZC_SYN_NEAR` into `ZC_SYN_NEXT`, five supplied sessions before the 8 February anchor. The incoming contract is assigned to the subsequent return interval ending 4 February. The generated event matches the independent hand expectation. This contract-path demonstration is separate from the fixed accounting quantities; it does not claim to construct historical capacity-limited integer holdings.

Tests use `unittest` and remain pytest-compatible. Pytest, ruff and mypy were not installed or run; no dependency or environment update was performed.

## Issue #5 acceptance-criteria mapping

| Issue criterion | Current status | Evidence or remaining work |
| --- | --- | --- |
| Phase A fixtures and engine merged early enough for Part 2 | **Implemented; merge pending team review** | Shared fixture, loader, interfaces, accounting engine and offline CLI exist. Team review and merge remain outstanding. |
| Interpolation weights sum to 1, match hand fixtures, flag fallback | **Satisfied at fixture/helper level** | Bracketing, exact-target and no-bracket tests pass. The full historical benchmark builder remains Phase B. |
| Baseline destination and roll-date state machine matches hand fixtures | **Satisfied at fixture/state-machine level** | Generated nearest-entry, hold and next-deferred -5-session roll path matches hand expectations. Tests cover eligibility loss, unavailable destinations, skipped sessions, late entry and deterministic events. Historical data/position-sizing pipeline remains Phase B. |
| Holdings for `(t-1,t]` use only information available at `t-1` | **Satisfied for Phase A interfaces/tests** | Per-session cut-offs, as-of selection, provenance checks and unavailable-input masking. Production calendar/source verification remains Phase B. |
| Benchmark and strategy P&L share one fixed AUM | **Satisfied** | One comparison-wide AUM; no invested-notional normalisation or compounding denominator. |
| Identical holdings have zero error; 50%-size has nonzero error | **Satisfied** | Exact hand checks and daily-basis-point RMSE tests. |
| No contract splicing; missing settlements invalidate the date | **Satisfied** | Exact IDs, adjacent-session endpoints, null invalid returns and one common comparison mask. |
| At least 100 dates and 70% survival, frozen before outcomes | **Phase B; not run** | Four synthetic evaluation dates are not a real-data coverage gate. |
| AUM/capacity preflight and predeclared fallbacks | **Phase B; not run** | Values are recorded in configuration; no empirical capacity check or fallback activation. |

Real-data benchmark/baseline/curve/roll-event Parquet files and the tracking-coverage CSV have not been generated. The fixture CLI is explicitly guarded against accidental real-data execution.

All fixture-level Phase A criteria in this mapping are now satisfied. The early-merge criterion remains outstanding pending team review and merge. ADV20 now directly excludes the decision session, checks all 20 consecutive prior observations as of the cut-off, and retains the configured freshness bound. The existing three timing-policy fields are explicitly validated without changing configuration values.

## Downstream readiness and Issue #11

**Sufficient to start fixture-based Part 2 engines:** destination receives explicit curve/event inputs; timing receives source-dated, publication-aware volume/OI; capacity receives ADV20 and contract metadata; rounding receives fractional-quantity/ceiling hand inputs. All may construct `Holding` rows and use the common accounting/metric engine. No strand implementation is included.

**Issue #11 overlap is reconciled for the Phase A scope.** Its description names Alan as owner, although its assignee field is empty. No competing schema/config implementation was found. There is one configuration and one schema-name registry. Individual statistic/as-of observations now have their own name; the wide daily table and dedicated benchmark/contract-return exports retain the Version 8 names and explicit deferred status. Strategy-daily diagnostic fields are present as unknown/null rather than fabricated values. Configuration numbers, the fixture's numerical cases and the P&L/RMSE algorithms were not changed.

This does not complete Issue #11: production field validation, full metadata/provenance and export construction remain deferred. See [the reconciled interface guide](../../docs/section0_phase_a.md#issue-11-ownership-and-reconciliation) for the exact boundaries. The 22-file Phase A contribution is ready for team review; broader declarations must not be described as completed real-data processing.

**Recommended PR structure:** one infrastructure PR for Issue #5 Phase A referencing the partial Issue #11 dependency, with no automatic closure of either full issue. Keep Alan's Issue #6 rounding engine as a separate, clearly Issue-linked Part 2 contribution under the team plan. Splitting this incomplete schema subset into another PR would add a dependency without delivering an independently complete Issue #11. No GitHub issue state or comment was changed.

## Complete shared-file inventory

All 22 files below are new. No existing tracked file was edited by this implementation. The branch inherits upstream main's existing tooling and lockfile changes; those are not Phase A edits.

| File | Purpose |
| --- | --- |
| [config/project.yaml](../../config/project.yaml) | Version 8 parameters and explicit timing-policy settings; Issue #11 dependency scaffolding. |
| [data/fixtures/phase_a.json](../../data/fixtures/phase_a.json) | Synthetic calendar, contracts, observations, holdings, explicit baseline decision curves and independent hand expectations. |
| [data/fixtures/README.md](../../data/fixtures/README.md) | Fixture provenance, intended use and limitations. |
| [src/__init__.py](../../src/__init__.py) | Repository-local shared package. |
| [src/validation/schemas.py](../../src/validation/schemas.py) | Shared primitives, config checks, compatible schema names and explicit readiness/deferred status. |
| [src/data/calendar.py](../../src/data/calendar.py) | Explicit per-session settlement and decision times. |
| [src/data/asof.py](../../src/data/asof.py) | Generalised as-of observation and ADV20 interfaces. |
| [src/data/fixtures.py](../../src/data/fixtures.py) | Shared synthetic fixture loader. |
| [src/contracts/eligibility.py](../../src/contracts/eligibility.py) | Contract metadata and delivery-anchor eligibility. |
| [src/contracts/maturity.py](../../src/contracts/maturity.py) | Calendar-based maturity distances. |
| [src/benchmark/constant_maturity.py](../../src/benchmark/constant_maturity.py) | Pure interpolation/fallback helper. |
| [src/portfolio/holdings.py](../../src/portfolio/holdings.py) | Canonical holdings and validated immutable snapshots. |
| [src/portfolio/baseline.py](../../src/portfolio/baseline.py) | Frozen settings and pure baseline contract-path/roll-event state machine. |
| [src/metrics/portfolio_pnl.py](../../src/metrics/portfolio_pnl.py) | Common-mask P&L, fixed-AUM returns and daily records. |
| [src/metrics/tracking_rmse_bps.py](../../src/metrics/tracking_rmse_bps.py) | Daily Tracking RMSE aggregation. |
| [scripts/run_section0.py](../../scripts/run_section0.py) | Offline fixture-only entry point. |
| [tests/unit/test_accounting.py](../../tests/unit/test_accounting.py) | Accounting and mask tests. |
| [tests/unit/test_asof.py](../../tests/unit/test_asof.py) | As-of, calendar and revision tests. |
| [tests/unit/test_benchmark.py](../../tests/unit/test_benchmark.py) | Shared benchmark/metadata primitive tests. |
| [tests/unit/test_fixtures.py](../../tests/unit/test_fixtures.py) | Fixture/config and clean-copy reproduction tests. |
| [docs/section0_phase_a.md](../../docs/section0_phase_a.md) | Consumer interfaces, scope and Issue #11 reconciliation. |
| [outputs/section0/HANDOFF.md](HANDOFF.md) | This Phase A handoff, acceptance mapping and inventory. |

The local September loader, audit scripts, raw downloads, credential file, original notebooks and specification PDF are excluded from this shared-file set. No larger sample, Phase B work or method selection is included. Publishing this contribution does not complete Phase B or the wider Issue #11.
