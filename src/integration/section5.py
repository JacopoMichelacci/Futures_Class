"""Section 5 (Issue #16): combine the four independently selected rules once.

Locked composition order: destination at each scheduled roll event -> timing for
that outgoing/destination pair -> capacity relative to the held primary contract
-> rounding of the post-capacity quantities. No joint search, no reselection.

Integration anchor (team decision on #16, "Option A"): the destination rule's
scheduled roll date is the timing deadline and fallback. The crossover search runs
causally from the session the outgoing contract is first held in the destination
path (the Part 3 convention) up to, strictly before, that scheduled date. On the
first roll this equals the Part 3 -5 rule.
"""
import csv
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from src.capacity.rules import (CapacityMethod, CapacityPortfolio, build_capacity_portfolio,
                                capacity_rounding_targets, compute_adv20, decision_time_dollar_contract_value,
                                deferred_overflow_candidates, integer_capacity_limit)
from src.contracts.eligibility import is_eligible
from src.metrics.portfolio_pnl import portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.baseline import BaselinePath
from src.portfolio.holdings import Holding, HoldingsBook
from src.rolling.rules import RollPair, lagged_oi_crossover, lagged_volume_crossover
from src.rounding.rules import RoundingTarget, build_rounded_portfolio
from src.section0.phase_b import load_inputs, read_table, sha
from src.selection.rules import apply_destination_rule_to_baseline, build_destination_schedule
from src.validation.schemas import ContractError, ROOTS, decimal_value

STRANDS = ("destination", "timing", "capacity", "rounding")
CROSSOVERS = {"volume_crossover": lagged_volume_crossover, "oi_crossover": lagged_oi_crossover}
BASELINE_SIZING = {"capacity": "hard_truncate", "rounding": "nearest"}


def _require(condition, message):
    if not condition:
        raise ContractError(message)


def _rows(path):
    with Path(path).open() as stream:
        return list(csv.DictReader(stream))


def read_strand_selections(outputs_dir):
    """Each strand's frozen preferred rule, read from its own committed outputs."""
    out = Path(outputs_dir)
    handoff = (out / "person1/HANDOFF.md").read_text()
    destination = next((line.split(":", 1)[1].strip() for line in handoff.splitlines()
                        if line.startswith("Preferred rule:")), None)
    person1 = _rows(out / "person1/contract_destination_summary.csv")
    _require(destination == min(person1, key=lambda r: float(r["tracking_rmse_bps"]))["rule"],
             "Destination HANDOFF and summary disagree")
    timing = [r["rule"] for r in _rows(out / "person2/roll_timing_summary.csv") if r["selected"] == "True"]
    capacity = [r["method"] for r in _rows(out / "person3/capacity_summary.csv")
                if r["preferred_at_aum"] == "True" and int(r["aum_usd"]) == 1000000000]
    rounding = [r["rule"] for r in _rows(out / "person4/rounding_summary.csv") if r["selected"] == "True"]
    _require(len(timing) == len(capacity) == len(rounding) == 1, "Each strand must report exactly one selection")
    return {"destination": destination, "timing": timing[0], "capacity": capacity[0], "rounding": rounding[0]}


@dataclass(frozen=True)
class IntegrationInputs:
    shared: object
    curve: tuple
    baseline_path: BaselinePath
    dates: tuple
    benchmark: dict  # aum -> tuple[Holding]
    baseline: dict  # aum -> tuple[Holding]
    baseline_daily: dict  # (aum, date) -> frozen row
    aum_grid: tuple
    output_hashes: dict

    @property
    def calendar(self):
        return self.shared.calendar

    @property
    def catalogue(self):
        return self.shared.catalogue.contracts


def load_integration_inputs(handoff_dir, config):
    """Verify and read the frozen Section 0 handoff; nothing is rebuilt."""
    path = Path(handoff_dir)
    hashes = json.loads((path / "output_hashes.json").read_text())
    for name, digest in hashes.items():
        _require(sha(path / name) == digest, f"Frozen Section 0 output hash mismatch: {name}")
    freeze = json.loads((path / "freeze.json").read_text())
    _require(freeze.get("project_spec_version") == config["project_spec_version"], "Spec version mismatch")
    dates = tuple(freeze["frozen_dates"])
    shared = load_inputs(path / "inputs", config)
    _require(tuple(shared.dates) == dates and shared.calendar.calendar_id == freeze["calendar_id"]
             and shared.catalogue.catalogue_id == freeze["catalogue_id"], "Shared inputs do not match the freeze")
    grid = tuple(int(a) for a in config["aum_grid_usd"])
    benchmark, baseline = {a: [] for a in grid}, {a: [] for a in grid}
    for r in read_table(path / "benchmark_holdings.parquet"):
        benchmark[r["fixed_aum"]].append(Holding(r["date"], "benchmark", r["commodity"], r["contract"],
            r["decision_cutoff"], r["decision_cutoff"], r["inputs_as_of"],
            benchmark_fractional_contracts=r["fractional_contracts"]))
    for r in read_table(path / "baseline_holdings.parquet"):
        baseline[r["fixed_aum"]].append(Holding(r["date"], "baseline", r["commodity"], r["contract"],
            r["decision_cutoff"], r["formed_at"], r["inputs_as_of"],
            implemented_contracts=r["implemented_contracts"]))
    daily = {(r["fixed_aum"], r["date"]): r for r in read_table(path / "baseline_daily.parquet")}
    _require(all((a, d) in daily for a in grid for d in dates), "Frozen baseline returns are incomplete")
    return IntegrationInputs(shared, tuple(read_table(path / "eligible_curve.parquet")),
                             BaselinePath(tuple(read_table(path / "baseline_contract_path.parquet")),
                                          tuple(read_table(path / "roll_events.parquet"))),
                             dates, {a: tuple(v) for a, v in benchmark.items()},
                             {a: tuple(v) for a, v in baseline.items()}, daily, grid, hashes)


def integrated_roll_events(inputs, selections):
    """Destination chooses each pair at its scheduled event; timing chooses when, causally."""
    # "fixed_-5" (baseline timing) rolls on the scheduled destination date itself; it is
    # used only for single-strand reconciliation checks, never as an integrated choice.
    _require(selections["timing"] in (*CROSSOVERS, "fixed_-5"), "Unsupported timing rule for integration")
    calendar, catalogue, curve = inputs.calendar, inputs.catalogue, inputs.curve
    schedule = build_destination_schedule(inputs.baseline_path, curve, calendar, catalogue,
                                          selections["destination"])
    destination_path = apply_destination_rule_to_baseline(inputs.baseline_path, schedule, curve,
                                                          calendar, catalogue)
    _require(all(p["implemented"] for p in destination_path.positions), "Destination path is incomplete")
    days_lookup = {(r["date"], r["contract"]): r["days_to_anchor"] for r in curve}
    search = CROSSOVERS.get(selections["timing"])
    events = []
    for root in ROOTS:
        held = [p for p in destination_path.positions if p["commodity"] == root]
        current = min(held, key=lambda p: p["decision_date"])["contract"]
        for e in sorted((e for e in schedule.events if e["commodity"] == root), key=lambda e: e["date"]):
            outgoing, incoming, scheduled = e["actual_outgoing_contract"], e["destination"], e["date"]
            _require(outgoing == current, f"Destination chain broken for {root} on {scheduled}")
            # Part 3 convention (as selected): the search starts on the first session the
            # outgoing contract is held in the path timing is applied to (here, the destination path).
            start = min(p["decision_date"] for p in held if p["contract"] == outgoing)
            pair = RollPair(root, outgoing, incoming, catalogue[outgoing].anchor, scheduled)
            found = (search(inputs.shared.observations, pair, calendar, start, scheduled) if search
                     else {"crossover_date": None, "trigger": None})
            roll = found["crossover_date"] or scheduled
            index = calendar.indices[roll]
            _require(is_eligible(catalogue[outgoing], roll)
                     and is_eligible(catalogue[incoming], calendar.dates[index + 1]),
                     f"Timed roll {outgoing}->{incoming} on {roll} breaks eligibility")
            trigger = found["trigger"] or {}
            events.append({
                "commodity": root, "scheduled_destination_date": scheduled,
                "baseline_outgoing_contract": e["outgoing_contract"],
                "baseline_destination": e["baseline_destination"],
                "outgoing_contract": outgoing, "incoming_contract": incoming, "search_start": start,
                "roll_decision_date": roll, "first_incoming_interval_end": calendar.dates[index + 1],
                "sessions_before_scheduled_date": calendar.indices[scheduled] - index,
                "outgoing_days_to_fnd_at_roll": days_lookup[(roll, outgoing)],
                "incoming_days_to_fnd_at_roll": days_lookup[(roll, incoming)],
                "fallback_to_scheduled_date": found["crossover_date"] is None,
                "triggered_on_first_window_session": found["crossover_date"] == start,
                "trigger_source_date": trigger.get("source_date"),
                "trigger_outgoing_value": trigger.get("outgoing_value"),
                "trigger_incoming_value": trigger.get("incoming_value")})
            current = incoming
    return tuple(events), destination_path


def contract_path(base_path, events):
    """Each position holds the incoming contract once its timed roll decision has passed."""
    rows = []
    for p in base_path.positions:
        rolls = [e for e in events if e["commodity"] == p["commodity"]]
        done = [e for e in rolls if e["roll_decision_date"] <= p["decision_date"]]
        contract = done[-1]["incoming_contract"] if done else (rolls[0]["outgoing_contract"] if rolls else p["contract"])
        rows.append({**p, "contract": contract})
    return tuple(rows)


def missing_frozen_inputs(inputs, path_rows, aum, capacity_method, weights):
    """Contracts the capacity rule would need that lie outside the frozen statistics universe.

    Diagnostic only (stop condition): it mirrors when the engine consults overflow
    candidates so an absent frozen input is reported instead of substituted.
    """
    store, known, missing = inputs.shared.observations, set(inputs.shared.observations.identities), set()
    method = CapacityMethod(capacity_method)
    if method == CapacityMethod.HARD_TRUNCATE:
        return ()
    for r in path_rows:
        ctx = inputs.calendar.decision(r["date"])
        value = decision_time_dollar_contract_value(store, inputs.catalogue, r["contract"], ctx).value
        cap = integer_capacity_limit(compute_adv20(store, r["contract"], ctx).value)
        if Decimal(aum) * weights[r["commodity"]] / value > cap:
            curve = [c for c in inputs.curve if c["date"] == ctx.decision_date and c["commodity"] == r["commodity"]]
            wanted = deferred_overflow_candidates(r["contract"], curve, inputs.catalogue, ctx.decision_date)
            missing.update(c for c in wanted[:1 if method == CapacityMethod.OVERFLOW_NEXT else 2] if c not in known)
    return tuple(sorted(missing))


def size_path(inputs, path_rows, aum, capacity_method, rounding_method, weights, strategy):
    """Shared engines only: capacity on the held primary, then rounding within ceilings."""
    targets = [{"date": r["date"], "strategy": strategy, "commodity": r["commodity"],
                "primary_contract": r["contract"], "target_weight": weights[r["commodity"]],
                "target_notional": Decimal(aum) * weights[r["commodity"]]} for r in path_rows]
    portfolio = build_capacity_portfolio(targets, inputs.curve, inputs.shared.observations,
                                         inputs.catalogue, CapacityMethod(capacity_method))
    by_date = {}
    for row in portfolio.rows:
        by_date.setdefault(row.date, []).append(row)
    holdings = []
    for day, rows in sorted(by_date.items()):
        records = capacity_rounding_targets(CapacityPortfolio(portfolio.method, tuple(rows), ()))
        holdings.extend(build_rounded_portfolio(
            [RoundingTarget(**r) for r in records], rounding_method, date=day,
            formed_at=max(r.formed_at for r in rows), inputs_as_of=max(r.inputs_as_of for r in rows),
            calendar=inputs.calendar, catalogue=inputs.catalogue, strategy=strategy).rows)
    return tuple(holdings), portfolio.diagnostics


def _mean_days(path_rows, curve):
    lookup = {(r["date"], r["contract"]): r["days_to_anchor"] for r in curve}
    values = [lookup[(p["decision_date"], p["contract"])] for p in path_rows]
    return Decimal(sum(values)) / Decimal(len(values))


def run_section5(handoff_dir, outputs_dir, config):
    """Baseline vs integrated implementation on the frozen benchmark, mask and AUM grid."""
    selections = read_strand_selections(outputs_dir)
    inputs = load_integration_inputs(handoff_dir, config)
    weights = {k: decimal_value(str(v)) for k, v in config["target_weights"].items()}
    events, destination_path = integrated_roll_events(inputs, selections)
    path = contract_path(inputs.baseline_path, events)
    summary, daily_rows, holdings_rows, unavailable = [], [], [], []
    for aum in inputs.aum_grid:
        # Correctness check: the same pipeline on the baseline path reproduces Section 0 exactly.
        check, _ = size_path(inputs, inputs.baseline_path.positions, aum, BASELINE_SIZING["capacity"],
                             BASELINE_SIZING["rounding"], weights, "baseline")
        _require({(h.date, h.contract): h.quantity for h in check}
                 == {(h.date, h.contract): h.quantity for h in inputs.baseline[aum]},
                 f"Sizing pipeline does not reproduce the frozen baseline at {aum}")
        missing = missing_frozen_inputs(inputs, path, aum, selections["capacity"], weights)
        if missing:
            unavailable.append({"fixed_aum": aum, "reason": "overflow candidate(s) outside the frozen Section 0 "
                                "statistics universe: " + ", ".join(missing)})
            continue
        integrated, diagnostics = size_path(inputs, path, aum, selections["capacity"],
                                            selections["rounding"], weights, "integrated")
        book = HoldingsBook([*inputs.benchmark[aum], *inputs.baseline[aum], *integrated],
                            inputs.calendar, inputs.catalogue)
        validity = {(d, s): inputs.baseline_daily[(aum, d)]["common_valid_date"] is True
                    for d in inputs.dates for s in ("benchmark", "baseline", "integrated")}
        daily = portfolio_pnl(book, inputs.shared.observations, inputs.dates,
                              ("benchmark", "baseline", "integrated"), Decimal(aum),
                              inputs.shared.provenance["snapshot_end_exclusive"], validity)
        _require(all(r.common_valid for r in daily), f"Common mask not fully valid at {aum}")
        for r in daily:
            frozen = inputs.baseline_daily[(aum, r.date)]
            _require(r.returns["benchmark"] == decimal_value(frozen["benchmark_daily_return"])
                     and r.returns["baseline"] == decimal_value(frozen["baseline_daily_return"]),
                     f"Benchmark/baseline returns differ from the freeze at {aum} on {r.date}")
            daily_rows.append({"date": r.date, "fixed_aum": aum,
                               "benchmark_daily_return": r.returns["benchmark"],
                               "baseline_daily_return": r.returns["baseline"],
                               "integrated_daily_return": r.returns["integrated"],
                               "baseline_tracking_difference": r.returns["baseline"] - r.returns["benchmark"],
                               "integrated_tracking_difference": r.returns["integrated"] - r.returns["benchmark"]})
        rmse = tracking_rmse_bps(daily)
        summary.append({"fixed_aum": aum, "baseline_rmse_bps": rmse["baseline"],
                        "integrated_rmse_bps": rmse["integrated"],
                        "change_bps": rmse["integrated"] - rmse["baseline"],
                        "change_pct": (rmse["integrated"] - rmse["baseline"]) / rmse["baseline"] * 100,
                        "common_valid_dates": len(daily), "candidate_dates": len(inputs.dates),
                        "capacity_binding_targets": sum("primary_capacity_binding" in d["flags"] for d in diagnostics),
                        "unimplemented_notional": sum((d["unimplemented_notional"] for d in diagnostics), Decimal(0)),
                        "integrated_contracts_held": len({h.contract for h in integrated if h.quantity})})
        holdings_rows.extend({"date": h.date, "fixed_aum": aum, "commodity": h.commodity, "contract": h.contract,
                              "implemented_contracts": h.quantity} for h in integrated)
    # Reconciliation: each strand's winner alone, through this same pipeline, must match its
    # committed Part 3 result. Descriptive interaction row: destination + timing, baseline sizing.
    baseline_rules = {"destination": "next_deferred", "timing": "fixed_-5", **BASELINE_SIZING}
    references = {"destination": 10000000, "timing": 10000000, "capacity": 1000000000, "rounding": 1000000}
    reconciliation = [{"strand": strand, "rule": selections[strand], "reference_aum": references[strand],
                       "reproduced_rmse_bps": strategy_rmse(inputs, {**baseline_rules, strand: selections[strand]},
                                                            references[strand], weights)}
                      for strand in STRANDS]
    destination_timing = {s["fixed_aum"]: strategy_rmse(
        inputs, {**baseline_rules, "destination": selections["destination"], "timing": selections["timing"]},
        s["fixed_aum"], weights) for s in summary}
    return {"selections": selections, "events": events, "path": path, "summary": summary,
            "reconciliation": reconciliation, "destination_timing_baseline_sizing": destination_timing,
            "daily": daily_rows, "holdings": holdings_rows, "unavailable": unavailable, "dates": inputs.dates,
            "mean_days_to_fnd": {"baseline": _mean_days(inputs.baseline_path.positions, inputs.curve),
                                 "destination_only": _mean_days(destination_path.positions, inputs.curve),
                                 "integrated": _mean_days(path, inputs.curve)},
            "input_hashes": inputs.output_hashes, "inputs": inputs, "handoff_dir": str(handoff_dir)}


def strategy_rmse(inputs, rules, aum, weights):
    """Tracking RMSE (daily bp) of one rule combination on the frozen mask; used for reconciliation."""
    events, _ = integrated_roll_events(inputs, rules)
    holdings, _ = size_path(inputs, contract_path(inputs.baseline_path, events), aum,
                            rules["capacity"], rules["rounding"], weights, "check")
    book = HoldingsBook([*inputs.benchmark[aum], *holdings], inputs.calendar, inputs.catalogue)
    daily = portfolio_pnl(book, inputs.shared.observations, inputs.dates, ("benchmark", "check"), Decimal(aum),
                          inputs.shared.provenance["snapshot_end_exclusive"])
    _require(all(r.common_valid for r in daily), "Reconciliation run left the common mask")
    return tracking_rmse_bps(daily)["check"]
