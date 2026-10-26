"""Real-data contract-destination experiment (Issue #14, PR #28).

Requires a checkout containing both this branch and Section 0 Phase B code,
plus the frozen data/section0 handoff. No synthetic production substitutes.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from src.selection.analysis import DestinationInputs, StrategyResult, run_experiment
from src.selection.exports import export_results
from src.portfolio.baseline import BaselinePath
from src.portfolio.holdings import Holding, HoldingsBook
from src.metrics.portfolio_pnl import portfolio_pnl
from src.validation.schemas import ContractError, load_project_config


def run_real(handoff: Path, outdir: Path, config_path: Path):
    # Import here so the selection branch's unit tests remain runnable before
    # the separate Section 0 Phase B PR is merged.
    try:
        from src.section0.phase_b import load_inputs, read_table, holdings_from_contract_path
    except ImportError as exc:
        raise RuntimeError("Section 0 Phase B is required; use a combined checkout or merge PR #29 first") from exc

    config = load_project_config(str(config_path))
    if not (handoff / "freeze.json").is_file():
        raise ContractError(f"Missing frozen Section 0 handoff: {handoff}")
    freeze = json.loads((handoff / "freeze.json").read_text())
    if not isinstance(freeze, dict):
        raise ContractError("Invalid freeze manifest")
    aum = Decimal("10000000")
    masks = [r for r in read_table(handoff / "common_masks.parquet")
             if r["strand"] == "destination"]
    if not masks or {Decimal(str(r["fixed_aum"])) for r in masks} != {aum}:
        raise ContractError("Frozen destination reference AUM must be exactly $10m")

    shared = load_inputs(handoff / "inputs", config)
    calendar, catalogue, observations = shared.calendar, shared.catalogue.contracts, shared.observations
    dates = tuple(shared.dates)
    if len(dates) != len(set(dates)) or tuple(sorted(dates)) != dates:
        raise ContractError("Frozen dates must be sorted and unique")
    mask = {r["date"]: r["common_valid_date"] is True for r in masks}
    if len(mask) != len(masks) or set(mask) != set(dates):
        raise ContractError("Frozen destination mask does not match the candidate dates")
    # Fail closed until the frozen common mask is reconciled; no fake flat positions.
    if not all(mask.values()):
        raise ContractError("Frozen destination mask has invalid dates; reconcile before export")

    curve = read_table(handoff / "eligible_curve.parquet")
    baseline = BaselinePath(
        tuple(read_table(handoff / "baseline_contract_path.parquet")),
        tuple(read_table(handoff / "roll_events.parquet")),
    )
    decision_inputs = read_table(handoff / "decision_inputs.parquet")
    frozen_daily = [r for r in read_table(handoff / "baseline_daily.parquet")
                    if Decimal(str(r["fixed_aum"])) == aum]
    benchmark_exact = {
        r["date"]: Decimal(str(r["benchmark_daily_return"]))
        if r["common_valid_date"] and r["benchmark_daily_return"] is not None else None
        for r in frozen_daily
    }
    if len(benchmark_exact) != len(frozen_daily) or set(benchmark_exact) != set(dates):
        raise ContractError("Frozen benchmark dates are missing or duplicated")
    benchmark = {d: float(v) if v is not None else None for d, v in benchmark_exact.items()}

    raw_benchmark = [r for r in read_table(handoff / "benchmark_holdings.parquet")
                     if Decimal(str(r["fixed_aum"])) == aum]
    if not raw_benchmark or {r["date"] for r in raw_benchmark} != set(dates):
        raise ContractError("Benchmark holdings missing frozen dates at $10m")
    benchmark_holdings = [Holding(
        date=r["date"], strategy="benchmark", commodity=r["commodity"],
        contract=r["contract"], decision_cutoff=r["decision_cutoff"],
        formed_at=r["decision_cutoff"], inputs_as_of=r["inputs_as_of"],
        benchmark_fractional_contracts=Decimal(str(r["fractional_contracts"])),
    ) for r in raw_benchmark]

    def evaluate_path(rule, path, reference_aum):
        if Decimal(str(reference_aum)) != aum:
            raise ContractError("Changed fixed reference AUM")
        incomplete = [p for p in path.positions if p.get("implemented") is not True or not p.get("contract")]
        if incomplete:
            sample = [(p["date"], p["commodity"], p.get("flags")) for p in incomplete[:5]]
            raise ContractError(f"{rule}: {len(incomplete)} unimplemented positions; examples: {sample}")
        sized, _ = holdings_from_contract_path(
            path.positions, decision_inputs, catalogue, calendar, config, aum, strategy=rule
        )
        book = HoldingsBook([*benchmark_holdings, *sized], calendar, catalogue)
        validity = {(d, s): mask[d] for d in dates for s in ("benchmark", rule)}
        daily = portfolio_pnl(book, observations, dates, ("benchmark", rule), aum,
                              shared.provenance["snapshot_end_exclusive"], validity)
        failures = [(r.date, r.invalid_reasons) for r in daily if not r.common_valid]
        if failures:
            raise ContractError(f"{rule}: accounting invalid on {len(failures)} dates: {failures[:5]}")
        for r in daily:
            expected = benchmark_exact[r.date]
            if expected is None or abs(r.returns["benchmark"] - expected) > Decimal("1e-12"):
                raise ContractError(f"{rule}: benchmark does not reproduce frozen return on {r.date}")
        return StrategyResult(
            daily_returns={r.date: float(r.returns[rule]) for r in daily},
            maturity_sessions=tuple(float(h.details["days_to_anchor"]) for h in sized),
            roll_dates=tuple(e["date"] for e in path.roll_events),
        )

    inputs = DestinationInputs(baseline, curve, calendar, catalogue, benchmark, dates, float(aum))
    result = run_experiment(
        inputs, evaluate_path,
        min_dates=config["tracking_min_common_dates"],
        min_survival=float(config["tracking_min_candidate_survival"]),
        tie_bps=float(config["tracking_rmse_tie_tolerance_bps_daily"]),
    )
    if not result["coverage_passed"]:
        raise ContractError("Coverage gate failed; no empirical export")
    export_results(result, outdir, provenance="real")
    print(f"Empirical results saved to {outdir.resolve()}")
    for r in result["summary"]:
        print(f"{r['rule']}: {r['tracking_rmse_bps']:.6f} bp; n={r['common_valid_dates']}")
    print("Preferred:", result["preferred_rule"] or "unresolved (tie priority not frozen)")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--handoff", type=Path, default=Path("data/section0"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/person1"))
    parser.add_argument("--config", type=Path, default=Path("config/project.yaml"))
    args = parser.parse_args()
    if not args.real:
        parser.error("Specify --real; synthetic tests are in tests/unit")
    run_real(args.handoff, args.output_dir, args.config)


if __name__ == "__main__":
    main()
