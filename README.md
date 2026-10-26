# Implementation Fidelity in an Agricultural Futures Portfolio

**Headline.** We tested four practical choices in running an equal-weight corn, soybean and wheat futures portfolio: which contract to roll into, when to roll, what to do when markets are too thin for the position, and how to round to whole contracts. **Each selected rule, tested alone, tracked our benchmark better than the standard baseline.** **Combined once, without re-optimisation, they made tracking worse at every portfolio size we could evaluate.** The integrated portfolio had 8.37 bp daily tracking error versus 7.26 bp for the baseline at $10m, and was worse by 15–64% across $1m–$100m. The reason is an interaction. Rolling further out and rolling earlier each move holdings toward the benchmark's maturity, but together they overshoot it: an average of 82 trading sessions to first notice, against the benchmark's 60. Implementation choices therefore need validating in combination, not only one at a time.

## What we studied

The portfolio holds CBOT Corn (ZC), Soybeans (ZS) and Chicago SRW Wheat (ZW) at equal dollar notional. Our **benchmark** is an idealised, fractional portfolio that holds each commodity at a constant **60 observed trading sessions** to the contract's first notice date (FND). It interpolates between bracketing contracts and has no capacity or rounding constraints. A real portfolio departs from this benchmark through four implementation choices, each studied by one team member against a common **baseline**: nearest contract, roll into the next deferred five sessions before FND, cap positions at 2.5% of lagged 20-day volume, and round to the nearest contract.

## Data and method

Daily CME settlements, cleared volume and open interest come from **Databento (GLBX.MDP3)**. Section 0 validated contract metadata and sourced FNDs (never substituting expiration), built the benchmark and baseline, and froze one sample of **148 common trading days, 2 March–30 September 2026**. Every decision uses only data published before that day's cut-off. P&L uses exact-contract settlement changes and one fixed AUM. The sole metric is **daily-return Tracking RMSE in basis points** against the benchmark (lower is better). Differences below 0.1 bp count as ties, and ties go to the simpler rule.

Each strand changed only its own choice, compared four pre-specified rules, and selected one at a reference AUM frozen before results were seen ($10m for destination and timing, $1bn for capacity, $1m for rounding). Section 5 then combined the four winners once, in a fixed order (destination → timing → capacity → rounding). When combined, the destination's scheduled roll date serves as the timing deadline.

## Results

**Each strand's selected rule versus its baseline:**

| Strand | Selected rule | Reference AUM | Baseline → selected (daily bp) |
|---|---|---|---|
| Destination | Contract closest to 75 sessions from FND | $10m | 7.26 → **5.62** |
| Timing | Roll when next contract's open interest overtakes | $10m | 7.26 → **6.27** |
| Capacity | Spill excess into the next two deferred contracts | $1bn | 87.98 → **57.28** |
| Rounding | Round toward zero | $1m | 8.02 → **7.66** |

**Integrated portfolio (all four rules) versus the original baseline:**

| AUM | Baseline | Integrated | Change |
|---|---:|---:|---:|
| $1m | 8.02 | 13.18 | +64% |
| $10m | 7.26 | 8.37 | +15% |
| $100m | 7.25 | 9.15 | +26% |
| $1bn | — | not evaluable | see limitations |

Each winner run alone through the Section 5 pipeline reproduces its strand's result exactly, so the deterioration comes from the combination rather than the code.

## Interpretation and limitations

The two maturity rules compound. The 75-session destination moves corn to September and soybeans to August; open-interest timing then sees the next, more liquid contract (December corn, November soybeans) already leading and rolls on immediately. With destination and timing alone, tracking is already worse than baseline at $10m (8.07 bp). At $1m, where positions are only about 5–15 contracts per commodity, always rounding down adds most of the remaining gap. At $100m, the far-dated contracts are thinly traded, so capacity binds, and the selected overflow rule recovers much of the loss. A related finding in the timing strand alone: thin September contracts triggered crossovers immediately, so the crossover rules largely skipped them.

Limitations: the **$1bn integrated result is not evaluable**, because overflow from the integrated soybean positions requires a contract (ZSK7) outside the frozen data universe. We did not substitute data. Results come from one seven-month sample, exclude transaction costs and collateral yield, and the strand rules were chosen independently at different reference AUMs and deliberately not re-optimised jointly.

---

## Repository guide

| Path | Contents |
|---|---|
| `Section 5 - Integrated Portfolio/Section 5.ipynb` | **Presentation notebook**: four strand summaries, integration, conclusion |
| `Section 0`–`Section 4` notebooks | Setup and each strand's analysis (destination, timing, capacity, rounding) |
| `data/section0/` | Frozen, hash-verified processed Databento bundle (~1.7 MB) used by every analysis |
| `outputs/person1`–`person4`, `outputs/final` | Strand results and Section 5 results (CSVs, charts, `HANDOFF.md`) |
| `src/` | Shared engines (`selection`, `rolling`, `capacity`, `rounding`, `integration`, `section0`, `metrics`) |
| `docs/` | Design notes for Section 0 and each strand |

## Reproducing the analysis

Requires [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync --locked
uv run --no-sync python -B -m unittest discover -s tests/unit -v          # full test suite

uv run --no-sync python -B scripts/run_section0.py --reproduce --output-dir personal/section0-reproduced
uv run --no-sync python -B -m scripts.run_person1 --real                     # destination -> outputs/person1
uv run --no-sync python -B scripts/run_person2.py --real                    # timing      -> outputs/person2
uv run --no-sync python -B scripts/run_person3.py                           # capacity    -> outputs/person3
uv run --no-sync python -B scripts/run_person4.py --real                    # rounding    -> outputs/person4
uv run --no-sync python -B scripts/run_section5.py                          # integration -> outputs/final
```

All commands run offline from the committed `data/section0` bundle, which is processed from Databento GLBX.MDP3 CME data. No API key is needed. Section 0's shared loader verifies the bundle's input hashes, and these commands regenerate the committed summary tables exactly. To present, open `Section 5 - Integrated Portfolio/Section 5.ipynb` with the project's `.venv` interpreter as the kernel and run all cells.

**Rebuilding Section 0 from raw data.** Raw Databento downloads are intentionally not committed. The frozen bundle was built from locally acquired, validated production files with `scripts/run_section0.py --production-dir … --inventory-dir …` (see `docs/section0_phase_b.md`). Re-acquiring the raw data requires a Databento account with CME GLBX.MDP3 access and incurs data costs. Keep any `DATABENTO_API_KEY` in a local, git-ignored `.env`, never in the repository.
