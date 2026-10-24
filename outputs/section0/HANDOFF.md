# Section 0 empirical handoff

**SECTION 0 HANDOFF READY** for the frozen Version 8 experiment on this branch.
This Issue #5 contribution builds on merged Phase A PR #18 and the validation
foundation now merged through PR #26. PR #29 is revalidated against exact team main
`ed97dcc7694de469052502db6348d7dccb867b0e`. All frozen bundle files remain unchanged;
the four strand engines match main exactly.

## Frozen decisions

| Item | Frozen value |
|---|---|
| Candidate/evaluation returns | 2 March–30 September 2026 |
| Initial decision | 27 February 2026; first interval (27 February, 2 March] |
| Warm-up | January/earlier February only; no January-seeded path |
| Common valid dates | **148/148, 100%, identical across all four strands** |
| Coverage gates | ≥100 dates and ≥70%; effective minimum 104 for 148 candidates |
| Roots / target weights | ZC 0.333333, ZS 0.333333, ZW 0.333334 |
| Destination/timing reference | **$10m**, declared fallback from $100m |
| Capacity reference | **$1bn**, no fallback needed |
| Rounding reference | **$1m**, no fallback needed |
| Reporting grid | $1m, $10m, $100m, $1bn; unchanged |
| Observation freeze | 2 October 2026 00:00 UTC, exclusive |
| Ranking policy for later Part 3 | Daily-bp tracking RMSE only; not calculated here |

The destination/timing feasibility envelope binds on 586/1,582 checked rows at
$100m and 0/1,582 at $10m. Baseline rounding targets are non-binding on 444/444
rows at $1m; capacity binds on 444/444 at $1bn. These checks were performed before
benchmark/baseline accounting, with no return or RMSE-based AUM choice. Neither
$250k nor $2.5bn is activated. Defaults remain in config; activated references
are recorded in the frozen bundle.

## Start here

Use the existing lockfile and project Python environment:

```sh
uv sync --locked
uv run --no-sync python -B scripts/run_section0.py --reproduce --output-dir personal/section0-reproduced
uv run --no-sync python -B -m unittest discover -s tests/unit -v
```

The reproduction command reads committed processed inputs only, makes no network
request, and refuses to overwrite an existing nonempty output directory. Its
`output_hashes.json` must equal the committed bundle's hash manifest. Raw
Databento files, credentials and the old pilot are unnecessary for consumers.

- [Frozen bundle and file inventory](../../data/section0/README.md)
- [Consumer API and boundary guide](../../docs/section0_phase_b.md)
- [Frozen dates, AUM decisions and identities](../../data/section0/freeze.json)
- [Coverage report](tracking_coverage_report.csv)
- [Accepted source hashes and conventions](../../data/section0/provenance.json)
- [Historical Phase A fixture guide](../../docs/section0_phase_a.md)

## Per-strand inputs

| Strand | Shared inputs | Team-owned work still required |
|---|---|---|
| Destination / #14 | Complete curves, catalogue/calendar, baseline path/events; shared path-sizing adapter; $10m benchmark and mask | Run own four destination rules, compare/select only in Part 3 |
| Timing / #7 | Baseline pairs, separate aligned volume/OI observations, calendar, shared path-sizing adapter; $10m benchmark and mask | Apply own four timing rules under signed-off Issue #23 boundary |
| Capacity / #15 | Baseline primary path, pre-capacity quantities/original targets, exact contract values, ADV20 and relative deferred metadata | Apply own four residual-handling rules; then unchanged rounding |
| Rounding / #8 | Baseline post-capacity fractional quantities, integer ceilings, preserved target metadata, benchmark/masks at each core AUM | Apply own four rounding rules; reference selection at $1m |

Do not rerun acquisition, choose a different sample, reconstruct the benchmark,
change target weights or infer new timing rules. The shared sizing helper uses
publication-safe prices, the fixed budget, 2.5% × lagged ADV20 × one day, and
baseline nearest-half-up rounding. Capacity changes only residual allocation;
rounding changes only the supplied post-capacity quantities.

## Coverage and accounting contract

Curves contain every active contract in the frozen 26-contract catalogue. Explicit
ineligibility is retained; a missing row is an error. The 22-statistics universe
covers benchmark, baseline and the accepted independent-strand exposure envelope.
ZSK7 is retained as metadata-only ranking evidence; the three H6 contracts are
explicitly ineligible at initialisation. No commodity is invented as zero.

All benchmark/strategy holdings for (t-1,t] are fixed using information available
by t-1's cut-off. Final settlement versions at the observation freeze are valuation
marks only. P&L uses the same exact contract at both consecutive endpoints and one
fixed AUM denominator. The mathematical benchmark is daily-retargeted, fractional,
uncapped and unrounded. The physical baseline rolls at -5 and is never held at or
after FND. Missing settlements invalidate the common date, not a zero return.

The mask conservatively requires valid potential inputs/endpoints across the
eligible statistics universe. Every frozen date passes at the final AUM settings
and across the core-grid baseline books. If a strand emits invalid holdings or
needs a new contract/date/AUM scope, reconcile and recheck before examining
performance; do not quietly shorten that method's mask.

`SessionCalendar` holds explicit historical cut-offs. Separate forward maturity
dates count sessions through the latest anchor without inventing future settlement
times. Good Friday and other known non-sessions never enter ADV20 windows. Volume
and OI remain independent rule inputs, each aligned and selected as of the cut-off.

The committed consumed-observation projection preserves the exact versions needed
for this freeze, not the full raw vendor history. It is restricted to the frozen
decision contexts; original raw-source hashes remain available for provenance.
All raw production downloads and earlier caches remain local and unchanged.

## Issue #5 completion boundary

Phase A interpolation/state-machine/accounting fixtures remain supported. This
contribution adds real curves, daily fractional benchmark, sized baseline and ten
roll events, conservative common masks, the declared AUM preflight, canonical
Parquet exports and clean offline reproduction. All fixture-level and Phase B
acceptance criteria are covered by the shared suite and frozen artifacts.

No alternative performance comparison, empirical RMSE winner, preferred-method
selection or Section 5 integration is included. Part 3 engine PRs remain separate.
The broader #11 daily snapshot declaration is not misrepresented as a replacement
for publication-aware history, and #11 is not closed by this contribution.
