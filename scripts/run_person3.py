"""Run Jacopo's real-data Part 3 capacity analysis from frozen Section 0 inputs."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.capacity.analysis import diagnostic_records, summary_records
from src.capacity.phase_b import run_real_capacity_analysis
from src.validation.schemas import load_project_config


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _plot(result, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    records = summary_records(result)
    methods = sorted({row["method"] for row in records})
    colors = {"hard_truncate": "#1f77b4", "overflow_next": "#2ca02c",
              "overflow_sequential_two": "#d62728", "overflow_adv_weighted": "#ff7f0e"}
    fig, (axis, detail) = plt.subplots(1, 2, figsize=(11, 4.8), gridspec_kw={"width_ratios": [1.7, 1]})
    for method in methods:
        rows = [row for row in records if row["method"] == method]
        axis.plot([float(row["aum_usd"]) for row in rows],
                  [float(row["tracking_rmse_bps"]) for row in rows], marker="o", label=method,
                  color=colors[method])
    axis.set_xscale("log")
    axis.set_xlabel("Fixed AUM (USD)")
    axis.set_ylabel("Tracking RMSE (daily bp)")
    axis.set_title("Capacity handling across AUM")
    axis.grid(alpha=0.25)
    axis.legend()
    reference_rows = result["reference_result"]["summary"]
    detail.bar([row["method"].value for row in reference_rows],
               [float(row["tracking_rmse_bps"]) for row in reference_rows],
               color=[colors[row["method"].value] for row in reference_rows])
    detail.tick_params(axis="x", labelrotation=90)
    detail.set_ylabel("Tracking RMSE (daily bp)")
    detail.set_title("$1bn selection AUM")
    detail.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _handoff(result):
    reference = result["reference_result"]
    selection = reference["selection"]
    preferred = selection["preferred_method"]
    selected_row = next(row for row in reference["summary"] if row["method"] == preferred)
    tied = ", ".join(method.value for method in selection["tied_methods"])
    other_tied = next((row for row in reference["summary"]
                       if row["method"] != preferred and row["method"] in selection["tied_methods"]), None)
    tie_detail = (f"`{other_tied['method'].value}` has {other_tied['tracking_rmse_bps']:.6f} daily bp RMSE, "
                  f"only {other_tied['tracking_rmse_bps'] - selected_row['tracking_rmse_bps']:.6f} bp above "
                  f"`{preferred.value}`. This is strictly below the 0.1 daily-bp tie threshold. "
                  if other_tied is not None else "No other method falls within the 0.1 daily-bp tie threshold. ")
    return f"""# Part 3 capacity handoff (Issue #15)

**HANDOFF READY.** This real-data analysis consumes the merged frozen Section 0 Phase B bundle.

## Frozen inputs

- Returns: 2 March–30 September 2026
- Common valid dates: {reference['gates']['common_valid_dates']}/{len(reference['daily'])} ({reference['gates']['survival']})
- Capacity selection AUM: ${reference['aum']:,}
- Core reporting grid: $1m, $10m, $100m, $1bn
- Participation / liquidation horizon: 2.5% / one day
- Rounding: unchanged nearest half-up after capacity

## Selection

The selected capacity rule is **{preferred.value}** at the frozen ${reference['aum']:,} reference AUM, with **{selected_row['tracking_rmse_bps']:.6f} daily bp** Tracking RMSE.

The tied-with-best methods are: {tied}. {tie_detail}The simpler tied method is selected. No second ranking metric was used.

## Output files

- `capacity_summary.csv`: the four reference-AUM methods and selection fields.
- `capacity_by_aum.csv`: all four methods at each core AUM.
- `capacity_diagnostics.csv`: capacity-binding/residual-allocation diagnostics.
- `tracking_rmse_vs_aum.png`: primary presentation figure.

The retained `synthetic_fixture/` directory is a prior plumbing check and is not an empirical finding.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-dir", type=Path, default=ROOT / "data/section0")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/person3")
    args = parser.parse_args()
    config = load_project_config(ROOT / "config/project.yaml")
    result = run_real_capacity_analysis(args.handoff_dir, config)
    records = summary_records(result)
    reference_aum = result["reference_result"]["aum"]
    reference_records = [row for row in records if row["aum_usd"] == format(reference_aum, "f")]
    write_csv(args.out / "capacity_summary.csv", reference_records)
    write_csv(args.out / "capacity_by_aum.csv", records)
    write_csv(args.out / "capacity_diagnostics.csv", diagnostic_records(result))
    _plot(result, args.out / "tracking_rmse_vs_aum.png")
    (args.out / "HANDOFF.md").write_text(_handoff(result))
    print(json.dumps({"mode": "frozen_section0_phase_b", "findings": True,
                      "preflight": result["preflight"], "selection": result["reference_result"]["selection"],
                      "summary": reference_records}, default=str, indent=2))


if __name__ == "__main__":
    main()
