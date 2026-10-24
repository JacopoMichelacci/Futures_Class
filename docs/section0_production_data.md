# Offline production input contracts: Issue #4 / minimum Issue #11 support

This is an offline validation layer, not a downloader or completed Phase B handoff.
Project specification and configuration remain Version 8. No sample, AUM fallback,
coverage gate or preferred method is selected here.

## Catalogue, curves and experiment completeness

[production.py](../src/validation/production.py) exposes:

- `validate_catalogue(records)`: explicit `first_notice`, equal delivery `anchor`,
  separate later `expiration`, activation, exact identity, FND source URL and
  retrieval date/time, `anchor_source` and contract-value `source_note` are required.
  It returns shared `Contract` objects plus retained metadata and a catalogue hash.
  It validates evidence fields, not the truth of an external source; synthetic
  provenance in tests is not production certification.
- `validate_complete_curve(rows, decision_dates, catalogue, calendar)`: for every
  requested date and ZC/ZS/ZW root, require every catalogue contract active between
  activation and expiration (inclusive). Present contracts at/after FND have
  explicit `eligible=False`; missing contracts raise a distinct error. Eligibility
  must match the sourced activation/FND predicate. An additional early withdrawal
  would need an agreed, sourced policy; this validator does not invent one.
  Anchor distances use the explicit session calendar, which must include anchors.
- `validate_experiment_targets(rows, dates)`: require exactly one non-negative
  commodity `target_notional` for each requested date/root. An explicit zero stays
  zero; a missing root fails. This is a commodity-target boundary, not a restriction
  on multiple contract holdings per commodity.

Curve evidence is **complete relative to the supplied catalogue**, with catalogue
and calendar identifiers. Acquisition must independently establish the catalogue's
coverage against the required listing/universe inventory. Neither a source URL nor
a successful row-set check proves exchange-wide completeness or verifies session
times. Missing settlement/volume/OI never changes contract eligibility.

Catalogue evidence is copied and restricted to flat strings, integers, booleans,
finite Decimals and nulls. Nested mutable values and arbitrary objects are rejected.
Retained rows are read-only; hashes use explicit Decimal encoding rather than
arbitrary object stringification. FND source URLs require HTTP(S), a nonempty host
and no whitespace; this checks syntax, not external source authenticity.

## Price units

[value.py](../src/contracts/value.py) validates standard ZC/ZS/ZW terms: `BU`,
5,000 bushels, `value_unit=cents_per_bushel`, scale 0.01, tick 0.25, tick value
$12.50 and `dollars_per_price_unit=50`. `quote_units_to_usd` converts a quoted
amount or signed price move for one contract; it does not replace shared P&L.
The raw vendor `min_price_increment_amount` is retained as evidence and is not
trusted as the dollar tick. Source verification remains an ingestion obligation.
Exact production prices, quantities and monetary amounts accept Decimal, integers
and decimal strings; binary floats and booleans are rejected locally. The existing
shared numeric helper and all strand implementations remain unchanged.

## Revision history and missingness

`serialize_observations(observations, identities, calendar)` and
`deserialize_observations(payload, identities, calendar)` provide an exact-decimal
JSON history envelope. All supplied revisions remain, including deletions, flags,
sequence/channel, publication and venue timestamps, invalid reasons and exact
identities. Trading date is the shared normalised UTC `ts_ref` date; it must not
be relabelled from publication date. The envelope binds the session calendar hash.
Acquisition manifests must separately retain raw files, requests and their hashes.
Duplicate JSON object members are rejected at both envelope and event level.
Non-null event values are checked through the existing shared raw normalizer so
its undefined-value convention remains the single authority. Undefined sentinels
are rejected at this boundary; already-normalized nulls retain their invalid reason.

The new `OBSERVATION_EVENT_COLUMNS` declaration uses existing `Observation`
fields. It is separate from the existing context-bearing `Observation.view()` /
`OBSERVATION_COLUMNS` contract; neither existing schema is renamed. A wide daily
snapshot cannot load as revision history. Reloading preserves as-of selection:
later revisions remain invisible before publication. A serializer cannot detect
revisions omitted upstream, so acquisition must retain the full received history.

`observation_availability` distinguishes absent history (`missing_observation`)
from history first published after the cut-off (`unavailable_by_decision_cutoff`).
Deleted/invalid values and non-session/future dates remain separate states.
Contract `eligible=False` is a catalogue/curve fact; a missing required contract
row is a validation error. None of these states creates zero exposure or a zero
settlement. Shared `ObservationStore` still controls revision selection, freshness
and strictly lagged ADV20. Decision cut-offs and later valuation freezes remain
distinct; no preliminary-settlement fallback is introduced.

## Capacity to rounding

`validate_post_capacity_targets` checks the eight existing rounding input fields
declared in `POST_CAPACITY_TARGET_COLUMNS`: commodity, exact contract, maturity
rank, fractional post-capacity quantity, dollar contract notional, integer ceiling,
target weight and target notional. It neither rounds nor recomputes capacity and
does not require all roots; the experiment boundary above does that separately.
Decision provenance must also be passed truthfully to the shared holdings builder.

Positive overflow rows must have valid target metadata before becoming rounding
inputs. Null weight/notional is rejected, never filled with zero. The producer and
consumer must agree whether each descriptive target field is commodity-level or
contract-level and how the adapter carries it without double-counting exposure.
That mapping and a real cross-engine adapter test remain outstanding; this module
does not patch either engine or claim current PR #25 is directly compatible.

## Validation and deferred work

From this checkout, using the existing project environment:

```bash
uv run --no-sync python -B -m unittest discover -s tests/unit -p 'test_production.py' -v
uv run --no-sync python -B -m unittest discover -s tests/unit -p 'test_contract_value.py' -v
uv run --no-sync python -B -m unittest discover -s tests/unit -v
```

Tests are entirely synthetic and use the shared fixture. There is no import-time
I/O or private-cache dependency in the new modules. Historical source acquisition,
verified exchange calendars, dated definition updates, production exports and
source registers remain Issue #4 work. Historical portfolio construction, final
AUM preflight, sample/mask freezing and the 100-date/70% gates remain Issue #5.
Existing table readiness declarations are deliberately not promoted to completed
production status by these validators.

Read-only compatibility check against the pre-existing September evidence:
15 catalogue records validated; 2,400 observations round-tripped exactly, including
30 non-session records; all 15 available ADV20 results were unchanged. Five raw
cache checksums matched before and after. Three cached notional hand checks also
matched. No data were downloaded or rewritten. This bounded check reused the
pilot's conservative 13:15 CT cut-off assumption; it did not independently verify
production times, re-source FNDs or certify curves. Its calendar ends before the
required anchors, and it has no production experiment-target table.
