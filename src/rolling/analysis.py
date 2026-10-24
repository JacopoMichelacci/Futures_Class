"""Part 3 roll-timing comparison (Issue #7): timing varies, everything else is supplied.

The experiment bundle is the Section 0 handoff boundary. Benchmark holdings and
the fixed sizing (target notional -> capacity -> rounding) are inputs, never
rebuilt here, so destination, capacity, rounding, benchmark and accounting stay
fixed while only the four pre-specified timing rules change. Accounting and the
metric are the shared ``portfolio_pnl`` and ``tracking_rmse_bps``.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from src.metrics.portfolio_pnl import portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.holdings import HoldingsBook
from src.rolling.rules import (RULES, apply_roll_timing_rule, build_baseline_roll_pairs,
                               build_roll_timing_portfolio, fixed_roll_date)
from src.validation.schemas import ContractError

BASELINE_RULE = "fixed_-5"
# Pre-specified tie preference agreed on #27 before any real-data run: baseline,
# the other data-free offset, then volume (direct trading activity) before OI.
SIMPLICITY = {"fixed_-5": 0, "fixed_-10": 1, "volume_crossover": 2, "oi_crossover": 3}


@dataclass(frozen=True)
class TimingExperiment:
    """Frozen inputs for one comparison; supplied by Section 0, not built here."""
    calendar: object
    catalogue: dict
    observations: object
    baseline_path: object
    benchmark_rows: tuple
    # Fixed sizing layer: (contract-path rows, strategy) -> implemented Holdings.
    size_path: Callable[[tuple, str], list]
    fixed_aum: Decimal
    snapshot_end_exclusive: str
    label: str
    dates: tuple = ()  # Frozen return dates; defaults to the benchmark dates.
    validity: dict | None = None  # Frozen common mask: date -> bool.


def crossover_search_window(baseline_path, pair, calendar, catalogue):
    """Boundary agreed on Issue #23: first session the outgoing is held -> -5 (exclusive)."""
    deadline = fixed_roll_date(pair, calendar, catalogue, -5)
    held = [r["decision_date"] for r in baseline_path.positions
            if r["commodity"] == pair.commodity and r["contract"] == pair.outgoing_contract]
    start = min(held) if held else deadline
    return min(start, deadline), deadline


def timing_decisions(experiment, rule):
    """One decision per unchanged baseline pair under ``rule``."""
    out = []
    for pair in build_baseline_roll_pairs(experiment.baseline_path):
        start, deadline = crossover_search_window(experiment.baseline_path, pair,
                                                  experiment.calendar, experiment.catalogue)
        out.append(apply_roll_timing_rule(pair, rule, experiment.calendar, experiment.catalogue,
                                          experiment.observations, start, deadline))
    return tuple(out)


def timing_holdings(experiment, rule, decisions):
    """Implemented rows for the timed contract path, sized by the supplied fixed rule."""
    strategy = f"timing:{rule}"
    path = build_roll_timing_portfolio(experiment.baseline_path, decisions,
                                       experiment.calendar, experiment.catalogue)
    return strategy, tuple(experiment.size_path(path, strategy))


def compare_timing_rules(experiment, min_common_dates=100, min_survival=Decimal("0.70")):
    """Run all four rules on one common date mask and one fixed AUM."""
    decisions, holdings, strategies = {}, {}, ["benchmark"]
    for rule in RULES:
        decisions[rule] = timing_decisions(experiment, rule)
        strategy, holdings[rule] = timing_holdings(experiment, rule, decisions[rule])
        strategies.append(strategy)
    implemented = [h for rows in holdings.values() for h in rows]
    book = HoldingsBook([*experiment.benchmark_rows, *implemented], experiment.calendar, experiment.catalogue)
    dates = list(experiment.dates) or sorted({r.date for r in experiment.benchmark_rows})
    validity = None
    if experiment.validity is not None:
        validity = {(d, s): experiment.validity.get(d) is True for d in dates for s in strategies}
    daily = portfolio_pnl(book, experiment.observations, dates, strategies, experiment.fixed_aum,
                          experiment.snapshot_end_exclusive, validity)
    rmse = tracking_rmse_bps(daily)
    valid = sum(r.common_valid for r in daily)
    survival = Decimal(valid) / Decimal(len(daily))
    summary = []
    for rule in RULES:
        rolls = decisions[rule]
        fallbacks = sum(d["fallback_flag"] for d in rolls)
        summary.append({
            "rule": rule, "tracking_rmse_bps": rmse[f"timing:{rule}"],
            "common_valid_dates": valid, "candidate_dates": len(daily), "roll_events": len(rolls),
            "fallback_events": fallbacks,
            "fallback_frequency": Decimal(fallbacks) / Decimal(len(rolls)) if rolls else None,
            "mean_sessions_before_anchor": (Decimal(sum(d["sessions_before_anchor"] for d in rolls))
                                            / Decimal(len(rolls))) if rolls else None,
        })
    gates = {"min_common_dates": min_common_dates, "min_survival": min_survival,
             "common_valid_dates": valid, "survival": survival,
             "passed": valid >= min_common_dates and survival >= min_survival}
    selection = select_preferred_rule({r["rule"]: r["tracking_rmse_bps"] for r in summary})
    if not gates["passed"]:
        # Spec: a failed coverage gate means no preferred method; RMSE stays reported.
        selection = {**selection, "preferred_rule": None, "resolved": False,
                     "blocked_reason": "coverage_gate_failed"}
    return {"label": experiment.label, "summary": tuple(summary), "decisions": decisions,
            "holdings": holdings, "gates": gates, "selection": selection, "daily": daily}


def select_preferred_rule(rmse_by_rule, tie_tolerance_bps=Decimal("0.1")):
    """Lowest daily-bp RMSE; differences strictly below the tolerance are ties.

    Ties go to the simplest rule in the agreed SIMPLICITY order, baseline first.
    """
    if set(rmse_by_rule) != set(RULES):
        raise ContractError("All four pre-specified timing rules are required")
    best = min(rmse_by_rule.values())
    tied = sorted((r for r, v in rmse_by_rule.items() if v - best < tie_tolerance_bps),
                  key=lambda r: (SIMPLICITY[r], RULES.index(r)))
    simplest = [r for r in tied if SIMPLICITY[r] == SIMPLICITY[tied[0]]]
    resolved = len(simplest) == 1
    return {"preferred_rule": simplest[0] if resolved else None, "tied_rules": tuple(tied),
            "lowest_rmse_rule": min(rmse_by_rule, key=lambda r: (rmse_by_rule[r], SIMPLICITY[r])),
            "resolved": resolved, "tie_tolerance_bps": tie_tolerance_bps}


def summary_records(result):
    preferred = result["selection"]["preferred_rule"]
    def fixed(value, places):
        return None if value is None else format(value, f".{places}f")

    return [{**row, "tracking_rmse_bps": fixed(row["tracking_rmse_bps"], 6),
             "fallback_frequency": fixed(row["fallback_frequency"], 4),
             "mean_sessions_before_anchor": fixed(row["mean_sessions_before_anchor"], 2),
             "survival": format(result["gates"]["survival"], ".4f"),
             "tied_with_best": row["rule"] in result["selection"]["tied_rules"],
             "preferred": row["rule"] == preferred, "experiment": result["label"]}
            for row in result["summary"]]


def roll_event_records(result):
    rows = []
    for rule, decisions in result["decisions"].items():
        for d in decisions:
            trigger = d["trigger"] or {}
            rows.append({"rule": rule, "commodity": d["commodity"], "outgoing_contract": d["outgoing_contract"],
                         "incoming_contract": d["incoming_contract"], "anchor": d["anchor"],
                         "roll_decision_date": d["roll_decision_date"],
                         "first_incoming_interval_end": d["first_incoming_interval_end"],
                         "sessions_before_anchor": d["sessions_before_anchor"], "fallback_flag": d["fallback_flag"],
                         "search_start": d["search_start"], "search_deadline": d["search_deadline"],
                         "trigger_source_date": trigger.get("source_date"),
                         "trigger_outgoing_value": trigger.get("outgoing_value"),
                         "trigger_incoming_value": trigger.get("incoming_value"),
                         "experiment": result["label"]})
    return rows
