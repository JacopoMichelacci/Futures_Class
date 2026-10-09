"""Exact-contract daily dollar P&L and a single fixed-AUM common comparison."""
from dataclasses import dataclass
from decimal import Decimal

from src.validation.schemas import ContractError, DEFERRED_STRATEGY_DAILY_FIELDS, decimal_value


@dataclass(frozen=True)
class DailyComparison:
    date: str
    decision_date: str
    decision_cutoff: str
    fixed_aum: Decimal
    common_valid: bool
    pnl: dict[str, Decimal] | None
    returns: dict[str, Decimal] | None
    invalid_reasons: tuple[str, ...]


def portfolio_pnl(holdings, observations, dates, strategies, fixed_aum,
                  snapshot_end_exclusive, decision_validity=None) -> tuple[DailyComparison, ...]:
    """Value pre-existing holdings over adjacent supplied sessions.

    All compared portfolios share the exact same valid-date mask and denominator.
    Optional decision_validity maps (date, strategy) to bool; absent entries in a
    supplied map are invalid. It lets downstream engines flag unavailable inputs.
    Prices observed later are valuation marks, never position-decision inputs.
    """
    aum = decimal_value(fixed_aum, "fixed_aum")
    if aum <= 0:
        raise ContractError("One positive fixed AUM is required for the whole comparison")
    dates, strategies = tuple(dates), tuple(strategies)
    if not dates or list(dates) != sorted(set(dates)) or len(strategies) < 2 or len(set(strategies)) != len(strategies):
        raise ContractError("Ordered unique dates and distinct benchmark/strategy names required")
    if holdings.benchmark_strategy not in strategies:
        raise ContractError("Comparison must include the benchmark")
    if holdings.calendar.calendar_id != observations.calendar.calendar_id:
        raise ContractError("Holdings and prices must share the same calendar and cut-offs")
    for alias, identity in observations.identities.items():
        if alias in holdings.catalogue and holdings.catalogue[alias].identity != identity:
            raise ContractError("Observation and holdings contract identities differ")
    daily = []
    for day in dates:
        context = holdings.calendar.decision(day)
        positions, reasons = {}, []
        for strategy in strategies:
            snapshot = holdings.snapshot(day, strategy)
            if snapshot is None:
                reasons.append(f"{strategy}: missing holdings snapshot")
            else:
                positions[strategy] = snapshot
            if decision_validity is not None and decision_validity.get((day, strategy)) is not True:
                reasons.append(f"{strategy}: decision inputs unavailable or unvalidated")
        endpoints = observations.return_inputs(positions, day, snapshot_end_exclusive)
        reasons.extend(f"{r['contract']} {r['date']}: {r['reason']}" for r in endpoints["missing_settlements"])
        if reasons:
            daily.append(DailyComparison(day, context.decision_date, context.cutoff_utc, aum, False, None, None, tuple(reasons)))
            continue
        pnl = {}
        for strategy, snapshot in positions.items():
            total = Decimal(0)
            for contract, quantity in snapshot.items():
                if quantity == 0:
                    continue
                old, new = endpoints["price_endpoints"][contract]
                change = new.observation.statistic_value - old.observation.statistic_value
                total += quantity * holdings.catalogue[contract].dollars_per_price_unit * change
            pnl[strategy] = total
        daily.append(DailyComparison(day, context.decision_date, context.cutoff_utc, aum, True, pnl,
                                     {s: value / aum for s, value in pnl.items()}, ()))
    return tuple(daily)


def daily_records(daily, strategies, benchmark="benchmark"):
    """Version 8 column-compatible rows; uncomputed diagnostics remain explicit nulls.

    Phase A does not calculate notional-share or production quality diagnostics.
    Their reserved fields are unknown, not zero or a claim that quality passed.
    """
    records = []
    for row in daily:
        for strategy in strategies:
            if strategy == benchmark:
                continue
            intended = row.returns[benchmark] if row.common_valid else None
            implemented = row.returns[strategy] if row.common_valid else None
            error = implemented - intended if row.common_valid else None
            records.append({"date": row.date, "strategy": strategy,
                            "benchmark_daily_return": intended, "strategy_daily_return": implemented,
                            "daily_tracking_difference": error,
                            "squared_tracking_difference": error * error if error is not None else None,
                            "common_valid_date": row.common_valid, "fixed_aum": row.fixed_aum,
                            "invalid_reasons": row.invalid_reasons,
                            **{name: None for name in DEFERRED_STRATEGY_DAILY_FIELDS}})
    return records
