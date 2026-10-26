# Section 5: integrated portfolio

**Real Databento GLBX.MDP3-derived frozen Section 0 inputs.** The four independently selected rules are combined once, in the locked order (destination → timing → capacity → rounding). Nothing is re-optimised or reselected.

## Selected rules (read from each strand's committed outputs)

| Strand | Owner | Selected rule | Reference AUM | Strand baseline → selected (bp) | Reproduced here |
|---|---|---|---|---|---|
| Destination | Varshini | 75-session target | $10m | 7.2581 → 5.6226 | yes |
| Timing | Lucie | OI crossover | $10m | 7.2581 → 6.2694 | yes |
| Capacity | Jacopo | Sequential two-deferred overflow | $1bn | 87.9814 → 57.2827 | yes |
| Rounding | Alan | Toward zero | $1m | 8.0219 → 7.6642 | yes |

## Baseline vs integrated (Tracking RMSE, daily bp)

| AUM | Baseline | Integrated | Change | Destination + timing only (baseline sizing) | Status |
|---|---:|---:|---:|---:|---|
| $1m | 8.021906 | 13.178885 | +64.3% | 8.132852 | evaluated |
| $10m | 7.258071 | 8.365115 | +15.3% | 8.066155 | evaluated |
| $100m | 7.248176 | 9.150402 | +26.2% | 17.804468 | evaluated |
| $1bn | — | — | — | — | not evaluable: overflow candidate(s) outside the frozen Section 0 statistics universe: ZSK7 |

## Conclusion

Each selected rule improved tracking on its own, but the integrated implementation tracks the benchmark **less** closely than the original baseline at every evaluable AUM. Combining the rules without re-optimising produces an interaction: the 75-session destination already moves holdings out the curve, and OI crossover then rolls on again early. Mean sessions to first notice of the held contract: baseline 28.9, destination alone 70.0, integrated 82.4, against the benchmark's 60.

## Integration anchor and interactions (descriptive only)

- Team decision on #16 (Option A): the destination rule's scheduled roll date is the timing deadline and fallback. OI crossover searches causally from the first session the outgoing contract is held in the destination path up to, strictly before, that date. After the first roll this deadline is not '-5' from the outgoing contract's own first notice date.
- 2 roll(s) fired on the first permitted session: ZCU6→ZCZ6 (41 sessions before the scheduled date), ZSQ6→ZSX6 (41 sessions before the scheduled date). Corn and soybeans therefore move roughly from May to December/November in late April.
- Each strand's winner alone, run through this same pipeline, reproduces its Part 3 result (table above), so the difference comes from the combination, not the implementation.

## Limitations

- $1bn is not evaluable: sequential overflow from the integrated ZSF7/ZSH7 positions needs ZSK7, which is outside the frozen Section 0 statistics universe. No substitute data were invented.
- Findings are conditional on this frozen sample. There are no transaction costs or collateral yield, and strand selections were made independently at different reference AUMs.

## Reproduction

```sh
uv sync --locked
uv run --no-sync python -B scripts/run_section5.py
```

Notebook: [Section 5.ipynb](../../Section%205%20-%20Integrated%20Portfolio/Section%205.ipynb).
