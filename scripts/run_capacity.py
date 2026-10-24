"""Explicit fixture-only demonstration of the Part 2 capacity alternatives."""
import argparse
import json
from pathlib import Path
import sys
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.capacity.rules import (
    CapacityMethod,
    allocate_overflow_adv_weighted,
    allocate_overflow_sequential,
    build_capacity_portfolio,
)
from src.data.fixtures import load_fixture
from src.validation.schemas import load_project_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="Run only committed synthetic cases")
    args = parser.parse_args()
    if not args.fixtures:
        parser.error("Historical capacity analysis is Part 3; use --fixtures for the offline demonstration")

    config = load_project_config(ROOT / "config/project.yaml")
    market = load_fixture(ROOT / "data/fixtures/phase_a.json",
                          config["max_liquidity_source_age_sessions"])
    fixture_cases = json.loads((ROOT / "data/fixtures/capacity_phase_a.json").read_text())
    case = fixture_cases["portfolio_case"]
    curve = [row for row in market.raw["baseline_path_case"]["eligible_curve"]
             if row["date"] == case["curve_date"] and row["commodity"] == case["commodity"]]
    target = {key: value for key, value in case.items() if key not in {"curve_date", "note"}}

    results = {}
    for method in CapacityMethod:
        portfolio = build_capacity_portfolio(
            [target], curve, market.observations, market.catalogue, method,
            participation_rate=config["capacity_participation_rate"],
            liquidation_horizon_days=config["capacity_liquidation_horizon_days"],
        )
        results[method.value] = {
            "diagnostic": portfolio.diagnostics[0],
            "holdings": [row.to_record() for row in portfolio.rows],
        }
    allocator_checks = {}
    for allocation_case in fixture_cases["allocation_cases"]:
        name = allocation_case["name"]
        values = allocation_case["dollar_contract_values"]
        if name.startswith("adv_weighted"):
            allocation = allocate_overflow_adv_weighted(
                allocation_case["residual_notional"], allocation_case["candidates"],
                allocation_case["capacity_limits"], values, allocation_case["adv20_by_contract"],
            )
        else:
            allocation = allocate_overflow_sequential(
                allocation_case["residual_notional"], allocation_case["candidates"],
                allocation_case["capacity_limits"], values,
            )
        expected = {key: Decimal(value) for key, value in allocation_case["expected_allocations"].items()}
        expected_residual = Decimal(allocation_case["expected_unimplemented_notional"])
        if (dict(allocation.allocations) != expected
                or allocation.unimplemented_notional != expected_residual):
            raise RuntimeError(f"Fixture expectation failed: {name}")
        allocator_checks[name] = {"allocations": dict(allocation.allocations),
                                  "unimplemented_notional": allocation.unimplemented_notional,
                                  "flags": allocation.flags}
    print(json.dumps({"mode": "synthetic_fixture_only", "real_data_gate_run": False,
                      "method_selected": False, "allocator_hand_checks": allocator_checks,
                      "portfolio_methods": results}, default=str, indent=2))


if __name__ == "__main__":
    main()
