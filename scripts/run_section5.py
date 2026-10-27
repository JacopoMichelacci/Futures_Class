"""Section 5 integrated portfolio (Issue #16) on the frozen Section 0 handoff.

Reads each strand's selected rule from its committed outputs, combines them once in
the locked order, and compares the original baseline with the integrated portfolio.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.integration.exports import selection_rows, summary_rows, write_outputs
from src.integration.section5 import run_section5
from src.section0.phase_b import plain
from src.validation.schemas import load_project_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-dir", type=Path, default=ROOT / "data/section0")
    parser.add_argument("--outputs-dir", type=Path, default=ROOT / "outputs", help="Strand outputs to read")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/final")
    args = parser.parse_args()
    config = load_project_config(ROOT / "config/project.yaml")
    result = run_section5(args.handoff_dir, args.outputs_dir, config)
    write_outputs(result, args.outputs_dir, args.out)
    print(json.dumps(plain({"selections": result["selections"],
                            "strand_reconciliation": [{k: r[k] for k in ("strand", "selected_rule",
                                                       "strand_selected_rmse_bps", "reproduced_in_section5_rmse_bps",
                                                       "reproduced_matches")}
                                                      for r in selection_rows(result, args.outputs_dir)],
                            "baseline_vs_integrated": summary_rows(result),
                            "mean_days_to_fnd": result["mean_days_to_fnd"]}), indent=2))


if __name__ == "__main__":
    main()
