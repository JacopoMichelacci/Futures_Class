"""Section 5 integrated portfolio (Issue #16) on the frozen Section 0 handoff."""
from decimal import Decimal
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from src.contracts.eligibility import is_eligible
from src.integration.exports import selection_rows, summary_rows, write_outputs
from src.integration.section5 import (load_integration_inputs, missing_frozen_inputs, read_strand_selections,
                                      run_section5)
from src.validation.schemas import ContractError, load_project_config

ROOT = Path(__file__).resolve().parents[2]
HANDOFF, OUTPUTS = ROOT / "data/section0", ROOT / "outputs"
FROZEN = {"destination": "target_75", "timing": "oi_crossover",
          "capacity": "overflow_sequential_two", "rounding": "toward_zero"}


class Section5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_project_config(ROOT / "config/project.yaml")
        cls.r = run_section5(HANDOFF, OUTPUTS, cls.config)

    def test_selections_come_from_strand_outputs_unchanged(self):
        self.assertEqual(read_strand_selections(OUTPUTS), FROZEN)
        self.assertEqual(self.r["selections"], FROZEN)

    def test_each_strand_winner_reproduces_through_the_pipeline(self):
        rows = selection_rows(self.r, OUTPUTS)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["reproduced_matches"] for r in rows), rows)

    def test_timing_never_rolls_after_the_scheduled_destination_date(self):
        for e in self.r["events"]:
            self.assertLessEqual(e["roll_decision_date"], e["scheduled_destination_date"])
            self.assertLessEqual(e["search_start"], e["roll_decision_date"])
            if e["fallback_to_scheduled_date"]:
                self.assertEqual(e["roll_decision_date"], e["scheduled_destination_date"])

    def test_destination_chain_is_consistent_per_commodity(self):
        for root in ("ZC", "ZS", "ZW"):
            chain = [e for e in self.r["events"] if e["commodity"] == root]
            self.assertTrue(all(a["incoming_contract"] == b["outgoing_contract"] for a, b in zip(chain, chain[1:])))
            self.assertTrue(all(a["roll_decision_date"] <= b["roll_decision_date"] for a, b in zip(chain, chain[1:])))

    def test_no_holding_at_or_after_first_notice_and_full_mask(self):
        catalogue = self.r["inputs"].catalogue
        self.assertTrue(all(is_eligible(catalogue[h["contract"]], h["date"]) for h in self.r["holdings"]))
        self.assertTrue(all(s["common_valid_dates"] == 148 for s in self.r["summary"]))
        self.assertEqual({s["fixed_aum"] for s in self.r["summary"]}, {1000000, 10000000, 100000000})

    def test_missing_frozen_input_is_reported_not_substituted(self):
        self.assertEqual(self.r["unavailable"][0]["fixed_aum"], 1000000000)
        self.assertIn("ZSK7", self.r["unavailable"][0]["reason"])
        inputs = self.r["inputs"]
        weights = {k: Decimal(str(v)) for k, v in self.config["target_weights"].items()}
        self.assertEqual(missing_frozen_inputs(inputs, self.r["path"], 1000000000, FROZEN["capacity"], weights),
                         ("ZSK7",))
        self.assertEqual(missing_frozen_inputs(inputs, self.r["path"], 100000000, FROZEN["capacity"], weights), ())

    def test_integrated_vs_baseline_results_are_reproduced(self):
        rmse = {s["fixed_aum"]: (s["baseline_rmse_bps"], s["integrated_rmse_bps"]) for s in self.r["summary"]}
        self.assertAlmostEqual(float(rmse[10000000][0]), 7.258070814444036, places=12)
        self.assertAlmostEqual(float(rmse[10000000][1]), 8.365114754749015, places=9)
        self.assertGreater(self.r["mean_days_to_fnd"]["integrated"], 60)

    def test_outputs_are_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_outputs(self.r, OUTPUTS, tmp)
            for name in ("integrated_summary.csv", "baseline_vs_integrated_daily.csv", "strand_selections.csv",
                         "integrated_roll_events.csv", "tracking_rmse_baseline_vs_integrated_by_aum.png",
                         "strand_selected_vs_baseline.png", "maturity_baseline_vs_integrated.png", "HANDOFF.md"):
                self.assertIn(name, manifest["output_hashes"])
            text = (Path(tmp) / "HANDOFF.md").read_text()
            self.assertIn("not evaluable", text)
            self.assertEqual(len(summary_rows(self.r)), 4)

    def test_tampered_handoff_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "section0"
            shutil.copytree(HANDOFF, copy)
            freeze = json.loads((copy / "freeze.json").read_text())
            freeze["frozen_dates"] = freeze["frozen_dates"][1:]
            (copy / "freeze.json").write_text(json.dumps(freeze))
            with self.assertRaises(ContractError):
                load_integration_inputs(copy, self.config)


if __name__ == "__main__":
    unittest.main()
