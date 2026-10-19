"""Part 3 roll-timing comparison (Issue #7).

``--fixtures`` runs the synthetic matched harness offline. Real-data mode needs
the frozen Section 0 handoff (Issues #4/#5), which does not exist yet; this
script refuses to substitute anything for it.
"""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rolling.analysis import compare_timing_rules, roll_event_records, summary_records
from src.rolling.fixture_experiment import build_synthetic_experiment
from src.validation.schemas import decimal_value, load_project_config


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", action="store_true", help="Run only the synthetic matched harness")
    parser.add_argument("--out", type=Path, help="Directory for roll_timing_summary.csv and roll_events_by_rule.csv")
    args = parser.parse_args()
    if not args.fixtures:
        parser.error("Real-data timing analysis awaits the frozen Section 0 handoff (#4/#5); use --fixtures")
    config = load_project_config(ROOT / "config/project.yaml")
    experiment = build_synthetic_experiment(config["selection_roll_reference_aum_usd"])
    result = compare_timing_rules(experiment, config["tracking_min_common_dates"],
                                  decimal_value(config["tracking_min_candidate_survival"]))
    summary, events = summary_records(result), roll_event_records(result)
    if args.out:
        write_csv(args.out / "roll_timing_summary.csv", summary)
        write_csv(args.out / "roll_events_by_rule.csv", events)
    print(json.dumps({"mode": "synthetic_fixture_only", "findings": False, "fixed_aum": experiment.fixed_aum,
                      "gates": result["gates"], "selection": result["selection"], "summary": summary},
                     default=str, indent=2))


if __name__ == "__main__":
    main()
