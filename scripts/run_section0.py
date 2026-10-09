"""Explicit fixture-only Phase A demonstration. Never downloads market data."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.fixtures import load_fixture
from src.metrics.portfolio_pnl import portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.baseline import build_baseline_path
from src.validation.schemas import load_project_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="Run only the committed synthetic example")
    args = parser.parse_args()
    if not args.fixtures:
        parser.error("Real-data Section 0 is Phase B; supply --fixtures for the offline Phase A demonstration")
    config = load_project_config(ROOT / "config/project.yaml")
    f = load_fixture(ROOT / "data/fixtures/phase_a.json", config["max_liquidity_source_age_sessions"])
    case = f.raw["baseline_path_case"]
    baseline = build_baseline_path(case["eligible_curve"], case["decision_dates"], case["commodities"],
                                   f.calendar, f.catalogue)
    daily = portfolio_pnl(f.holdings, f.observations, f.raw["return_dates"], f.raw["strategies"],
                          f.raw["fixed_aum"], f.raw["snapshot_end_exclusive"])
    print(json.dumps({"mode": "synthetic_fixture_only", "real_data_gate_run": False,
                      "candidate_dates": len(daily), "common_valid_dates": sum(r.common_valid for r in daily),
                      "fixed_aum": daily[0].fixed_aum,
                      "baseline_contract_path": baseline.positions,
                      "baseline_roll_events": baseline.roll_events,
                      "daily_pnl": [{"date": r.date, "pnl": r.pnl} for r in daily],
                      "tracking_rmse_bps": tracking_rmse_bps(daily)}, default=str, indent=2))


if __name__ == "__main__":
    main()
