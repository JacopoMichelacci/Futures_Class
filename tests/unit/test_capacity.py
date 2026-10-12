"""Deterministic capacity rules and fixture-driven portfolio construction."""
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import unittest

from src.capacity.rules import (
    CapacityMethod,
    allocate_overflow_adv_weighted,
    allocate_overflow_next,
    allocate_overflow_sequential,
    apply_primary_cap,
    build_capacity_portfolio,
    compute_adv20,
    deferred_overflow_candidates,
    integer_capacity_limit,
)
from src.data.fixtures import load_fixture
from src.portfolio.holdings import HoldingsBook
from src.validation.schemas import ContractError

ROOT = Path(__file__).resolve().parents[2]


class CapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture(ROOT / "data/fixtures/phase_a.json")
        cls.capacity_fixture = json.loads((ROOT / "data/fixtures/capacity_phase_a.json").read_text())

    def test_compute_adv20_uses_twenty_sessions_before_decision_session(self):
        context = self.fixture.calendar.decision("2030-02-06")
        result = compute_adv20(self.fixture.observations, "ZC_SYN_NEAR", context)
        self.assertTrue(result.available)
        self.assertEqual(result.observations, 20)
        self.assertEqual(len(result.source_dates), 20)
        self.assertLess(max(self.fixture.calendar.indices[d] for d in result.source_dates), context.decision_index)
        self.assertEqual(result.window_end, "2030-02-04")
        self.assertEqual(result.value, Decimal("51065.45"))

    def test_compute_adv20_preserves_unavailable_reason(self):
        context = self.fixture.calendar.decision(self.fixture.calendar.dates[2])
        result = compute_adv20(self.fixture.observations, "ZC_SYN_NEAR", context)
        self.assertFalse(result.available)
        self.assertIsNone(result.value)
        self.assertEqual(result.reason, "insufficient_calendar_warmup")

    def test_compute_adv20_rejects_unknown_contract_and_mismatched_context(self):
        context = self.fixture.calendar.decision("2030-02-06")
        with self.assertRaises(ContractError):
            compute_adv20(self.fixture.observations, "UNKNOWN", context)
        with self.assertRaises(ContractError):
            compute_adv20(self.fixture.observations, "ZC_SYN_NEAR",
                          replace(context, calendar_id="different-calendar"))

    def test_integer_capacity_floors_and_handles_unavailable_adv(self):
        self.assertEqual(integer_capacity_limit("100.5"), 2)
        self.assertEqual(integer_capacity_limit("0"), 0)
        self.assertIsNone(integer_capacity_limit(None))
        self.assertEqual(integer_capacity_limit("10", "0.025", 4), 1)
        for args in (("-1",), ("10", "0"), ("10", "0.025", 0), ("10", "0.025", True)):
            with self.subTest(args=args), self.assertRaises(ContractError):
                integer_capacity_limit(*args)

    def test_apply_primary_cap_rounds_then_caps_and_reports_residual(self):
        result = apply_primary_cap("10.5", 7)
        self.assertEqual(result.rounded_requested_contracts, 11)
        self.assertEqual(result.implemented_contracts, 7)
        self.assertEqual(result.residual_contracts, 4)
        self.assertTrue(result.capacity_binds)
        unbound = apply_primary_cap("6.49", 7)
        self.assertEqual(unbound.implemented_contracts, 6)
        self.assertEqual(unbound.residual_contracts, 0)
        self.assertFalse(unbound.capacity_binds)
        with self.assertRaises(ContractError):
            apply_primary_cap("-1", 2)

    def test_deferred_candidates_are_relative_and_maturity_ordered(self):
        day = "2030-02-05"
        rows = [row for row in self.fixture.raw["baseline_path_case"]["eligible_curve"]
                if row["date"] == day and row["commodity"] == "ZC"]
        result = deferred_overflow_candidates("ZC_SYN_NEAR", rows, self.fixture.catalogue, day)
        self.assertEqual(result, ("ZC_SYN_NEXT", "ZC_SYN_FAR"))
        # A later-dated primary means contracts before it are never considered.
        result = deferred_overflow_candidates("ZC_SYN_NEXT", rows, self.fixture.catalogue, day)
        self.assertEqual(result, ("ZC_SYN_FAR",))
        with self.assertRaises(ContractError):
            deferred_overflow_candidates("ZC_SYN_NEAR", rows[1:], self.fixture.catalogue, day)

    def test_next_overflow_uses_only_nearest_candidate(self):
        result = allocate_overflow_next(5, ("near", "far"), {"near": 3, "far": 99})
        self.assertEqual(dict(result.allocations), {"near": 3})
        self.assertEqual(result.unimplemented_contracts, 2)
        self.assertIn("overflow_residual_unimplemented", result.flags)
        self.assertEqual(dict(allocate_overflow_next(0, ("near",), {"near": 3}).allocations), {})

    def test_sequential_overflow_fills_first_then_second_and_ignores_third(self):
        case = next(row for row in self.capacity_fixture["allocation_cases"]
                    if row["name"] == "sequential_overflow_respects_each_cap")
        result = allocate_overflow_sequential(case["residual_contracts"], case["candidates"],
                                              case["capacity_limits"])
        self.assertEqual(dict(result.allocations), case["expected_allocations"])
        self.assertEqual(result.unimplemented_contracts, case["expected_unimplemented"])
        self.assertNotIn("third", result.allocations)

    def test_adv_weighted_overflow_reallocates_after_capacity_saturation(self):
        case = next(row for row in self.capacity_fixture["allocation_cases"]
                    if row["name"] == "adv_weighted_reallocates_after_first_candidate_caps")
        result = allocate_overflow_adv_weighted(case["residual_contracts"], case["candidates"],
                                                case["capacity_limits"], case["adv20_by_contract"])
        self.assertEqual(dict(result.allocations), case["expected_allocations"])
        self.assertEqual(result.unimplemented_contracts, case["expected_unimplemented"])
        self.assertLessEqual(result.allocations["first"], case["capacity_limits"]["first"])
        self.assertLessEqual(result.allocations["second"], case["capacity_limits"]["second"])

    def test_adv_weighted_no_valid_adv_leaves_residual_and_flags_it(self):
        case = next(row for row in self.capacity_fixture["allocation_cases"]
                    if row["name"] == "adv_weighted_no_valid_adv_fails_closed")
        result = allocate_overflow_adv_weighted(case["residual_contracts"], case["candidates"],
                                                case["capacity_limits"], case["adv20_by_contract"])
        self.assertEqual(dict(result.allocations), case["expected_allocations"])
        self.assertEqual(result.unimplemented_contracts, case["expected_unimplemented"])
        self.assertIn(case["expected_flag"], result.flags)

    def test_adv_weighted_remainder_tie_break_is_deferred_order(self):
        result = allocate_overflow_adv_weighted(1, ("first", "second"),
                                                {"first": 2, "second": 2},
                                                {"first": 1, "second": 1})
        self.assertEqual(dict(result.allocations), {"first": 1, "second": 0})

    def test_builder_uses_shared_asof_and_only_overflows_when_primary_binds(self):
        case = self.capacity_fixture["portfolio_case"]
        curve = [row for row in self.fixture.raw["baseline_path_case"]["eligible_curve"]
                 if row["date"] == case["curve_date"] and row["commodity"] == case["commodity"]]
        target = {key: value for key, value in case.items() if key not in {"curve_date", "note"}}
        methods = {method: build_capacity_portfolio([target], curve, self.fixture.observations,
                                                     self.fixture.catalogue, method)
                   for method in CapacityMethod}
        baseline = methods[CapacityMethod.HARD_TRUNCATE]
        next_method = methods[CapacityMethod.OVERFLOW_NEXT]
        sequential = methods[CapacityMethod.OVERFLOW_SEQUENTIAL]
        weighted = methods[CapacityMethod.OVERFLOW_ADV_WEIGHTED]
        expected_caps = {"ZC_SYN_NEAR": 1276, "ZC_SYN_NEXT": 30, "ZC_SYN_FAR": 33}
        self.assertEqual(baseline.diagnostics[0]["primary_capacity_limit_contracts"], expected_caps["ZC_SYN_NEAR"])
        self.assertEqual(baseline.diagnostics[0]["overflow_allocations"], {})
        self.assertEqual(dict(next_method.diagnostics[0]["overflow_allocations"]), {"ZC_SYN_NEXT": 30})
        self.assertEqual(dict(sequential.diagnostics[0]["overflow_allocations"]),
                         {"ZC_SYN_NEXT": 30, "ZC_SYN_FAR": 10})
        self.assertEqual(dict(weighted.diagnostics[0]["overflow_allocations"]),
                         {"ZC_SYN_NEXT": 19, "ZC_SYN_FAR": 21})
        self.assertEqual(
            {row.contract: row.details["capacity_limit_contracts"] for row in sequential.rows},
            expected_caps,
        )
        for portfolio in methods.values():
            book = HoldingsBook(portfolio.rows, self.fixture.calendar, self.fixture.catalogue)
            snapshot = book.snapshot(target["date"], portfolio.rows[0].strategy)
            limits = {row.contract: row.details.get("capacity_limit_contracts") for row in portfolio.rows}
            for contract, quantity in snapshot.items():
                self.assertLessEqual(quantity, Decimal(limits[contract]))

    def test_builder_does_not_add_deferred_rows_when_primary_capacity_does_not_bind(self):
        case = self.capacity_fixture["portfolio_case"]
        curve = [row for row in self.fixture.raw["baseline_path_case"]["eligible_curve"]
                 if row["date"] == case["curve_date"] and row["commodity"] == case["commodity"]]
        target = {key: value for key, value in case.items() if key not in {"curve_date", "note"}}
        target["pre_capacity_contracts"] = "10"
        portfolio = build_capacity_portfolio([target], curve, self.fixture.observations,
                                             self.fixture.catalogue, CapacityMethod.OVERFLOW_SEQUENTIAL)
        self.assertEqual(len(portfolio.rows), 1)
        self.assertEqual(portfolio.rows[0].contract, "ZC_SYN_NEAR")
        self.assertEqual(portfolio.rows[0].implemented_contracts, Decimal("10"))
        self.assertEqual(portfolio.diagnostics[0]["overflow_allocations"], {})

    def test_builder_flags_no_valid_deferred_adv_fallback(self):
        case = self.capacity_fixture["portfolio_case"]
        curve = [row for row in self.fixture.raw["baseline_path_case"]["eligible_curve"]
                 if row["date"] == case["curve_date"] and row["commodity"] == case["commodity"]]
        target = {key: value for key, value in case.items() if key not in {"curve_date", "note"}}
        original_adv20 = self.fixture.observations.adv20

        def unavailable_deferred(contract, context):
            if contract in {"ZC_SYN_NEXT", "ZC_SYN_FAR"}:
                return {"available": False, "reason": "fixture_missing_candidate_adv20", "rows": []}
            return original_adv20(contract, context)

        with patch.object(self.fixture.observations, "adv20", side_effect=unavailable_deferred):
            result = build_capacity_portfolio([target], curve, self.fixture.observations,
                                              self.fixture.catalogue, CapacityMethod.OVERFLOW_ADV_WEIGHTED)
        self.assertEqual(result.diagnostics[0]["unimplemented_contracts"], 40)
        self.assertIn("no_valid_positive_adv20_capacity", result.diagnostics[0]["flags"])
        candidate_rows = [row for row in result.rows if row.contract != "ZC_SYN_NEAR"]
        self.assertTrue(candidate_rows)
        self.assertTrue(all(row.implemented_contracts == 0 for row in candidate_rows))
        self.assertTrue(all(row.decision_cutoff == self.fixture.calendar.decision(target["date"]).cutoff_utc
                            for row in result.rows))


if __name__ == "__main__":
    unittest.main()
