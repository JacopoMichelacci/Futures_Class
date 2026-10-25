# Issue #6: fixture-based integer rounding

This Part 2 implementation consumes the merged interfaces from [Section 0 PR #18](https://github.com/JacopoMichelacci/Futures_Class/pull/18) and addresses [Issue #6](https://github.com/JacopoMichelacci/Futures_Class/issues/6). The dedicated branch is `issue-6-rounding-part2`, based on team main commit `5851efbf2810a6e50d2ccea2c944a13b97b3df5b`, which also includes the merged starter notebooks from [PR #19](https://github.com/JacopoMichelacci/Futures_Class/pull/19).

## Current review boundary

Version 8 and Issue #6 specify nearest-integer rounding but do not specify exact-half ties. Alan explicitly approved **half-up**: for non-negative quantities, 0.5 → 1, 2.5 → 3 and 3.5 → 4. This is the deterministic tie convention for the baseline rounding rule, not a research result or method selection. The implementation uses exact rational comparisons rather than binary float rounding.

The Issue #6 contribution is isolated in a clean worktree from the merged team main. Only the four rounding source, test, demonstration and documentation files are included; the original local files remain preserved in their original working tree. This contribution is published separately from the shared setup PRs. Merge remains subject to team review and explicit authorisation.

## Inputs and rules

[rules.py](../src/rounding/rules.py) provides `round_nearest`, `round_toward_zero`, `round_away_from_zero`, `round_residual_greedy` and `build_rounded_portfolio`.

Scalar rules accept a non-negative post-capacity fractional quantity and its non-negative integer capacity ceiling, and return a Python integer. Quantities above the supplied ceiling are rejected, because clipping them here would redo upstream capacity handling. Nearest uses the closest integer, choosing the higher integer at exact halves. Toward-zero uses floor. Away-from-zero returns the smallest integer at least as large as the quantity, including zero for a zero target. Integer ceilings ensure that rounding up a valid post-capacity input remains feasible.

`RoundingTarget` supplies explicit commodity and exact contract identity, a positive maturity rank, the post-capacity fractional quantity, dollar contract notional, integer capacity ceiling, target weight and target notional. The producer must supply the same targets and contracts to every method, with truthful decision-time provenance. Ranks must uniquely order held contracts within each root; ambiguous ranks and duplicate contracts fail validation.

The rounding engine validates the post-capacity positions supplied to it but does not independently require that ZC, ZS and ZW are all present. Required project-commodity completeness is an upstream Section 0 / Part 3 experiment-preflight responsibility. A missing commodity must not be silently interpreted as zero exposure.

Residual-greedy starts from toward-zero quantities. It selects the feasible one-contract addition with the greatest strictly positive decrease in

```text
sum_i ((post_capacity_fractional_quantity_i - integer_quantity_i)
       * dollar_contract_notional_i)^2
```

Exact ties use the shared root order ZC, ZS, ZW, then earlier supplied maturity rank. Exact rational arithmetic preserves improvement comparisons without float tolerances. A zero improvement is not accepted. `GreedyResult` retains immutable quantities, the initial/final squared error and an ordered trace of accepted additions. This objective is only a construction rule; it is never a substitute for daily return Tracking RMSE.

Residual greedy has no shared notional-budget constraint. Its squared dollar-notional error objective is separable by contract, so in unconstrained cases it will generally agree with nearest rounding except where the strict-positive-improvement rule differs, especially exact half cases. At an exact half, nearest uses half-up while residual greedy receives zero improvement from adding the extra contract and therefore remains at floor. This behaviour is intentional under the locked project specification and does not introduce another optimisation constraint.

The builder returns a shared `HoldingsBook` containing one implemented snapshot. Its `date` is the return-interval end. The shared calendar determines the preceding decision cut-off, and `Holding` enforces `inputs_as_of <= formed_at <= decision_cutoff`. Zero quantities remain explicit. Combine these rows with the shared benchmark and other strategy rows before calling `portfolio_pnl` once for the entire comparison, followed by `tracking_rmse_bps`. Use one fixed AUM and one common valid-date mask.

## Reproduction

Use the existing locked environment from the repository root:

```bash
uv run --no-sync python -B -m unittest discover -s tests/unit -v
uv run --no-sync python -B scripts/run_person4.py --fixtures
```

The demo uses the existing shared `post_capacity_targets` and synthetic settlements. All supplied targets are explicitly near contracts, with maturity rank 1. Its fractional reference is an accounting fixture, not a constructed historical constant-maturity benchmark. It shows all four rules, the greedy trace and shared-accounting integration; it selects no preferred method and writes no empirical output. Running without `--fixtures` fails explicitly.

Tests include hand rounding examples, exact-half ties for both integer parities and large quantities, exact decimal boundaries, rejected invalid/uncapped inputs, zero and binding ceilings, dollar-weighted greedy priorities, exact root/maturity ties under reordered input, step-by-step error reductions, unchanged contract/target sets, provenance guards and shared P&L/RMSE with common missing-settlement invalidation. Exact-half targets also pass through the portfolio builder: nearest and away-from-zero round up, while residual-greedy keeps the floor because adding a contract gives zero objective improvement. The half-up convention does not change that strict-improvement stopping rule.

## Issue #6 acceptance evidence

All fixture-level acceptance criteria are covered in [test_rounding.py](../tests/unit/test_rounding.py):

| Criterion | Evidence |
| --- | --- |
| Non-negative integer quantities | Scalar output tests, zero targets and shared holdings validation. |
| Nearest, toward-zero and away-from-zero match hand examples | Explicit hand calculations, half-up cases and exact decimal boundary checks. |
| Greedy never worsens squared error; fixed tie-break | Hand objective totals, step-by-step improvement checks and all 24 input permutations of tied root/maturity candidates. |
| Integer capacity ceilings respected | Zero/binding-cap examples, uncapped input rejection and exact-half portfolio checks. |
| Same target notionals and held contracts | All four builders preserve supplied identities, weights, notionals, ceilings and decision timing. |
| Determinism | Exact arithmetic, fixed sorting and repeated/reordered tied cases. |

The complete shared-plus-rounding suite and explicit fixture demo are checked on the merged team-main base before publication. Empirical readiness is a separate Phase B requirement.

## Unchanged design and Phase B prerequisites

Rounding does not select contracts, change roll dates, compute ADV/capacity, alter weights or AUM, construct a new benchmark or redefine P&L/RMSE. It consumes no other strand's results. No transaction costs, collateral yield or additional ranking metric is introduced.

Before Part 3, Section 0 must supply real data and sourced first-notice metadata; verified exchange sessions and shortened-session times; the production daily-contract table; real benchmark and baseline quantities; a frozen common sample with at least 100 valid dates and 70% survival; capacity/AUM preflight using only the predeclared $250k, $10m and $2.5bn reference fallbacks where applicable; and production outputs with provenance. None of those empirical requirements has been completed by this rounding implementation.
