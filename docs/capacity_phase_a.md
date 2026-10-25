# Capacity engine: fixture-driven Part 2 scope

`src/capacity/rules.py` implements reusable capacity helpers for Jacopo's
Person 3 strand. It consumes the shared `ObservationStore`, verified calendar,
contract catalogue and explicit eligible curve. It does not acquire market
data or run the historical analysis.

## Locked rules

- ADV20 comes from the shared as-of store and uses exactly 20 verified
  sessions before the decision session. Current/future sessions and late
  publications are excluded.
- The integer ceiling is `floor(0.025 * ADV20 * 1 day)`. These are locked
  configuration assumptions, not experiment parameters.
- Capacity acts on fractional requested contract quantities. It does not round
  the target before applying the integer per-contract ceiling.
- A decision-time dollar value for each contract is calculated from its latest
  valid settlement available by the decision cutoff and the catalogue's
  verified dollars-per-price-unit multiplier.
- Primary capacity is applied first. Any residual is kept in dollars and
  converted into fractional quantities using each deferred contract's own
  decision-time dollar value.
- Overflow candidates are eligible contracts with an anchor later than the
  primary, ordered by anchor. The baseline leaves residual notional
  unimplemented; alternatives allocate to the next contract, fill the next two
  sequentially, or weight the next two by positive ADV20. The weighted rule
  reallocates around a saturated recipient cap.
- The capacity builder requires a complete decision-date curve for every
  catalogue contract active between its activation and expiration dates. A
  missing contract row is an input error, not evidence of ineligibility.
- Missing or non-positive candidate ADV20, capacity or contract value makes
  that recipient unavailable. Unsupported residual notional remains explicit
  and flagged. If primary ADV20 or its dollar value is unavailable, construction
  fails closed.

The output contains fractional post-capacity quantities, each contract's
integer capacity ceiling, post-capacity dollar notional, decision cutoff,
source timestamp and flags. It is not a rounded `Holding`; the separate
rounding stage consumes these fractional quantities and ceilings.
Zero-allocation overflow recipients are omitted from the rows passed to that
stage; their unimplemented exposure and availability flags remain in the
diagnostics. Every emitted row has an integer capacity ceiling and positive
maturity rank.

`capacity_rounding_targets(portfolio)` is the explicit capacity-to-rounding
adapter. It returns records matching the separate `RoundingTarget` input shape
without importing or reimplementing that strand. The adapter sets each row's
target notional to its post-capacity fractional notional and allocates the
source target weight across rows in proportion to those notionals. It requires
the primary source weight/notional, keeps unimplemented exposure out of the
rounded targets, and never fills missing metadata with placeholder zeros. The
handoff tests check the adapter records for all four capacity methods.

## Reproduce the synthetic demonstration

From the repo root:

```bash
uv run --no-sync python -B scripts/run_capacity.py --fixtures
uv run --no-sync python -B -m unittest discover -s tests/unit -p 'test_capacity.py' -v
```

The demonstration uses only the existing Phase A synthetic observations and
hand-authored allocator cases in `data/fixtures/capacity_phase_a.json`. It
prints all four approaches and explicitly reports `method_selected: false`.
The checks include unequal dollar contract values, reallocation after a
recipient reaches its cap, and a no-valid-recipient fallback. These are test
examples, not market estimates or Part 3 findings.

## Part 3 boundary

No historical capacity comparison, reference-AUM fallback decision, Tracking
RMSE ranking or preferred method is produced here. Real-data source timing,
capacity preflight, sample coverage and diagnostics remain Part 3 work.
