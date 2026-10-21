"""Issue #14: real Section 0 contract-destination experiment.

Run from a checkout containing both src.selection and src.section0.phase_b.
Never substitutes synthetic prices, positions, or performance data.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from src.selection.analysis import DestinationInputs, StrategyResult, RULES, run_experiment
from src.selection.exports import export_results
from src.section0.phase_b import load_inputs, read_table, holdings_from_contract_path
from src.portfolio.baseline import BaselinePath
from src.portfolio.holdings import Holding, HoldingsBook
from src.metrics.portfolio_pnl import portfolio_pnl
from src.validation.schemas import ContractError, load_project_config


def _reference_aum(handoff: Path) -> Decimal:
    """Read the locked selection reference, rejecting an unexpected handoff."""
    freeze = json.loads((handoff / "freeze.json").read_text())
    # The handoff's destination mask and baseline rows are authoritative for AUM.
    mask = read_table(handoff / "common_masks.parquet")
    amounts = {Decimal(str(r["fixed_aum"])) for r in mask if r["strand"] == "destination"}
    if amounts != {Decimal("10000000")}:
        raise ContractError(f"Unexpected destination AUM(s): {sorted(amounts)}")
    if not isinstance(freeze, dict):
        raise ContractError("Invalid frozen manifest")
    return Decimal("10000000")


def _benchmark_rows(handoff: Path, aum: Decimal, dates: tuple[str, ...]):
    selected = [r for r in read_table(handoff / "benchmark_holdings.parquet")
                if Decimal(str(r["fixed_aum"])) == aum]
    if not selected:
        raise ContractError("No benchmark holdings at destination reference AUM")
    if {r["date"] for r in selected} != set(dates):
        raise ContractError("Benchmark holdings dates differ from frozen candidate dates")
    return [Holding(
        date=r["date"], strategy="benchmark", commodity=r["commodity"],
        contract=r["contract"], decision_cutoff=r["decision_cutoff"],
        formed_at=r["decision_cutoff"], inputs_as_of=r["inputs_as_of"],
        benchmark_fractional_contracts=Decimal(str(r["fractional_contracts"])),
    ) for r in selected]


def run_real(handoff: Path, outdir: Path, config_path: Path):
    config = load_project_config(str(config_path))
    aum = _reference_aum(handoff)
    shared = load_inputs(handoff / "inputs", config)  # verifies frozen input hashes
    calendar = shared.calendar
    catalogue = shared.catalogue.contracts
    observations = shared.observations
    dates = tuple(shared.dates)
    curve = read_table(handoff / "eligible_curve.parquet")
    baseline = BaselinePath(
        tuple(read_table(handoff / "baseline_contract_path.parquet")),
        tuple(read_table(handoff / "roll_events.parquet")),
    )
    decision_inputs = read_table(handoff / "decision_inputs.parquet")
    benchmark_holdings = _benchmark_rows(handoff, aum, dates)

    # Frozen benchmark returns and destination-specific validity are not inferred
    # from strategy outcomes. We never search for a more favorable date mask.
    benchmark_rows = [r for r in read_table(handoff / "baseline_daily.parquet")
                      if Decimal(str(r["fixed_aum"])) == aum]
    benchmark = {r["date"]: float(Decimal(str(r["benchmark_daily_return"])))
                 if r["common_valid_date"] and r["benchmark_daily_return"] is not None else None
                 for r in benchmark_rows}
    mask_rows = [r for r in read_table(handoff / "common_masks.parquet")
                 if r["strand"] == "destination" and Decimal(str(r["fixed_aum"])) == aum]
    mask = {r["date"]: r["common_valid_date"] is True for r in mask_rows}
    if set(benchmark) != set(dates) or set(mask) != set(dates):
        raise ContractError("Frozen benchmark/mask dates do not match frozen inputs")
    if not all(mask.values()):
        raise ContractError("Frozen destination mask contains invalid dates; reconcile upstream")

    # Each candidate path is valued with the SAME Section 0 exact-contract P&L.
    # Incomplete paths raise, rather than becoming zero returns or shortened samples.
    def evaluate_path(rule, path, reference_aum):
        if Decimal(str(reference_aum)) != aum:
            raise ContractError("Reference AUM differs from frozen destination AUM")
        incomplete = [r for r in path.positions if r.get("implemented") is not True or not r.get("contract")]
        if incomplete:
            sample = [(r["date"], r["commodity"], r.get("flags")) for r in incomplete[:8]]
            raise ContractError(f"{rule}: {len(incomplete)} unimplemented path rows; examples: {sample}")
        sized, _ = holdings_from_contract_path(
            path.positions, decision_inputs, catalogue, calendar, config, aum, strategy=rule
        )
        book = HoldingsBook([*benchmark_holdings, *sized], calendar, catalogue)
        validity = {(day, strategy): mask[day]
                    for day in dates for strategy in ("benchmark", rule)}
        daily = portfolio_pnl(
            book, observations, dates, ("benchmark", rule), aum,
            shared.provenance["snapshot_end_exclusive"], validity,
        )
        failures = [(r.date, r.invalid_reasons) for r in daily if not r.common_valid]
        if failures:
            raise ContractError(f"{rule}: shared accounting invalid on {len(failures)} dates: {failures[:5]}")
        # Verify benchmark matches the frozen Section 0 baseline computation.
        for row in daily:
            frozen = benchmark[row.date]
            if frozen is None or abs(row.returns["benchmark"] - Decimal(str(frozen))) > Decimal("1e-12"):
                raise ContractError(f"{rule}: benchmark differs from frozen return on {row.date}")
        maturities = tuple(float(r.details["days_to_anchor"]) for r in sized)
        return StrategyResult(
            daily_returns={r.date: float(r.returns[rule]) for r in daily},
            maturity_sessions=maturities,
            roll_dates=tuple(e["date"] for e in path.roll_events),
        )

    inputs = DestinationInputs(
        baseline_path=baseline, eligible_curve=curve, calendar=calendar,
        catalogue=catalogue, benchmark_daily_returns=benchmark,
        candidate_dates=dates, reference_aum_usd=float(aum),
    )
    # No invented tie priority. If a <0.1 bp tie occurs, report it unresolved
    # until the exact frozen Part V order is supplied.
    result = run_experiment(
        inputs, evaluate_path,
        min_dates=config["tracking_min_common_dates"],
        min_survival=float(config["tracking_min_candidate_survival"]),
        tie_bps=float(config["tracking_rmse_tie_tolerance_bps_daily"]),
    )
    if not result["coverage_passed"]:
        raise ContractError("Destination coverage gate failed; no canonical empirical export")
    export_results(result, outdir, provenance="real")
    print(f"Saved empirical outputs to: {outdir.resolve()}")
    for row in result["summary"]:
        print(f"  {row['rule']:<18} RMSE={row['tracking_rmse_bps']:.5f} bp "
              f"common_dates={row['common_valid_dates']}")
    print(f"Preferred rule: {result['preferred_rule'] or 'unresolved (confirm Part V tie priority)'}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="Run frozen empirical comparison")
    parser.add_argument("--fixtures", action="store_true", help="Not supported by this real-data runner")
    parser.add_argument("--handoff", type=Path, default=Path("data/section0"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/person1"))
    parser.add_argument("--config", type=Path, default=Path("config/project.yaml"))
    args = parser.parse_args()
    if not args.real or args.fixtures:
        parser.error("Specify --real (fixture tests remain in tests/unit)")
    run_real(args.handoff, args.output_dir, args.config)


if __name__ == "__main__":
    main()
