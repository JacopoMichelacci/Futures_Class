"""Independent empirical rounding comparison on the frozen Section 0 handoff.

This module consumes targets and benchmark quantities. It never selects contracts,
sizes targets, applies capacity, rebuilds Section 0 or changes its common mask.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

from src.metrics.portfolio_pnl import portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.holdings import Holding, HoldingsBook
from src.rounding.rules import ROUNDING_METHODS, RoundingTarget, build_rounded_portfolio
from src.section0.phase_b import load_inputs, read_table, sha
from src.validation.schemas import ContractError, ROOTS, decimal_value, utc_ns


@dataclass
class RoundingExperiment:
    shared: object
    config: dict
    freeze: dict
    targets: list
    benchmark: list
    baseline: list
    baseline_daily: list
    input_hashes: dict


def require(condition, message):
    if not condition:
        raise ContractError(message)


def load_rounding_experiment(handoff_dir, config):
    """Validate the frozen mask and non-binding reference before any comparison."""
    path = Path(handoff_dir)
    hashes = json.loads((path / "output_hashes.json").read_text())
    for name, digest in hashes.items():
        require(Path(name).name == name and sha(path / name) == digest,
                f"Frozen Section 0 output hash mismatch: {name}")
    freeze = json.loads((path / "freeze.json").read_text())
    require(freeze["project_spec_version"] == config["project_spec_version"] == "8.0", "Version 8 required")
    require(freeze["preflight"]["core_aum_grid"] == config["aum_grid_usd"] == [1000000, 10000000, 100000000, 1000000000], "Frozen AUM grid changed")
    require(freeze["preflight"]["references"]["rounding"] == 1000000, "Frozen rounding reference must be $1m")
    dates = freeze["frozen_dates"]
    require(len(dates) == 148 and dates == sorted(set(dates)) and dates[0] == "2026-03-02" and dates[-1] == "2026-09-30", "Frozen date set changed")
    masks = [r for r in read_table(path / "common_masks.parquet") if r["strand"] == "rounding"]
    require([r["date"] for r in masks] == dates and all(r["common_valid_date"] and r["fixed_aum"] == 1000000 for r in masks), "Rounding common mask differs from frozen dates")
    preflight = [r for r in freeze["preflight"]["checks"] if r["scope"] == "rounding_baseline_targets" and r["fixed_aum"] == 1000000]
    require(len(preflight) == 1 and preflight[0]["binding_rows"] == 0, "Reference capacity must be non-binding")
    shared = load_inputs(path / "inputs", config)
    require(shared.dates == dates and shared.calendar.decision(dates[0]).decision_date == "2026-02-27", "First return/decision mismatch")
    require(shared.calendar.calendar_id == freeze["calendar_id"] and shared.catalogue.catalogue_id == freeze["catalogue_id"], "Frozen calendar/catalogue identity mismatch")
    targets = read_table(path / "post_capacity_targets.parquet")
    baseline = read_table(path / "baseline_holdings.parquet")
    benchmark = read_table(path / "benchmark_holdings.parquet")
    baseline_daily = read_table(path / "baseline_daily.parquet")
    expected = {(a, d, r) for a in config["aum_grid_usd"] for d in dates for r in ROOTS}
    require(len(targets) == len(expected) and {(r["fixed_aum"], r["date"], r["commodity"]) for r in targets} == expected, "Missing/duplicate commodity target; no implicit zero exposure")
    require(len(baseline) == len(expected) and {(r["fixed_aum"], r["date"], r["commodity"]) for r in baseline} == expected, "Incomplete frozen baseline")
    baseline_index = {(r["fixed_aum"], r["date"], r["commodity"]): r for r in baseline}
    for r in targets:
        b = baseline_index[(r["fixed_aum"], r["date"], r["commodity"])]
        require(r["contract"] == b["contract"], "Destination/timing identity changed")
        target = RoundingTarget(**{k: r[k] for k in RoundingTarget.__dataclass_fields__})
        require(target.target_weight == config["target_weights"][r["commodity"]] and target.target_notional == Decimal(r["fixed_aum"]) * target.target_weight, "Target notional/weight mismatch")
        for key in ("capacity_limit_contracts", "dollar_contract_notional", "pre_capacity_contracts", "implemented_contracts"):
            require(decimal_value(r[key]) == decimal_value(b[key]), f"Frozen baseline metadata mismatch: {key}")
        if r["fixed_aum"] == 1000000:
            require(not r["capacity_binding"] and decimal_value(r["pre_capacity_contracts"]) == target.post_capacity_fractional_contracts, "Reference input has capacity truncation")
    hashes = {**hashes, "inputs/hashes.json": sha(path / "inputs/hashes.json")}
    return RoundingExperiment(shared, config, freeze, targets, benchmark, baseline, baseline_daily, hashes)


def select_reference(scores, tolerance=Decimal("0.1")):
    """Select only at the caller's frozen reference, never by robustness results.

    Baseline wins a tie with the minimum. Independent floor/ceil are simpler
    than greedy. An otherwise unresolved floor/ceil tie is reported, not invented.
    """
    require(set(scores) == set(ROUNDING_METHODS), "Exactly four locked methods required")
    scores = {m: decimal_value(v) for m, v in scores.items()}
    require(all(v >= 0 for v in scores.values()) and tolerance == Decimal("0.1"), "Invalid metric or changed tie tolerance")
    best = min(scores.values())
    tied = [m for m in ROUNDING_METHODS if scores[m] - best < tolerance]
    if "nearest" in tied:
        chosen, reason = "nearest", "baseline_within_strict_0.1_bp_of_minimum"
    elif len(tied) == 1:
        chosen, reason = tied[0], "unique_best_outside_tie_tolerance"
    else:
        simple = [m for m in tied if m in ("toward_zero", "away_from_zero")]
        chosen = simple[0] if len(simple) == 1 else None
        reason = "simpler_independent_rule_within_tolerance" if chosen else "unresolved_floor_ceil_tie"
    return dict(preferred_rule=chosen, tied_rules=tied, tie_triggered=len(tied) > 1,
                minimum_rmse_bps=best, tolerance_bps=tolerance, reason=reason)


def require_frozen_dates(daily, dates, config):
    valid = [r.date for r in daily if r.common_valid]
    require(valid == dates, "Accounting changed the frozen common mask; stop before RMSE")
    require(len(valid) >= config["tracking_min_common_dates"] and
            Decimal(len(valid)) / len(dates) >= config["tracking_min_candidate_survival"],
            "Coverage gate failed; no method selection permitted")


def analyse_rounding(experiment):
    """Evaluate the existing four rules with shared exact-contract accounting."""
    x = experiment
    dates = x.freeze["frozen_dates"]
    calendar, catalogue = x.shared.calendar, x.shared.catalogue.contracts
    target_groups, base_groups, benchmark_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in x.targets: target_groups[(r["fixed_aum"], r["date"])].append(r)
    for r in x.baseline: base_groups[(r["fixed_aum"], r["date"])].append(r)
    for r in x.benchmark: benchmark_groups[(r["fixed_aum"], r["date"])].append(r)
    frozen_daily = {(r["fixed_aum"], r["date"]): r for r in x.baseline_daily}
    summaries, daily_rows, holdings_rows = [], [], []
    for aum in x.config["aum_grid_usd"]:
        holdings = []
        changed = {m: 0 for m in ROUNDING_METHODS}
        halves = binding = 0
        for day in dates:
            context = calendar.decision(day)
            original = target_groups[(aum, day)]
            targets = [RoundingTarget(**{k: r[k] for k in RoundingTarget.__dataclass_fields__}) for r in original]
            bases = base_groups[(aum, day)]
            # Preserve the shared decision provenance; do not re-size positions.
            formed_at = max((r["formed_at"] for r in bases), key=utc_ns)
            inputs_as_of = max((r["inputs_as_of"] for r in bases), key=utc_ns)
            require(utc_ns(inputs_as_of) <= utc_ns(formed_at) <= context.cutoff_ns, "Late decision input")
            baseline_quantities = {r["contract"]: decimal_value(r["implemented_contracts"]) for r in bases}
            halves += sum(t.post_capacity_fractional_contracts % 1 == Decimal("0.5") for t in targets)
            binding += sum(r["capacity_binding"] for r in original)
            for r in benchmark_groups[(aum, day)]:
                holdings.append(Holding(day, "benchmark", r["commodity"], r["contract"],
                    r["decision_cutoff"], r["decision_cutoff"], r["inputs_as_of"],
                    benchmark_fractional_contracts=r["fractional_contracts"]))
            require(bool(benchmark_groups[(aum, day)]), "Missing frozen benchmark")
            for method in ROUNDING_METHODS:
                book = build_rounded_portfolio(targets, method, date=day, formed_at=formed_at,
                    inputs_as_of=inputs_as_of, calendar=calendar, catalogue=catalogue)
                quantities = {h.contract: h.quantity for h in book.rows}
                require(set(quantities) == set(baseline_quantities), "Rounding altered contract identities")
                if method == "nearest":
                    require(quantities == baseline_quantities, "Nearest no longer reproduces frozen baseline")
                changed[method] += sum(quantities[s] != baseline_quantities[s] for s in quantities)
                holdings.extend(book.rows)
                holdings_rows.extend(dict(date=day, fixed_aum=aum, rule=method, commodity=h.commodity,
                    contract=h.contract, implemented_contracts=h.quantity,
                    decision_cutoff=h.decision_cutoff, inputs_as_of=h.inputs_as_of) for h in book.rows)
        combined = HoldingsBook(holdings, calendar, catalogue)
        names = ["benchmark"] + ["rounding_" + m for m in ROUNDING_METHODS]
        daily = portfolio_pnl(combined, x.shared.observations, dates, names, aum,
                              x.shared.provenance["snapshot_end_exclusive"])
        require_frozen_dates(daily, dates, x.config)
        for row in daily:
            original = frozen_daily[(aum, row.date)]
            require(row.returns["benchmark"] == decimal_value(original["benchmark_daily_return"]), "Benchmark return changed")
            require(row.returns["rounding_nearest"] == decimal_value(original["baseline_daily_return"]), "Baseline return changed")
        metrics = tracking_rmse_bps(daily)
        for method in ROUNDING_METHODS:
            summaries.append(dict(rule=method, fixed_aum=aum, tracking_rmse_bps_daily=metrics["rounding_" + method],
                common_valid_dates=len(dates), candidate_dates=len(dates), survival=Decimal(1),
                selection_reference=aum == x.freeze["preflight"]["references"]["rounding"],
                positions_different_from_nearest=changed[method], positions=len(dates) * 3,
                exact_half_targets=halves, capacity_binding_positions=binding))
        for row in daily:
            for method in ROUNDING_METHODS:
                name = "rounding_" + method
                daily_rows.append(dict(date=row.date, fixed_aum=aum, rule=method, common_valid_date=True,
                    benchmark_daily_return=row.returns["benchmark"], strategy_daily_return=row.returns[name],
                    daily_tracking_difference=row.returns[name] - row.returns["benchmark"]))
    reference = x.freeze["preflight"]["references"]["rounding"]
    selection = select_reference({r["rule"]: r["tracking_rmse_bps_daily"] for r in summaries if r["fixed_aum"] == reference},
                                  x.config["tracking_rmse_tie_tolerance_bps_daily"])
    selection["reference_aum"] = reference
    return dict(data_kind="real_frozen_databento_section0", by_aum=summaries,
                summary=[r for r in summaries if r["fixed_aum"] == reference], daily=daily_rows,
                holdings=holdings_rows, selection=selection, dates=dates,
                input_hashes=x.input_hashes, calendar_id=x.freeze["calendar_id"],
                catalogue_id=x.freeze["catalogue_id"], core_aum_grid=x.config["aum_grid_usd"])
