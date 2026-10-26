"""Part 3 roll-timing comparison (Issue #7).

``--real`` analyses the frozen Section 0 handoff (Databento GLBX.MDP3-derived).
``--fixtures`` runs the separate synthetic harness demonstration.
"""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rolling.analysis import compare_timing_rules, roll_event_records, summary_records
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
    parser.add_argument("--real", action="store_true", help="Analyse only the frozen Section 0 handoff")
    parser.add_argument("--handoff-dir", type=Path, default=ROOT / "data/section0")
    parser.add_argument("--out", type=Path, help="Output directory (real default: outputs/person2)")
    parser.add_argument("--chart-html", type=Path, help="Directory for self-contained offline chart pages")
    parser.add_argument("--chart-png", type=Path, nargs="+", help="Register browser-exported chart PNGs")
    args = parser.parse_args()
    if args.fixtures == args.real:
        parser.error("Choose exactly one of --fixtures or --real")
    config = load_project_config(ROOT / "config/project.yaml")
    gates = (config["tracking_min_common_dates"], decimal_value(config["tracking_min_candidate_survival"]))
    if args.real:
        from src.rolling.exports import register_chart_exports, write_chart_html, write_outputs
        from src.rolling.handoff import load_timing_experiment, verify_baseline_reproduction
        from src.section0.phase_b import plain
        out = args.out or ROOT / "outputs/person2"
        result = compare_timing_rules(load_timing_experiment(args.handoff_dir, config), *gates)
        verify_baseline_reproduction(result, args.handoff_dir)
        hashes = json.loads((args.handoff_dir / "output_hashes.json").read_text())
        manifest = write_outputs(result, out, hashes)
        if args.chart_html:
            write_chart_html(result, args.chart_html)
        if args.chart_png:
            register_chart_exports(out, args.chart_png)
            manifest = write_outputs(result, out, hashes)
        if not manifest["chart_export_verified"]:
            print("PNG export pending: use --chart-html DIR, the camera button on each page, then --chart-png.",
                  file=sys.stderr)
        print(json.dumps(plain({"mode": result["label"], "gates": result["gates"], "selection": result["selection"],
                                "summary": [{k: r[k] for k in ("rule", "tracking_rmse_bps", "fallback_events",
                                                                "mean_sessions_before_anchor")}
                                            for r in result["summary"]]}), indent=2))
        return
    from src.rolling.fixture_experiment import build_synthetic_experiment
    experiment = build_synthetic_experiment(config["selection_roll_reference_aum_usd"])
    result = compare_timing_rules(experiment, *gates)
    summary, events = summary_records(result), roll_event_records(result)
    if args.out:
        write_csv(args.out / "roll_timing_summary.csv", summary)
        write_csv(args.out / "roll_events_by_rule.csv", events)
    print(json.dumps({"mode": "synthetic_fixture_only", "findings": False, "fixed_aum": experiment.fixed_aum,
                      "gates": result["gates"], "selection": result["selection"], "summary": summary},
                     default=str, indent=2))


if __name__ == "__main__":
    main()
