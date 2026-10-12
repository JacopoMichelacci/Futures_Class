# Capacity engine: fixture-driven Part 2 scope

`src/capacity/rules.py` implements the reusable capacity helpers for Jacopo's
Person 3 strand. It consumes the shared `ObservationStore`, verified calendar,
contract catalogue and an explicit eligible curve. It does not acquire market
data or run the historical analysis.

## Locked rules

- ADV20 comes from the shared as-of store. It uses 20 consecutive verified
  sessions before the decision session; current/future sessions and late
  publications are excluded.
- The integer limit is `floor(0.025 * ADV20 * 1 day)`. Both assumptions come
  from the locked project configuration and are not experiment parameters.
- The target is rounded to the nearest whole primary-contract equivalent,
  with half contracts rounded up, then capped at the primary limit.
- Overflow candidates are eligible contracts with an anchor later than the
  primary contract, ordered by anchor. This is relative to the supplied
  primary, so it remains compatible with a future destination rule.
- The baseline truncates residual exposure. Alternatives send residual to the
  next deferred contract, fill the next two sequentially, or split across the
  next two by positive ADV20. The ADV-weighted rule reallocates around a
  saturated capacity and leaves any unsupported residual explicit.
- If primary ADV20 is unavailable, construction fails closed. For overflow,
  missing or non-positive ADV20 makes that candidate unavailable; if neither
  candidate has valid positive ADV20 and capacity, the residual stays
  unimplemented and the output is flagged.

All functions are deterministic. `build_capacity_portfolio` emits standard
`Holding` rows with each contract's own capacity ceiling, integer quantity,
decision cut-off, input timestamp and flags. It only considers deferred
contracts after primary capacity binds.

## Reproduce the synthetic demonstration

From the repo root:

```bash
uv run --no-sync python -B scripts/run_capacity.py --fixtures
uv run --no-sync python -B -m unittest discover -s tests/unit -p 'test_capacity.py' -v
```

The demonstration uses the existing Phase A synthetic observations for its
portfolio case and runs the hand-authored allocator cases in
`data/fixtures/capacity_phase_a.json`. It prints all four approaches and
explicitly reports `method_selected: false`. One allocator check reallocates
after a candidate reaches its cap; another exercises the no-valid-ADV20
fallback. These values are test examples, not market estimates or Part 3
findings.

## Part 3 boundary

No historical capacity comparison, reference-AUM fallback decision, Tracking
RMSE ranking or preferred method is produced here. Real-data source timing,
capacity preflight, sample coverage and diagnostics remain Part 3 work.
