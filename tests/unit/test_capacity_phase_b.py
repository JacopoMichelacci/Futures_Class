"""Real frozen-handoff checks for Jacopo's Part 3 capacity analysis."""
from decimal import Decimal
from pathlib import Path
import unittest

from src.capacity.phase_b import load_capacity_experiment, run_real_capacity_analysis
from src.validation.schemas import load_project_config

ROOT = Path(__file__).resolve().parents[2]


class CapacityPhaseBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_project_config(ROOT / "config/project.yaml")
        cls.handoff = ROOT / "data/section0"
        cls.result = run_real_capacity_analysis(cls.handoff, cls.config)

    def test_adapter_preserves_the_frozen_capacity_reference_and_148_date_mask(self):
        experiment = load_capacity_experiment(self.handoff, self.config)
        self.assertEqual(len(experiment.frozen_dates), 148)
        self.assertEqual(experiment.frozen_dates[0], "2026-03-02")
        self.assertEqual(experiment.frozen_dates[-1], "2026-09-30")
        self.assertEqual(self.result["reference_result"]["aum"], Decimal("1000000000"))
        self.assertTrue(self.result["reference_result"]["gates"]["passed"])
        self.assertTrue(all(row.common_valid for row in self.result["reference_result"]["daily"]))

    def test_real_capacity_selection_uses_the_locked_tie_policy(self):
        selection = self.result["reference_result"]["selection"]
        self.assertEqual(selection["preferred_method"].value, "overflow_sequential_two")
        self.assertEqual([method.value for method in selection["tied_methods"]],
                         ["overflow_sequential_two", "overflow_adv_weighted"])
        rows = {row["method"].value: row for row in self.result["reference_result"]["summary"]}
        difference = rows["overflow_adv_weighted"]["tracking_rmse_bps"] - rows["overflow_sequential_two"]["tracking_rmse_bps"]
        self.assertGreater(difference, Decimal(0))
        self.assertLess(difference, Decimal("0.1"))

    def test_capacity_binds_only_at_the_frozen_one_billion_reference_in_the_core_grid(self):
        self.assertTrue(self.result["preflight"]["reference_capacity_binds"])
        self.assertFalse(self.result["preflight"]["fallback_activated"])
        for aum, comparison in self.result["results"].items():
            bindings = {row["binding_target_count"] for row in comparison["summary"]}
            if aum == Decimal("1000000000"):
                self.assertEqual(bindings, {444})
            else:
                self.assertEqual(bindings, {0})


if __name__ == "__main__":
    unittest.main()
