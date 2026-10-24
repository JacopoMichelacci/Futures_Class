"""Offline Section 0 fixture demonstration or frozen Phase B construction."""
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
    parser.add_argument("--production-dir", type=Path, help="Accepted local production directory; never downloads")
    parser.add_argument("--inventory-dir", type=Path, help="Frozen accepted inventory evidence for initial construction")
    parser.add_argument("--reproduce", action="store_true", help="Rebuild Phase B from committed consumed inputs")
    parser.add_argument("--handoff-dir", type=Path, default=ROOT / "data/section0")
    parser.add_argument("--output-dir", type=Path, help="New empty output directory")
    args = parser.parse_args()
    if sum((args.fixtures, bool(args.production_dir), args.reproduce)) != 1:
        parser.error("Choose exactly one of --fixtures, --production-dir or --reproduce")
    config = load_project_config(ROOT / "config/project.yaml")
    if not args.fixtures:
        from src.section0.phase_b import export, load_inputs, load_production
        if args.output_dir is None:
            parser.error("Phase B requires a new --output-dir")
        if args.production_dir and args.inventory_dir is None:
            parser.error("Initial production construction requires --inventory-dir")
        inputs = (load_production(args.production_dir, args.inventory_dir, config) if args.production_dir
                  else load_inputs(args.handoff_dir / "inputs", config))
        result = export(inputs, config, args.output_dir, freeze_inputs=bool(args.production_dir))
        print(json.dumps({"mode": "phase_b", "coverage": result["coverage"],
                          "references": result["preflight"]["references"],
                          "method_comparisons_run": False}, default=str, indent=2))
        return
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
