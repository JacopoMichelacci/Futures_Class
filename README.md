# Towards Optimal Implementation of an Agricultural Futures Portfolio

## Project Overview

Maintaining an agricultural futures portfolio presents implementation challenges arising from contract expiry, maturity selection, roll timing, liquidity constraints and standardised contract sizes. These features can cause implemented positions to diverge from the intended economic exposure. We will investigate how alternative implementation rules affect this divergence while keeping the underlying portfolio allocation fixed. Our focus is implementation fidelity rather than return forecasting or asset allocation.

## Portfolio and Methodology

We will construct an approximately equally weighted portfolio of Corn (ZC), Soybeans (ZS) and Chicago SRW Wheat (ZW), measured by dollar notional, using CME/CBOT futures data obtained through Databento.

Our unconstrained benchmark will maintain a target maturity of 60 observed trading days to a common delivery-avoidance anchor, normally first notice date. It will interpolate between eligible contracts bracketing that target, permit fractional holdings and impose no capacity or rounding constraints. Where no bracket exists, it will use the closest eligible contract and flag the substitution.

The common baseline will begin with the nearest eligible contract and roll into the next deferred eligible contract five trading days before the outgoing contract's anchor. Positions will be subject to a fixed capacity ceiling based on 2.5% of lagged 20-day average volume and a one-day liquidation horizon. Residual exposure will remain unimplemented, with nearest-integer rounding applied after capacity constraints.

Each independent investigation will compare this baseline with three alternatives, holding all other assumptions constant.

| Investigation | Baseline | Three alternatives |
| --- | --- | --- |
| Contract destination | Next deferred eligible contract | Second deferred; deferred maturity closest to 45 trading days to anchor; closest to 75 trading days to anchor |
| Roll timing | Five trading days before anchor | Ten trading days before anchor; lagged-volume crossover; lagged-open-interest crossover |
| Liquidity-capacity handling | Truncate residual exposure | Overflow to next deferred; sequential overflow across next two deferred; lagged-average-volume-weighted overflow across next two deferred |
| Integer contract rounding | Nearest integer | Towards zero; away from zero; residual-greedy |

Our sole ranking metric will be realised daily return tracking RMSE, defined as the root mean squared difference between each implementation's daily return and that of the unconstrained benchmark. We will calculate daily dollar profit or loss from pre-existing positions and consecutive settlement changes for the exact contracts held, then divide both portfolio totals by the same fixed assets under management (AUM). Lower tracking RMSE, reported in daily basis points, indicates better historical implementation fidelity. We will use common valid evaluation dates within each comparison and verify settlement coverage before the empirical comparisons.

## Intended Outcome

We will combine the preferred method from each investigation, applying destination, timing, capacity handling and rounding in sequence, and compare the integrated implementation with the original baseline. We aim to establish how these decisions individually and jointly affect portfolio fidelity under the specified assumptions. The completed Python analysis will provide reproducible comparisons, tables and figures, with documented data limitations.

## Reproducing the Analysis

The repository contains [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock). The existing environment can be synchronised from the repository root:

```bash
uv sync --locked
```

Data acquisition will require Databento access and a locally configured `DATABENTO_API_KEY`. Our planned workflow will prepare and validate shared inputs, run the four investigations and integrate their preferred methods. The full analysis is not currently runnable; the end-to-end script below is planned and does not yet exist:

```bash
uv run python scripts/run_all.py
```
