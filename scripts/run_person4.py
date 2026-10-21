"""Independent real-data rounding analysis or explicitly synthetic Part 2 demo."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.fixtures import load_fixture
from src.metrics.portfolio_pnl import portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.holdings import Holding, HoldingsBook
from src.rounding.rules import (ROUNDING_METHODS, RoundingTarget, build_rounded_portfolio,
                                round_residual_greedy)
from src.validation.schemas import load_project_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true")
    parser.add_argument("--real", action="store_true", help="Analyse only the frozen Section 0 handoff")
    parser.add_argument("--handoff-dir", type=Path, default=ROOT / "data/section0")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/person4")
    parser.add_argument("--chart-html", type=Path, help="Write a self-contained offline Plotly export page")
    parser.add_argument("--chart-png", type=Path, help="Register a PNG exported from the current figure")
    args = parser.parse_args()
    if args.fixtures == args.real:
        parser.error("Choose exactly one of --fixtures or --real")
    config = load_project_config(ROOT / "config/project.yaml")
    if args.real:
        from src.rounding.analysis import load_rounding_experiment, analyse_rounding
        from src.rounding.exports import write_outputs, write_chart_html, register_chart_export
        from src.section0.phase_b import plain
        result = analyse_rounding(load_rounding_experiment(args.handoff_dir, config))
        manifest = write_outputs(result, args.out)
        if args.chart_html:
            args.chart_html.parent.mkdir(parents=True, exist_ok=True)
            write_chart_html(result, args.chart_html)
        if args.chart_png:
            register_chart_export(args.out, args.chart_png.read_bytes())
            manifest = write_outputs(result, args.out)
        if not manifest["chart_export_verified"]:
            print("PNG export pending: use --chart-html and the offline camera button, then --chart-png.", file=sys.stderr)
        print(json.dumps(plain({"mode": result["data_kind"], "by_aum": result["by_aum"], "selection": result["selection"]}), indent=2))
        return
    f = load_fixture(ROOT / "data/fixtures/phase_a.json", config["max_liquidity_source_age_sessions"])
    # All supplied targets are the near maturity, by explicit fixture identity.
    targets = [RoundingTarget(**row, maturity_rank=1) for row in f.raw["post_capacity_targets"]]
    day = f.raw["return_dates"][0]
    context = f.calendar.decision(day)
    provenance = dict(date=day, formed_at="2030-01-31T09:00:00Z", inputs_as_of="2030-01-31T08:00:00Z")
    books = {method: build_rounded_portfolio(targets, method, **provenance,
                                           calendar=f.calendar, catalogue=f.catalogue)
             for method in ROUNDING_METHODS}
    benchmark = [Holding(**provenance, strategy="benchmark", commodity=r.commodity,
                         contract=r.contract, decision_cutoff=context.cutoff_utc,
                         benchmark_fractional_contracts=r.post_capacity_fractional_contracts)
                 for r in targets]
    combined = HoldingsBook(benchmark + [r for b in books.values() for r in b.rows], f.calendar, f.catalogue)
    names = ["benchmark"] + ["rounding_" + method for method in ROUNDING_METHODS]
    daily = portfolio_pnl(combined, f.observations, [day], names, config["rounding_reference_aum_usd"],
                          f.raw["snapshot_end_exclusive"])
    greedy = round_residual_greedy(targets)
    print(json.dumps({"mode": "synthetic_rounding_fixture_only", "empirical_analysis": False,
                      "preferred_method": None,
                      "nearest_tie_convention": "half_up",
                      "reference": "Supplied fractional accounting fixture, not a historical constant-maturity benchmark",
                      "quantities": {m: {r.contract: int(r.quantity) for r in b.rows} for m, b in books.items()},
                      "greedy_steps": [s.__dict__ for s in greedy.steps],
                      "greedy_initial_squared_error": greedy.initial_squared_error,
                      "greedy_final_squared_error": greedy.final_squared_error,
                      "fixed_aum": daily[0].fixed_aum,
                      "common_valid_dates": sum(r.common_valid for r in daily),
                      "synthetic_tracking_rmse_bps": tracking_rmse_bps(daily)}, default=str, indent=2))


if __name__ == "__main__":
    main()
