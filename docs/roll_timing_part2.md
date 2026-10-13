# Roll-timing engine (Part 2)

Implements [GitHub Issue #23](https://github.com/JacopoMichelacci/Futures_Class/issues/23), which carries roadmap number `[6]`. The later empirical comparison is [GitHub Issue #7](https://github.com/JacopoMichelacci/Futures_Class/issues/7) (`[10]`, Part 3). The two numbering systems must not be confused.

## Scope

Timing only. Every rule uses the same baseline outgoing/incoming pair taken from `build_baseline_path(...).roll_events`. Destination, capacity handling, rounding, target weights, benchmark and metric stay fixed. The shared `BASELINE` constant is read, never mutated.

| Rule | Roll decision session |
| --- | --- |
| `fixed_-5` (baseline) | 5 observed sessions before the outgoing anchor |
| `fixed_-10` | 10 observed sessions before the outgoing anchor |
| `volume_crossover` | first search-window session where lagged incoming cleared volume strictly exceeds outgoing |
| `oi_crossover` | first search-window session where lagged incoming open interest strictly exceeds outgoing |

## Interfaces ([src/rolling/rules.py](../src/rolling/rules.py))

| Function | Responsibility |
| --- | --- |
| `build_baseline_roll_pairs` | Frozen `RollPair`s from the baseline roll events. |
| `fixed_roll_date` | Decision session at `anchor_index + offset` on the explicit calendar. Rejects offsets outside the calendar, an ineligible outgoing contract at the decision, or an ineligible incoming contract when first held. |
| `lagged_volume_crossover`, `lagged_oi_crossover` | Each requests only its own statistic via the shared `ObservationStore.latest_common`. Publication cut-off, revisions and the source-age limit therefore follow the Section 0 policy. Returns the first qualifier plus a per-session audit trail. |
| `apply_roll_timing_rule` | Roll decision, decision cut-off, first incoming interval end, `fallback_flag` and trigger evidence for one pair and one rule. |
| `build_roll_timing_portfolio` | The baseline contract path with each pair switched at its timed decision. It has no sizing, capacity or rounding. It refuses any path that would hold a contract on or after its anchor. |

### Timing conventions

- **Decision and execution.** The roll decision on session *d* uses only information published by *d*'s decision cut-off. The incoming contract is first held over the interval (*d*, *d+1*]. The trade never earns the interval ending on *d*. This matches the baseline path, where a position row's `date` is the session after its `decision_date`.
- **Strict inequality.** Equal values never qualify.
- **Missing data.** A missing or unaligned pair observation is recorded as `no_aligned_publication_safe_pair`. It is never treated as a crossover. Missing OI does not affect the volume rule, and missing volume does not affect the OI rule.
- **First qualifier.** The search stops at the first qualifying session. Later qualifiers cannot replace it.

## Causal search boundary — proposed, pending team sign-off

Version 8 says to fall back to -5 "if no crossover occurs before eligibility ends". Taken literally, the -5 decision would depend on observations that arrive after -5. The proposal recorded on Issue #23:

1. **Search deadline = the baseline -5 decision session.** Only decision sessions strictly before it are searched.
2. **Search start = the first decision session on which the outgoing contract is held.**
3. If no session qualifies, roll at -5 with `fallback_flag=True`.

Under this proposal a crossover rule can only roll at or before -5, and the fallback never depends on later information. The code does not default either bound: `search_start` and `search_deadline` are required arguments. A deadline after the -5 session is rejected. The fallback tests and the demo use the proposed values and are labelled as pending sign-off.

## Validation

```bash
uv sync --locked
uv run --no-sync python -B -m unittest discover -s tests/unit -v   # 80 tests: 60 shared + 20 rolling
uv run --no-sync python -B scripts/run_roll_timing.py --fixtures
```

[tests/unit/test_rolling.py](../tests/unit/test_rolling.py) builds its own synthetic calendar, including a non-session weekday inside the roll window, plus contracts and observations. It also checks the four rules against the unchanged shared `phase_a.json` baseline pair. On that fixture, -5 reproduces the baseline event (2030-02-01), -10 rolls on 2030-01-25, and both crossovers roll on 2030-01-30 using source date 2030-01-29.

## Not in scope

- Real-data calendars, first-notice dates, publication timestamps and complete decision-date curves (Phase B, Issues #4/#5).
- Matched benchmark and implementation accounting (requires a matched synthetic experiment).
- Part 3 comparisons and any preferred-method selection (Issue #7).
