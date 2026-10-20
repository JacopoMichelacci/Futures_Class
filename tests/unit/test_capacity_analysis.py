"""Part 3 capacity/AUM harness tests; fixture output is never a market finding."""
from decimal import Decimal
import unittest

from src.capacity.analysis import (
    METHODS,
    compare_capacity_across_aum,
    select_preferred_method,
    summary_records,
)
from src.capacity.fixture_experiment import build_synthetic_experiment
from src.capacity.rules import CapacityMethod
from src.validation.schemas import ContractError


class CapacityAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = compare_capacity_across_aum(
            build_synthetic_experiment(),
            (Decimal("1000000"), Decimal("10000000"), Decimal("100000000"), Decimal("1000000000")),
            Decimal("1000000000"), Decimal("2500000000"),
        )

    def test_runs_all_four_fixed_methods_over_the_required_grid_and_common_mask(self):
        self.assertEqual(set(self.result["results"]), {
            Decimal("1000000"), Decimal("10000000"), Decimal("100000000"), Decimal("1000000000"),
        })
        for comparison in self.result["results"].values():
            self.assertEqual({row["method"] for row in comparison["summary"]}, set(METHODS))
            self.assertTrue(comparison["gates"]["passed"])
            self.assertGreaterEqual(comparison["gates"]["common_valid_dates"], 100)
            self.assertGreaterEqual(comparison["gates"]["survival"], Decimal("0.70"))
            self.assertEqual({row.fixed_aum for row in comparison["daily"]}, {comparison["aum"]})

    def test_reference_preflight_uses_one_billion_when_capacity_binds(self):
        preflight = self.result["preflight"]
        self.assertTrue(preflight["reference_capacity_binds"])
        self.assertFalse(preflight["fallback_activated"])
        self.assertIsNone(preflight["fallback_capacity_binds"])
        self.assertFalse(preflight["capacity_limitation"])
        self.assertEqual(preflight["active_reference_aum"], Decimal("1000000000"))

    def test_failed_coverage_gate_blocks_selection_instead_of_selecting_on_inadequate_data(self):
        result = compare_capacity_across_aum(
            build_synthetic_experiment(), (Decimal("1000000"),), Decimal("1000000000"),
            Decimal("2500000000"), min_common_dates=106,
        )
        selection = result["results"][Decimal("1000000")]["selection"]
        self.assertIsNone(selection["preferred_method"])
        self.assertEqual(selection["blocked_reason"], "common_date_or_candidate_survival_gate_failed")

    def test_nonbinding_fallback_is_reported_as_a_limitation(self):
        result = compare_capacity_across_aum(
            build_synthetic_experiment(), (Decimal("1000000"),), Decimal("1000000"),
            Decimal("10000000"),
        )
        self.assertTrue(result["preflight"]["fallback_activated"])
        self.assertFalse(result["preflight"]["fallback_capacity_binds"])
        self.assertTrue(result["preflight"]["capacity_limitation"])

    def test_methods_share_primary_target_and_differ_only_when_capacity_binds(self):
        low = self.result["results"][Decimal("1000000")]
        self.assertEqual({row["unimplemented_notional"] for row in low["summary"]}, {Decimal(0)})
        high = self.result["results"][Decimal("1000000000")]
        by_method = {row["method"]: row for row in high["summary"]}
        self.assertGreater(by_method[CapacityMethod.HARD_TRUNCATE]["unimplemented_notional"],
                           by_method[CapacityMethod.OVERFLOW_NEXT]["unimplemented_notional"])
        self.assertGreater(by_method[CapacityMethod.OVERFLOW_NEXT]["unimplemented_notional"],
                           by_method[CapacityMethod.OVERFLOW_SEQUENTIAL]["unimplemented_notional"])

    def test_tie_rule_is_strictly_below_point_one_bp_and_prefers_simpler_method(self):
        values = {method: Decimal("1.0") for method in METHODS}
        values[CapacityMethod.OVERFLOW_NEXT] = Decimal("0.95")
        selected = select_preferred_method(values)
        self.assertEqual(selected["preferred_method"], CapacityMethod.HARD_TRUNCATE)
        self.assertIn(CapacityMethod.HARD_TRUNCATE, selected["tied_methods"])
        self.assertEqual(selected["lowest_rmse_method"], CapacityMethod.OVERFLOW_NEXT)
        values[CapacityMethod.HARD_TRUNCATE] = Decimal("1.05")
        selected = select_preferred_method(values)
        self.assertNotIn(CapacityMethod.HARD_TRUNCATE, selected["tied_methods"])

    def test_selection_requires_every_pre_specified_method(self):
        with self.assertRaises(ContractError):
            select_preferred_method({CapacityMethod.HARD_TRUNCATE: Decimal(0)})

    def test_flat_output_has_one_row_per_grid_method_pair(self):
        records = summary_records(self.result)
        self.assertEqual(len(records), 16)
        self.assertTrue(all(record["common_mask_gate_passed"] for record in records))


if __name__ == "__main__":
    unittest.main()
