"""Real frozen-handoff checks for Jacopo's Part 3 capacity analysis."""
from decimal import Decimal
from pathlib import Path
import unittest

from src.capacity.phase_b import load_capacity_experiment, run_real_capacity_analysis
from src.capacity.analysis import allocation_summary_records, maturity_exposure_records
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

    def test_allocation_diagnostics_explain_recovery_without_affecting_selection(self):
        rows = {(row["aum_usd"], row["method"]): row for row in allocation_summary_records(self.result)}
        hard = rows[("1000000000", "hard_truncate")]
        sequential = rows[("1000000000", "overflow_sequential_two")]
        weighted = rows[("1000000000", "overflow_adv_weighted")]
        self.assertEqual(hard["residual_recovered_notional"], "0.00")
        self.assertGreater(Decimal(sequential["residual_recovered_notional"]), Decimal(0))
        self.assertGreater(Decimal(sequential["deferred_notional_share"]), Decimal(0))
        self.assertGreater(Decimal(weighted["weighted_deferred_maturity_rank"]), Decimal(0))
        exposure = maturity_exposure_records(self.result)
        self.assertTrue(any(row["allocation_role"] == "deferred" and row["method"] == "overflow_next"
                            for row in exposure))


if __name__ == "__main__":
    unittest.main()
