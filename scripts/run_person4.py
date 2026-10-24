"""Offline Part 2 rounding demonstration; no historical analysis or selection."""
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
    if not parser.parse_args().fixtures:
        parser.error("Historical analysis is deferred; use --fixtures for the offline Part 2 demo")
    config = load_project_config(ROOT / "config/project.yaml")
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
