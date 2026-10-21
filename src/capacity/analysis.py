"""Part 3 capacity comparison across a fixed AUM grid (Issue #15).

Only the residual-allocation method changes.  Contract choices, target weights,
benchmark, rounding rule, calendar, valuation and the fixed AUM denominator
are supplied by the Section 0 handoff.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from src.capacity.rules import (CapacityMethod, CapacityPortfolio, build_capacity_portfolio,
                                capacity_rounding_targets)
from src.metrics.portfolio_pnl import portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.holdings import HoldingsBook
from src.rounding.rules import RoundingTarget, build_rounded_portfolio
from src.validation.schemas import ContractError, decimal_value


METHODS = tuple(CapacityMethod)
# Hard truncate is the pre-specified baseline.  The remaining order is the
# increasing amount of allocation logic, used only to resolve a metric tie.
SIMPLICITY = {
    CapacityMethod.HARD_TRUNCATE: 0,
    CapacityMethod.OVERFLOW_NEXT: 1,
    CapacityMethod.OVERFLOW_SEQUENTIAL: 2,
    CapacityMethod.OVERFLOW_ADV_WEIGHTED: 3,
}


@dataclass(frozen=True)
class CapacityExperiment:
    """Frozen inputs supplied by Section 0 for one capacity comparison.

    ``targets_for_aum`` returns the unchanged primary contract path and target
    weights, scaled only to the requested AUM. ``benchmark_for_aum`` returns
    the matching fractional benchmark holdings. Neither is rebuilt here.
    """
    calendar: object
    catalogue: dict
    observations: object
    eligible_curve: tuple[dict, ...]
    targets_for_aum: Callable[[Decimal], tuple[dict, ...]]
    benchmark_for_aum: Callable[[Decimal], tuple]
    snapshot_end_exclusive: str
    label: str
    rounding_method: str = "nearest"


def _strategy(method: CapacityMethod) -> str:
    return f"capacity:{method.value}"


def _rounded_rows(experiment: CapacityExperiment, portfolio):
    """Apply the one supplied rounding method to every date in a capacity output."""
    by_date: dict[str, list] = {}
    for row in portfolio.rows:
        by_date.setdefault(row.date, []).append(row)
    rows = []
    for day, capacity_rows in sorted(by_date.items()):
        daily_portfolio = CapacityPortfolio(portfolio.method, tuple(capacity_rows), ())
        targets = tuple(RoundingTarget(**record)
                        for record in capacity_rounding_targets(daily_portfolio))
        if not targets:
            raise ContractError(f"No capacity targets available for {day}")
        formed_at = max(row.formed_at for row in capacity_rows)
        inputs_as_of = max(row.inputs_as_of for row in capacity_rows)
        rounded = build_rounded_portfolio(
            targets, experiment.rounding_method, date=day, formed_at=formed_at,
            inputs_as_of=inputs_as_of, calendar=experiment.calendar,
            catalogue=experiment.catalogue, strategy=_strategy(portfolio.method),
        )
        rows.extend(rounded.rows)
    return tuple(rows)


def _method_portfolio(experiment: CapacityExperiment, aum: Decimal, method: CapacityMethod):
    targets = experiment.targets_for_aum(aum)
    if not targets:
        raise ContractError("Capacity analysis requires at least one supplied target")
    return build_capacity_portfolio(targets, experiment.eligible_curve, experiment.observations,
                                    experiment.catalogue, method)


def select_preferred_method(rmse_by_method, tie_tolerance_bps=Decimal("0.1")):
    """Select by daily-bp RMSE, with the Bible's strict-below tie threshold."""
    normalised = {CapacityMethod(key): decimal_value(value, "tracking_rmse_bps")
                  for key, value in rmse_by_method.items()}
    if set(normalised) != set(METHODS):
        raise ContractError("All four pre-specified capacity methods are required")
    tolerance = decimal_value(tie_tolerance_bps, "tie_tolerance_bps")
    if tolerance < 0:
        raise ContractError("Tie tolerance must be non-negative")
    best = min(normalised.values())
    tied = tuple(sorted((method for method, value in normalised.items() if value - best < tolerance),
                        key=lambda method: SIMPLICITY[method]))
    return {
        "preferred_method": tied[0],
        "tied_methods": tied,
        "lowest_rmse_method": min(normalised, key=lambda method: (normalised[method], SIMPLICITY[method])),
        "tie_tolerance_bps": tolerance,
    }


def compare_capacity_at_aum(experiment: CapacityExperiment, aum, min_common_dates=100,
                            min_survival=Decimal("0.70"), tie_tolerance_bps=Decimal("0.1")):
    """Compare all four allocation methods at one fixed AUM and common mask."""
    fixed_aum = decimal_value(aum, "aum")
    if fixed_aum <= 0:
        raise ContractError("AUM must be positive")
    portfolios = {method: _method_portfolio(experiment, fixed_aum, method) for method in METHODS}
    benchmark = tuple(experiment.benchmark_for_aum(fixed_aum))
    if not benchmark:
        raise ContractError("Capacity analysis requires supplied benchmark holdings")
    rows = list(benchmark)
    strategies = ["benchmark"]
    for method in METHODS:
        rows.extend(_rounded_rows(experiment, portfolios[method]))
        strategies.append(_strategy(method))
    book = HoldingsBook(rows, experiment.calendar, experiment.catalogue)
    dates = sorted({row.date for row in benchmark})
    daily = portfolio_pnl(book, experiment.observations, dates, strategies, fixed_aum,
                          experiment.snapshot_end_exclusive)
    rmse = tracking_rmse_bps(daily)
    valid = sum(row.common_valid for row in daily)
    survival = Decimal(valid) / Decimal(len(daily)) if daily else Decimal(0)
    summary = []
    for method in METHODS:
        diagnostics = portfolios[method].diagnostics
        summary.append({
            "method": method,
            "tracking_rmse_bps": rmse[_strategy(method)],
            "common_valid_dates": valid,
            "candidate_dates": len(daily),
            "survival": survival,
            "binding_target_count": sum("primary_capacity_binding" in d["flags"] for d in diagnostics),
            "unimplemented_notional": sum((d["unimplemented_notional"] for d in diagnostics), Decimal(0)),
        })
    gates = {"min_common_dates": min_common_dates, "min_survival": decimal_value(min_survival, "min_survival"),
             "common_valid_dates": valid, "survival": survival,
             "passed": valid >= min_common_dates and survival >= decimal_value(min_survival, "min_survival")}
    # A numerical RMSE is useful diagnostics after a coverage failure, but the
    # Bible does not permit a production method choice until both gates pass.
    selection = ({
        "preferred_method": None,
        "tied_methods": (),
        "lowest_rmse_method": None,
        "tie_tolerance_bps": decimal_value(tie_tolerance_bps, "tie_tolerance_bps"),
        "blocked_reason": "common_date_or_candidate_survival_gate_failed",
    } if not gates["passed"] else select_preferred_method(
        {row["method"]: row["tracking_rmse_bps"] for row in summary}, tie_tolerance_bps))
    return {"aum": fixed_aum, "summary": tuple(summary), "portfolios": portfolios,
            "daily": daily, "gates": gates, "selection": selection}


def compare_capacity_across_aum(experiment: CapacityExperiment, aum_grid, reference_aum,
                                fallback_reference_aum, min_common_dates=100,
                                min_survival=Decimal("0.70"), tie_tolerance_bps=Decimal("0.1")):
    """Run the required AUM grid and perform the $1bn/$2.5bn feasibility preflight."""
    grid = tuple(decimal_value(aum, "aum_grid") for aum in aum_grid)
    if not grid or any(aum <= 0 for aum in grid) or len(set(grid)) != len(grid):
        raise ContractError("AUM grid must contain distinct positive values")
    reference = decimal_value(reference_aum, "reference_aum")
    fallback = decimal_value(fallback_reference_aum, "fallback_reference_aum")
    reference_portfolio = _method_portfolio(experiment, reference, CapacityMethod.HARD_TRUNCATE)
    reference_binds = any("primary_capacity_binding" in row["flags"]
                          for row in reference_portfolio.diagnostics)
    active_reference = reference if reference_binds else fallback
    fallback_binds = None
    if not reference_binds:
        fallback_portfolio = _method_portfolio(experiment, fallback, CapacityMethod.HARD_TRUNCATE)
        fallback_binds = any("primary_capacity_binding" in row["flags"]
                             for row in fallback_portfolio.diagnostics)
    results = {aum: compare_capacity_at_aum(experiment, aum, min_common_dates, min_survival,
                                            tie_tolerance_bps) for aum in grid}
    reference_result = results.get(active_reference)
    if reference_result is None:
        reference_result = compare_capacity_at_aum(experiment, active_reference, min_common_dates,
                                                   min_survival, tie_tolerance_bps)
    return {
        "label": experiment.label,
        "results": results,
        "reference_result": reference_result,
        "preflight": {
            "configured_reference_aum": reference,
            "fallback_reference_aum": fallback,
            "reference_capacity_binds": reference_binds,
            "active_reference_aum": active_reference,
            "fallback_activated": not reference_binds,
            "fallback_capacity_binds": fallback_binds,
            "capacity_limitation": not reference_binds and not fallback_binds,
        },
    }


def summary_records(result):
    """Flat, serialisable AUM-by-method records for the required report table."""
    records = []
    for aum, comparison in sorted(result["results"].items()):
        selection = comparison["selection"]
        for row in comparison["summary"]:
            records.append({
                "experiment": result["label"], "aum_usd": format(aum, "f"),
                "method": row["method"].value,
                "tracking_rmse_bps": format(row["tracking_rmse_bps"], ".6f"),
                "common_valid_dates": row["common_valid_dates"],
                "candidate_dates": row["candidate_dates"],
                "survival": format(row["survival"], ".4f"),
                "binding_target_count": row["binding_target_count"],
                "unimplemented_notional": format(row["unimplemented_notional"], ".2f"),
                "tied_with_best": row["method"] in selection["tied_methods"],
                "preferred_at_aum": row["method"] == selection["preferred_method"],
                "common_mask_gate_passed": comparison["gates"]["passed"],
            })
    return records


def diagnostic_records(result):
    """One auditable capacity diagnostic row per target, AUM and method."""
    records = []
    for aum, comparison in sorted(result["results"].items()):
        for method, portfolio in comparison["portfolios"].items():
            for row in portfolio.diagnostics:
                records.append({
                    "aum_usd": format(aum, "f"), "method": method.value,
                    "date": row["date"], "commodity": row["commodity"],
                    "primary_contract": row["primary_contract"],
                    "primary_capacity_limit_contracts": row["primary_capacity_limit_contracts"],
                    "requested_notional": format(row["requested_notional"], "f"),
                    "primary_residual_notional": format(row["primary_residual_notional"], "f"),
                    "overflow_allocations": ";".join(f"{k}={v}" for k, v in row["overflow_allocations"].items()),
                    "unimplemented_notional": format(row["unimplemented_notional"], "f"),
                    "flags": ";".join(row["flags"]),
                })
    return records
