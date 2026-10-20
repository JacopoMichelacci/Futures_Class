"""Part 3 capacity/AUM comparison (Issue #15).

Only ``--fixtures`` is available until the frozen real Section 0 handoff
exists. This command refuses to substitute synthetic results for research.
"""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.capacity.analysis import compare_capacity_across_aum, diagnostic_records, summary_records
from src.capacity.fixture_experiment import build_synthetic_experiment
from src.validation.schemas import decimal_value, load_project_config


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="run the synthetic harness only")
    parser.add_argument("--out", type=Path, help="directory for capacity_aum_summary.csv and capacity_diagnostics.csv")
    args = parser.parse_args()
    if not args.fixtures:
        parser.error("Real-data capacity analysis awaits the frozen Section 0 handoff; use --fixtures")
    config = load_project_config(ROOT / "config/project.yaml")
    result = compare_capacity_across_aum(
        build_synthetic_experiment(), config["aum_grid_usd"], config["capacity_reference_aum_usd"],
        config["capacity_fallback_reference_aum_usd"], config["tracking_min_common_dates"],
        decimal_value(config["tracking_min_candidate_survival"]),
        decimal_value(config["tracking_rmse_tie_tolerance_bps_daily"]),
    )
    summary, diagnostics = summary_records(result), diagnostic_records(result)
    if args.out:
        write_csv(args.out / "capacity_aum_summary.csv", summary)
        write_csv(args.out / "capacity_diagnostics.csv", diagnostics)
    print(json.dumps({"mode": "synthetic_fixture_only", "findings": False,
                      "preflight": result["preflight"], "summary": summary}, default=str, indent=2))


if __name__ == "__main__":
    main()
