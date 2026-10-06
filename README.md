# Towards Optimal Implementation of an Agricultural Futures Portfolio

## Project Overview

Maintaining an agricultural futures portfolio presents implementation challenges arising from contract expiry, maturity selection, roll timing, liquidity constraints and standardised contract sizes. These features can cause implemented positions to diverge from their intended economic exposure. We will investigate how alternative implementation rules affect this divergence while keeping the underlying portfolio allocation fixed. Our focus is implementation fidelity rather than return forecasting or asset allocation.

## Portfolio and Methodology

We will construct a portfolio of Corn (ZC), Soybeans (ZS) and Chicago SRW Wheat (ZW), using CME/CBOT futures data obtained through Databento. We will target equal dollar-notional weights, with total intended notional equal to assets under management (AUM), representing an unlevered target exposure. Both the benchmark and implemented portfolios will be retargeted daily using information available before the next return interval.

We will examine four AUM levels: $1 million, $10 million, $100 million and $1 billion. Our historical evaluation period will be finalised following an initial assessment of data availability and settlement coverage.

Our unconstrained benchmark will target a constant maturity of 60 observed trading days to a delivery-avoidance anchor, normally the first-notice date. This fixed reference provides a consistent intended maturity exposure against which we can compare implementation choices, rather than assuming that 60 days is inherently optimal. We will interpolate between eligible contracts bracketing this maturity, permitting fractional holdings without capacity or rounding constraints. Contract-level first-notice dates will be verified using independent CME sources.

Our common baseline will initially hold the nearest eligible contract and roll into the next deferred eligible contract five trading days before the outgoing contract's anchor. We will impose a strict per-contract capacity limit of 2.5% of lagged 20-day average cleared volume over a one-day liquidation horizon, rounded down to the nearest whole contract. Any residual exposure will remain unimplemented, with nearest-integer rounding applied subject to the capacity limit.

Each investigation will compare this baseline with three alternatives, holding all other assumptions constant.

| Investigation | Baseline | Three alternatives |
| --- | --- | --- |
| Contract destination | Next deferred eligible contract | Second deferred; maturity closest to 45 trading days to anchor; maturity closest to 75 trading days to anchor |
| Roll timing | Five trading days before anchor | Ten trading days before anchor; lagged-volume crossover; lagged-open-interest crossover |
| Liquidity-capacity handling | Truncate residual exposure | Next-deferred overflow; sequential overflow across two deferred contracts; volume-weighted overflow across two deferred contracts |
| Integer contract rounding | Nearest integer | Towards zero; away from zero; residual-greedy |

For the crossover alternatives, we will execute a valid signal no earlier than the following trading session. Where no qualifying crossover occurs in time, we will roll at the baseline deadline, preventing delivery-period exposure.

Our sole ranking metric will be realised daily return tracking RMSE, calculated from the difference between each implementation's return and the unconstrained benchmark's return. Both returns will use pre-existing holdings, consecutive settlement changes for the exact contracts held and the same fixed AUM denominator. We will compare methods on common valid evaluation dates, with lower RMSE indicating better historical tracking. Transaction costs are excluded.

## Intended Outcome

We will combine the preferred method from each investigation, applying destination, timing, capacity handling and rounding in sequence. We will then compare the integrated implementation with the original baseline to assess how these choices individually and jointly affect historical portfolio fidelity. Our analysis will produce reproducible comparisons, tables and figures, with documented data limitations.

## Reproducing the Analysis

The repository contains `pyproject.toml` and `uv.lock`. We can synchronise the shared Python environment using:

```bash
uv sync --locked
```

Historical data acquisition will require Databento access and a locally configured `DATABENTO_API_KEY`. Our intended workflow will prepare and validate shared inputs, run the four investigations and integrate their preferred methods. The end-to-end script below is planned and does not yet exist:

```bash
uv run python scripts/run_all.py
```
