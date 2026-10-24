"""Frozen Section 0 handoff -> timing experiment (Issue #7). Verifies inputs; never rebuilds.

Everything except the timing rule comes from ``data/section0``: calendar, catalogue
and publication-aware observations (``load_inputs``), the baseline pairs and path,
the $10m timing reference, its common mask, the benchmark, and the shared sizing
layer ``holdings_from_contract_path`` (target notional -> capacity -> nearest).
"""
import json
from decimal import Decimal
from pathlib import Path

from src.portfolio.baseline import BaselinePath
from src.portfolio.holdings import Holding
from src.rolling.analysis import BASELINE_RULE, TimingExperiment
from src.section0.phase_b import holdings_from_contract_path, load_inputs, read_table, sha
from src.validation.schemas import ContractError, decimal_value

STRAND = "timing"
DATA_KIND = "real_frozen_databento_section0"


def load_timing_experiment(handoff_dir, config):
    path = Path(handoff_dir)
    for name, digest in json.loads((path / "output_hashes.json").read_text()).items():
        if sha(path / name) != digest:
            raise ContractError(f"Frozen Section 0 output hash mismatch: {name}")
    freeze = json.loads((path / "freeze.json").read_text())
    if freeze.get("project_spec_version") != config["project_spec_version"]:
        raise ContractError("Handoff and configuration specification versions differ")
    aum = freeze["preflight"]["references"][STRAND]
    if aum not in (config["selection_roll_reference_aum_usd"], config["selection_roll_fallback_reference_aum_usd"]):
        raise ContractError("Timing reference AUM is not a declared reference or fallback")
    dates = tuple(freeze["frozen_dates"])
    if not dates or list(dates) != sorted(set(dates)):
        raise ContractError("Frozen dates must be unique and ordered")
    shared = load_inputs(path / "inputs", config)
    if (tuple(shared.dates) != dates or shared.calendar.calendar_id != freeze["calendar_id"]
            or shared.catalogue.catalogue_id != freeze["catalogue_id"]):
        raise ContractError("Shared inputs do not match the frozen calendar/catalogue/dates")
    mask = [r for r in read_table(path / "common_masks.parquet") if r["strand"] == STRAND]
    if [r["date"] for r in mask] != list(dates) or any(r["fixed_aum"] != aum for r in mask):
        raise ContractError("Timing common mask does not match the frozen dates and reference AUM")
    decision_inputs = read_table(path / "decision_inputs.parquet")
    catalogue = shared.catalogue.contracts
    baseline = BaselinePath(tuple(read_table(path / "baseline_contract_path.parquet")),
                            tuple(read_table(path / "roll_events.parquet")))
    benchmark = tuple(Holding(r["date"], "benchmark", r["commodity"], r["contract"], r["decision_cutoff"],
                              r["decision_cutoff"], r["inputs_as_of"],
                              benchmark_fractional_contracts=r["fractional_contracts"])
                      for r in read_table(path / "benchmark_holdings.parquet") if r["fixed_aum"] == aum)

    def size_path(rows, strategy):
        return holdings_from_contract_path(rows, decision_inputs, catalogue, shared.calendar,
                                           config, aum, strategy)[0]

    return TimingExperiment(shared.calendar, catalogue, shared.observations, baseline, benchmark, size_path,
                            Decimal(aum), shared.provenance["snapshot_end_exclusive"], DATA_KIND, dates,
                            {r["date"]: r["common_valid_date"] for r in mask})


def verify_baseline_reproduction(result, handoff_dir):
    """The fixed_-5 rule must reproduce Section 0's frozen baseline exactly."""
    path, aum = Path(handoff_dir), result["daily"][0].fixed_aum
    frozen = {(r["date"], r["contract"]): decimal_value(r["implemented_contracts"])
              for r in read_table(path / "baseline_holdings.parquet") if r["fixed_aum"] == aum}
    ours = {(h.date, h.contract): h.implemented_contracts for h in result["holdings"][BASELINE_RULE]}
    if ours != frozen:
        raise ContractError("fixed_-5 holdings differ from the frozen Section 0 baseline")
    daily = {r["date"]: r for r in read_table(path / "baseline_daily.parquet") if r["fixed_aum"] == aum}
    for row in result["daily"]:
        frozen_row = daily.get(row.date)
        if (frozen_row is None or row.returns is None
                or row.returns["benchmark"] != decimal_value(frozen_row["benchmark_daily_return"])
                or row.returns[f"timing:{BASELINE_RULE}"] != decimal_value(frozen_row["baseline_daily_return"])):
            raise ContractError(f"Daily returns differ from the frozen Section 0 baseline on {row.date}")
    return True
