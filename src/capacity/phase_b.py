"""Frozen Section 0 Phase B adapter for Jacopo's Part 3 capacity analysis.

This module only loads the committed empirical handoff and feeds its unchanged
primary targets into the capacity engine. It does not reconstruct acquisition,
benchmark, baseline sizing, calendars or masks.
"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

from src.capacity.analysis import CapacityExperiment, compare_capacity_across_aum
from src.portfolio.holdings import Holding
from src.section0.phase_b import load_inputs, read_table
from src.validation.schemas import ContractError, ROOTS, decimal_value


def _aum(value):
    return decimal_value(value, "fixed_aum")


def _require_exact_dates(rows, dates, name):
    seen = {row["date"] for row in rows}
    if seen != set(dates):
        raise ContractError(f"{name} dates do not match the frozen Section 0 dates")


def load_capacity_experiment(handoff_dir, config) -> CapacityExperiment:
    """Load and verify the exact inputs authorised for the capacity strand."""
    handoff = Path(handoff_dir)
    freeze = json.loads((handoff / "freeze.json").read_text())
    if freeze.get("project_spec_version") != "8.0" or not freeze.get("coverage", {}).get("passed"):
        raise ContractError("A passed frozen Version 8 Section 0 handoff is required")
    if _aum(freeze.get("preflight", {}).get("references", {}).get("capacity")) != Decimal("1000000000"):
        raise ContractError("Capacity Part 3 requires the frozen $1bn reference AUM")
    grid = tuple(_aum(value) for value in freeze["preflight"].get("core_aum_grid", ()))
    if grid != tuple(_aum(value) for value in config["aum_grid_usd"]):
        raise ContractError("Frozen Section 0 core AUM grid differs from locked configuration")
    dates = tuple(freeze.get("frozen_dates", ()))
    if len(dates) != 148 or tuple(sorted(set(dates))) != dates:
        raise ContractError("Frozen capacity handoff must contain its 148 explicit ordered dates")

    shared = load_inputs(handoff / "inputs", config)
    if tuple(shared.dates) != dates:
        raise ContractError("Consumed observations and frozen date manifest disagree")
    curve = tuple(read_table(handoff / "eligible_curve.parquet"))
    targets = tuple(read_table(handoff / "post_capacity_targets.parquet"))
    benchmark = tuple(read_table(handoff / "benchmark_holdings.parquet"))
    masks = tuple(read_table(handoff / "common_masks.parquet"))
    capacity_mask = [row for row in masks if row["strand"] == "capacity"]
    if (len(capacity_mask) != len(dates) or any(_aum(row["fixed_aum"]) != Decimal("1000000000")
                                                or not row["common_valid_date"] for row in capacity_mask)):
        raise ContractError("Capacity strand must use the fully valid frozen $1bn common mask")
    _require_exact_dates(capacity_mask, dates, "Capacity mask")

    by_target_aum, by_benchmark_aum = {}, {}
    required_target_keys = {(date, root) for date in dates for root in ROOTS}
    for aum in grid:
        target_rows = tuple(row for row in targets if _aum(row["fixed_aum"]) == aum)
        benchmark_rows = tuple(row for row in benchmark if _aum(row["fixed_aum"]) == aum)
        if {(row["date"], row["commodity"]) for row in target_rows} != required_target_keys:
            raise ContractError(f"Frozen capacity targets are incomplete at AUM {aum}")
        _require_exact_dates(benchmark_rows, dates, f"Benchmark AUM {aum}")
        by_target_aum[aum] = target_rows
        by_benchmark_aum[aum] = benchmark_rows

    def targets_for_aum(aum):
        selected = by_target_aum.get(_aum(aum))
        if selected is None:
            raise ContractError("Requested AUM is outside the frozen capacity grid")
        return tuple({
            "date": row["date"], "strategy": "capacity", "commodity": row["commodity"],
            "primary_contract": row["contract"], "target_weight": row["target_weight"],
            "target_notional": row["target_notional"],
            "pre_capacity_contracts": row["pre_capacity_contracts"],
        } for row in selected)

    def benchmark_for_aum(aum):
        selected = by_benchmark_aum.get(_aum(aum))
        if selected is None:
            raise ContractError("Requested benchmark AUM is outside the frozen capacity grid")
        return tuple(Holding(
            row["date"], "benchmark", row["commodity"], row["contract"], row["decision_cutoff"],
            row["decision_cutoff"], row["inputs_as_of"],
            benchmark_fractional_contracts=row["fractional_contracts"],
            details={"target_weight": row["target_weight"], "target_notional": row["target_notional"],
                     "flags": ["FROZEN_SECTION0_BENCHMARK"]},
        ) for row in selected)

    return CapacityExperiment(
        shared.calendar, shared.catalogue.contracts, shared.observations, curve,
        targets_for_aum, benchmark_for_aum, shared.provenance["snapshot_end_exclusive"],
        "frozen_section0_phase_b", frozen_dates=dates,
    )


def run_real_capacity_analysis(handoff_dir, config):
    """Run all locked methods over the frozen core grid; no synthetic fallback."""
    experiment = load_capacity_experiment(handoff_dir, config)
    return compare_capacity_across_aum(
        experiment, config["aum_grid_usd"], Decimal("1000000000"),
        config["capacity_fallback_reference_aum_usd"], config["tracking_min_common_dates"],
        config["tracking_min_candidate_survival"], config["tracking_rmse_tie_tolerance_bps_daily"],
    )
