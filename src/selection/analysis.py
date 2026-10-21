"""Issue #14: destination-only experiment, with Section 0 accounting injected.

All portfolio returns MUST come from the shared accounting layer. This module
never synthesizes production prices, sizes contracts or changes the baseline.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Callable, Mapping, Sequence

RULES = ("next_deferred", "second_deferred", "target_45", "target_75")


@dataclass(frozen=True)
class DestinationInputs:
    baseline_path: object
    eligible_curve: object
    calendar: object
    catalogue: object
    benchmark_daily_returns: Mapping[str, float | None]
    candidate_dates: tuple[str, ...]
    reference_aum_usd: float


@dataclass(frozen=True)
class StrategyResult:
    """Return series must use shared exact-contract P&L / frozen sizing.

    `daily_returns` keys are return-interval END dates. None means unavailable.
    """
    daily_returns: Mapping[str, float | None]
    maturity_sessions: tuple[float, ...] = ()
    roll_dates: tuple[str, ...] = ()


def _finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value)


def _baseline_event_identity(events):
    return tuple((e["date"], e["commodity"], e["outgoing_contract"],
                  e["baseline_destination"], e["anchor"]) for e in events)


def build_paths(inputs: DestinationInputs):
    """Construct each destination-only path from the same baseline inputs."""
    from src.selection.rules import (
        build_destination_schedule, apply_destination_rule_to_baseline
    )
    baseline = inputs.baseline_path
    baseline_events = _baseline_event_identity(baseline.roll_events)
    paths, schedules = {}, {}
    for rule in RULES:
        schedule = build_destination_schedule(
            baseline, inputs.eligible_curve, inputs.calendar, inputs.catalogue, rule
        )
        if _baseline_event_identity(schedule.events) != baseline_events:
            raise ValueError(f"{rule} changed baseline roll-event identity")
        path = apply_destination_rule_to_baseline(
            baseline, schedule, inputs.eligible_curve, inputs.calendar, inputs.catalogue
        )
        if tuple(p["decision_date"] for p in path.positions) != tuple(
                p["decision_date"] for p in baseline.positions):
            raise ValueError(f"{rule} changed baseline decision dates")
        if tuple(p["date"] for p in path.positions) != tuple(
                p["date"] for p in baseline.positions):
            raise ValueError(f"{rule} changed baseline position interval dates")
        paths[rule], schedules[rule] = path, schedule
    return paths, schedules


def compare_daily(
    returns_by_rule: Mapping[str, Mapping[str, float | None]],
    benchmark: Mapping[str, float | None],
    candidate_dates: Sequence[str],
    reference_aum_usd: float,
    *,
    min_dates: int = 100,
    min_survival: float = 0.70,
    tie_bps: float = 0.1,
    tie_priority: Sequence[str] | None = None,
    diagnostics: Mapping[str, Mapping] | None = None,
):
    """Compute a single intersection mask and realized daily tracking RMSE.

    The reference AUM is validated and reported; the shared accounting function
    must already have applied it to ALL portfolio and benchmark returns.
    """
    if not _finite(reference_aum_usd) or reference_aum_usd <= 0:
        raise ValueError("Frozen reference AUM must be positive and finite")
    if set(returns_by_rule) != set(RULES):
        raise ValueError("Require exactly four pre-specified destination rules")
    if not (0 <= min_survival <= 1) or min_dates < 1 or tie_bps < 0:
        raise ValueError("Invalid frozen selection or coverage parameters")
    dates = tuple(dict.fromkeys(candidate_dates))
    if not dates:
        raise ValueError("Frozen candidate dates are empty")
    common = tuple(d for d in dates if _finite(benchmark.get(d))
                   and all(_finite(returns_by_rule[r].get(d)) for r in RULES))
    survival = len(common) / len(dates)
    coverage_ok = len(common) >= min_dates and survival >= min_survival
    summary = []
    diagnostics = diagnostics or {}
    for rule in RULES:
        rmse = (10000 * sqrt(sum((returns_by_rule[rule][d] - benchmark[d]) ** 2
                                 for d in common) / len(common))) if common else None
        extras = diagnostics.get(rule, {})
        summary.append({
            "rule": rule, "tracking_rmse_bps": rmse,
            "common_valid_dates": len(common), "candidate_dates": len(dates),
            "survival_fraction": survival, "coverage_passed": coverage_ok,
            "reference_aum_usd": reference_aum_usd,
            "roll_count": extras.get("roll_count"),
            "fallback_count": extras.get("fallback_count"),
            "mean_maturity_sessions": extras.get("mean_maturity_sessions"),
        })
    preferred, unresolved = None, False
    if coverage_ok:
        best = min(row["tracking_rmse_bps"] for row in summary)
        tied = [row["rule"] for row in summary
                if row["tracking_rmse_bps"] - best < tie_bps]
        if len(tied) == 1:
            preferred = tied[0]
        elif tie_priority is None:
            unresolved = True
        else:
            if len(tie_priority) != len(RULES) or set(tie_priority) != set(RULES):
                raise ValueError("Tie priority must be a complete frozen ordering")
            preferred = next(rule for rule in tie_priority if rule in tied)
    daily = []
    for day in dates:
        valid = day in common
        row = {"date": day, "in_common_mask": valid,
               "benchmark_return": benchmark.get(day)}
        for rule in RULES:
            ret = returns_by_rule[rule].get(day)
            row[f"{rule}_return"] = ret
            row[f"{rule}_tracking_error_bps"] = (
                10000 * (ret - benchmark[day]) if valid else None
            )
        daily.append(row)
    return {"summary": summary, "daily": daily, "preferred_rule": preferred,
            "tie_unresolved": unresolved, "coverage_passed": coverage_ok,
            "common_dates": common}


def run_experiment(
    inputs: DestinationInputs,
    evaluate_path: Callable[[str, object, float], StrategyResult],
    *,
    min_dates: int = 100,
    min_survival: float = 0.70,
    tie_bps: float = 0.1,
    tie_priority: Sequence[str] | None = None,
):
    """`evaluate_path` MUST use Section 0 shared sizing, P&L and fixed AUM.

    One callback invocation per rule, with the same AUM. Never substitute a
    simplified P&L implementation for the production callback.
    """
    paths, schedules = build_paths(inputs)
    evaluated, diagnostics = {}, {}
    for rule in RULES:
        result = evaluate_path(rule, paths[rule], inputs.reference_aum_usd)
        if not isinstance(result, StrategyResult):
            raise TypeError("evaluate_path must return StrategyResult")
        evaluated[rule] = result.daily_returns
        events = schedules[rule].events
        diagnostics[rule] = {
            "roll_count": len(events),
            "fallback_count": sum(bool(e.get("fallback_flag")) for e in events),
            "mean_maturity_sessions": (
                sum(result.maturity_sessions) / len(result.maturity_sessions)
                if result.maturity_sessions else None),
        }
    comparison = compare_daily(
        evaluated, inputs.benchmark_daily_returns, inputs.candidate_dates,
        inputs.reference_aum_usd, min_dates=min_dates,
        min_survival=min_survival, tie_bps=tie_bps,
        tie_priority=tie_priority, diagnostics=diagnostics,
    )
    comparison["paths"] = paths
    comparison["schedules"] = schedules
    return comparison
