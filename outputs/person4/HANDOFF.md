# Person 4: independent empirical rounding analysis

**PERSON 4 ROUNDING HANDOFF READY**

Real Databento GLBX.MDP3-derived frozen Section 0 inputs, not synthetic fixture results.

## Frozen experiment

- Issue #8; only integer rounding changes. Destination, -5 baseline timing, baseline capacity, weights, benchmark and accounting remain fixed.
- 148 identical common return dates: 2 March–30 September 2026. Initial decision: 27 February; earlier data are warm-up only.
- Reference AUM: $1m, with no binding capacity in the frozen reference targets. Core grid: $1m/$10m/$100m/$1bn. No $250k fallback is activated.
- Exact post-capacity fractional targets and ceilings are consumed without re-sizing or reapplying capacity. Nearest reproduces frozen baseline holdings and daily returns exactly.
- Pre-existing exact-contract holdings, publication-safe provenance and a shared fixed-AUM denominator are enforced. Every method must retain the full frozen mask before any metric is computed.

## Results

Tracking RMSE in daily basis points, without annualisation. CSVs retain full decimal precision; this table is rounded for display.

| AUM | Nearest | Toward zero | Away from zero | Residual greedy |
|---|---:|---:|---:|---:|
| $1,000,000 | 8.021905648 | 7.664236707 | 12.462801021 | 8.021905648 |
| $10,000,000 | 7.258070814 | 6.929644991 | 7.618796920 | 7.258070814 |
| $100,000,000 | 7.248176217 | 7.217197002 | 7.286009788 | 7.247606857 |
| $1,000,000,000 | 87.981363407 | 87.981363407 | 87.981363407 | 87.981363407 |

## Reference-only selection

Preferred rule at $1m: **toward_zero**.
Tie rule triggered: **False**. Rules strictly within 0.1 daily bp of the minimum: toward_zero.
Selection reason: `unique_best_outside_tie_tolerance`. Exactly 0.1 bp is not a tie. Other AUMs do not select the preferred rule.

## Interpretation and limitations

Residual greedy has no shared notional-budget constraint. Its locked squared dollar-notional objective is separable; exact-half targets are the principal distinction from nearest-half-up because a zero-improvement addition is rejected. The engine is unchanged.
Observed greedy-vs-nearest position differences (diagnostic only): $1,000,000: 0/444, $10,000,000: 0/444, $100,000,000: 2/444, $1,000,000,000: 0/444.
Absolute benchmark tracking error also reflects the baseline maturity/timing/capacity implementation held fixed across methods; it is not a rounding-only error decomposition. At large AUM, capacity truncation may make post-capacity targets integral and collapse differences between rounding rules.
Findings are conditional on this frozen historical sample and implementation assumptions. No transaction costs or collateral yield enter ranking; robustness AUMs are descriptive. No other strand's preferred method is imported, and Section 5 has not been run.

## Reproduction and outputs

```sh
uv sync --locked
uv run --no-sync python -B scripts/run_person4.py --real
```

The CLI regenerates numeric outputs and the Plotly specification. The PNG is a browser export, not rerendered by Python. For a fresh export without extra packages, use `--chart-html personal/rounding_chart.html`, open that self-contained file offline and use the camera button. Register the downloaded current-figure PNG with `--chart-png PATH`. The receipt detects later changes to either the figure specification or PNG; it does not independently certify image content.

Canonical notebook: [Section 4.ipynb](../../Section%204%20-%20Rounding%20(Alan)/Section%204.ipynb).
- `rounding_summary.csv`: reference-AUM results and tie/selection flags.
- `rounding_by_aum.csv`: all 16 rule/AUM results and descriptive diagnostics.
- `rounding_daily.csv`: common-date returns and tracking differences for reconciliation.
- `rounded_holdings.csv`: exact implemented quantities and decision provenance.
- `tracking_rmse_vs_aum.png`: primary comparison chart; selection point is $1m only.
- `rounding_figure.json` and `chart_export.json`: Plotly specification and exported-image hash receipt.
- `analysis_manifest.json`: frozen input hashes, selection and output hashes.

The `--fixtures` CLI mode remains a separate synthetic Part 2 demonstration and is not used for these outputs.
