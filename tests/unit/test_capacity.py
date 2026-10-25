"""Deterministic capacity rules and fractional fixture-driven construction."""
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
    capacity_rounding_targets,
    compute_adv20,
    decision_time_dollar_contract_value,
    deferred_overflow_candidates,
    integer_capacity_limit,
)
from src.data.fixtures import load_fixture
from src.validation.schemas import ContractError, utc_ns

ROOT = Path(__file__).resolve().parents[2]


class CapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_fixture(ROOT / "data/fixtures/phase_a.json")
        cls.capacity_fixture = json.loads((ROOT / "data/fixtures/capacity_phase_a.json").read_text())

    def _portfolio_inputs(self):
        case = self.capacity_fixture["portfolio_case"]
        curve = [row for row in self.fixture.raw["baseline_path_case"]["eligible_curve"]
                 if row["date"] == case["curve_date"] and row["commodity"] == case["commodity"]]
        target = {key: value for key, value in case.items() if key not in {"curve_date", "note"}}
        return case, target, curve

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

    def test_decision_time_contract_values_use_publication_safe_settlement_and_multiplier(self):
        context = self.fixture.calendar.decision("2030-02-06")
        expected = {"ZC_SYN_NEAR": Decimal("20250"),
                    "ZC_SYN_NEXT": Decimal("50000"),
                    "ZC_SYN_FAR": Decimal("60200")}
        for contract, value in expected.items():
            result = decision_time_dollar_contract_value(
                self.fixture.observations, self.fixture.catalogue, contract, context)
            self.assertTrue(result.available)
            self.assertEqual(result.value, value)
            self.assertLessEqual(
                self.fixture.calendar.indices[result.settlement_date], context.decision_index)
            self.assertLessEqual(utc_ns(result.publication_timestamp), context.cutoff_ns)
        self.assertNotEqual(expected["ZC_SYN_NEAR"], expected["ZC_SYN_NEXT"])

    def test_integer_capacity_floors_and_handles_unavailable_adv(self):
        self.assertEqual(integer_capacity_limit("100.5"), 2)
        self.assertEqual(integer_capacity_limit("0"), 0)
        self.assertIsNone(integer_capacity_limit(None))
        self.assertEqual(integer_capacity_limit("10", "0.025", 4), 1)
        for args in (("-1",), ("10", "0"), ("10", "0.025", 0), ("10", "0.025", True)):
            with self.subTest(args=args), self.assertRaises(ContractError):
                integer_capacity_limit(*args)

    def test_primary_cap_preserves_fractional_quantity_and_exposes_notional_residual(self):
        result = apply_primary_cap("10.5", 7, "200")
        self.assertEqual(result.requested_contracts, Decimal("10.5"))
        self.assertEqual(result.implemented_contracts, Decimal("7"))
        self.assertEqual(result.residual_contracts, Decimal("3.5"))
        self.assertEqual(result.requested_notional, Decimal("2100.0"))
        self.assertEqual(result.implemented_notional, Decimal("1400"))
        self.assertEqual(result.residual_notional, Decimal("700.0"))
        self.assertTrue(result.capacity_binds)
        unbound = apply_primary_cap("6.49", 7, "200")
        self.assertEqual(unbound.implemented_contracts, Decimal("6.49"))
        self.assertEqual(unbound.residual_notional, Decimal("0.00"))
        self.assertFalse(unbound.capacity_binds)
        with self.assertRaises(ContractError):
            apply_primary_cap("-1", 2, "200")

    def test_deferred_candidates_are_relative_and_maturity_ordered(self):
        day = "2030-02-05"
        rows = [row for row in self.fixture.raw["baseline_path_case"]["eligible_curve"]
                if row["date"] == day and row["commodity"] == "ZC"]
        result = deferred_overflow_candidates("ZC_SYN_NEAR", rows, self.fixture.catalogue, day)
        self.assertEqual(result, ("ZC_SYN_NEXT", "ZC_SYN_FAR"))
        result = deferred_overflow_candidates("ZC_SYN_NEXT", rows, self.fixture.catalogue, day)
        self.assertEqual(result, ("ZC_SYN_FAR",))
        with self.assertRaises(ContractError):
            deferred_overflow_candidates("ZC_SYN_NEAR", rows[1:], self.fixture.catalogue, day)

    def test_deferred_candidates_reject_incomplete_date_curve(self):
        day = "2030-02-05"
        rows = [row for row in self.fixture.raw["baseline_path_case"]["eligible_curve"]
                if row["date"] == day and row["commodity"] == "ZC"
                and row["contract"] != "ZC_SYN_FAR"]
        with self.assertRaisesRegex(ContractError, "curve is incomplete.*ZC_SYN_FAR"):
            deferred_overflow_candidates("ZC_SYN_NEAR", rows, self.fixture.catalogue, day)

    def test_next_overflow_converts_dollar_residual_to_one_contract_value(self):
        result = allocate_overflow_next(
            "550", ("near", "far"), {"near": 3, "far": 99},
            {"near": "100", "far": "50"})
        self.assertEqual(dict(result.allocations), {"near": Decimal("3")})
        self.assertEqual(result.unimplemented_notional, Decimal("250"))
        self.assertEqual(dict(result.demand_contracts), {"near": Decimal("5.5")})
        self.assertIn("overflow_residual_unimplemented", result.flags)
        self.assertEqual(dict(allocate_overflow_next(0, ("near",), {"near": 3},
                                                     {"near": "100"}).allocations), {})

    def test_sequential_overflow_decimal_round_trip_never_creates_negative_later_fill(self):
        result = allocate_overflow_sequential(
            "0.8999999999999999999999999999", ("first", "second"),
            {"first": 3, "second": 4}, {"first": "0.3", "second": "0.1"})
        self.assertGreaterEqual(result.allocations["first"], Decimal(0))
        self.assertGreaterEqual(result.allocations["second"], Decimal(0))
        self.assertLessEqual(result.allocations["first"], Decimal(3))
        self.assertLessEqual(result.allocations["second"], Decimal(4))
        self.assertEqual(result.unimplemented_notional, Decimal(0))

    def test_capacity_to_rounding_adapter_rejects_missing_source_weight(self):
        _, target, curve = self._portfolio_inputs()
        target.pop("target_weight")
        portfolio = build_capacity_portfolio(
            [target], curve, self.fixture.observations,
            self.fixture.catalogue, CapacityMethod.OVERFLOW_NEXT)
        with self.assertRaisesRegex(ContractError, "source target_weight"):
            capacity_rounding_targets(portfolio)

    def test_capacity_to_rounding_adapter_supplies_truthful_positive_row_metadata(self):
        case, target, curve = self._portfolio_inputs()
        requested_notional = Decimal(target["target_notional"])
        source_weight = Decimal(target["target_weight"])
        for method in CapacityMethod:
            with self.subTest(method=method):
                portfolio = build_capacity_portfolio(
                    [target], curve, self.fixture.observations,
                    self.fixture.catalogue, method)
                rounding_targets = capacity_rounding_targets(portfolio)
                self.assertTrue(rounding_targets)
                for rounding_target in rounding_targets:
                    self.assertEqual(set(rounding_target), {
                        "commodity", "contract", "maturity_rank",
                        "post_capacity_fractional_contracts", "dollar_contract_notional",
                        "capacity_limit_contracts", "target_weight", "target_notional",
                    })
                    self.assertIsInstance(rounding_target["target_weight"], Decimal)
                    self.assertIsInstance(rounding_target["target_notional"], Decimal)
                    self.assertGreaterEqual(rounding_target["target_weight"], Decimal(0))
                    self.assertGreaterEqual(rounding_target["target_notional"], Decimal(0))
                    self.assertEqual(
                        rounding_target["target_notional"],
                        rounding_target["post_capacity_fractional_contracts"]
                        * rounding_target["dollar_contract_notional"],
                    )
                positive_overflow = [row for row in rounding_targets
                                     if row["contract"] != case["primary_contract"]
                                     and row["post_capacity_fractional_contracts"] > 0]
                for row in positive_overflow:
                    self.assertGreater(row["target_weight"], Decimal(0))
                    self.assertGreater(row["target_notional"], Decimal(0))
                represented = sum((row["target_notional"] for row in rounding_targets), Decimal(0))
                unimplemented = portfolio.diagnostics[0]["unimplemented_notional"]
                self.assertLessEqual(abs(represented + unimplemented - requested_notional),
                                     Decimal("0.01"))
                expected_weight = source_weight * represented / requested_notional
                actual_weight = sum((row["target_weight"] for row in rounding_targets), Decimal(0))
                self.assertLessEqual(abs(actual_weight - expected_weight), Decimal("0.00000000000000000001"))

    def test_sequential_overflow_respects_caps_and_unequal_contract_values(self):
        case = next(row for row in self.capacity_fixture["allocation_cases"]
                    if row["name"] == "sequential_overflow_respects_each_cap")
        result = allocate_overflow_sequential(
            case["residual_notional"], case["candidates"], case["capacity_limits"],
            case["dollar_contract_values"])
        self.assertEqual(dict(result.allocations),
                         {"first": Decimal("3"), "second": Decimal("4")})
        self.assertEqual(result.unimplemented_notional, Decimal("200"))
        self.assertNotIn("third", result.allocations)

    def test_adv_weighted_reallocates_dollar_residual_after_candidate_saturates(self):
        case = next(row for row in self.capacity_fixture["allocation_cases"]
                    if row["name"] == "adv_weighted_reallocates_after_first_candidate_caps")
        result = allocate_overflow_adv_weighted(
            case["residual_notional"], case["candidates"], case["capacity_limits"],
            case["dollar_contract_values"], case["adv20_by_contract"])
        self.assertEqual(dict(result.allocations),
                         {"first": Decimal("1"), "second": Decimal("19")})
        self.assertEqual(result.unimplemented_notional, Decimal("0"))
        self.assertLessEqual(result.allocations["first"], Decimal(case["capacity_limits"]["first"]))
        self.assertLessEqual(result.allocations["second"], Decimal(case["capacity_limits"]["second"]))
        self.assertEqual(sum(result.demand_notional.values()), Decimal("1000"))

    def test_adv_weighted_no_valid_adv_leaves_notional_residual_and_flags_it(self):
        case = next(row for row in self.capacity_fixture["allocation_cases"]
                    if row["name"] == "adv_weighted_no_valid_adv_fails_closed")
        result = allocate_overflow_adv_weighted(
            case["residual_notional"], case["candidates"], case["capacity_limits"],
            case["dollar_contract_values"], case["adv20_by_contract"])
        self.assertEqual(dict(result.allocations),
                         {"first": Decimal("0"), "second": Decimal("0")})
        self.assertEqual(result.unimplemented_notional, Decimal("1000"))
        self.assertIn(case["expected_flag"], result.flags)

    def test_builder_outputs_fractional_quantities_and_contract_ceilings(self):
        case, target, curve = self._portfolio_inputs()
        methods = {method: build_capacity_portfolio(
            [target], curve, self.fixture.observations, self.fixture.catalogue, method)
            for method in CapacityMethod}
        baseline = methods[CapacityMethod.HARD_TRUNCATE]
        next_method = methods[CapacityMethod.OVERFLOW_NEXT]
        sequential = methods[CapacityMethod.OVERFLOW_SEQUENTIAL]
        weighted = methods[CapacityMethod.OVERFLOW_ADV_WEIGHTED]
        expected_caps = {"ZC_SYN_NEAR": 1276, "ZC_SYN_NEXT": 30, "ZC_SYN_FAR": 33}
        self.assertEqual(baseline.diagnostics[0]["primary_capacity_limit_contracts"], 1276)
        self.assertEqual(baseline.diagnostics[0]["primary_residual_notional"], Decimal("2511000"))
        self.assertEqual(baseline.diagnostics[0]["unimplemented_notional"], Decimal("2511000"))
        self.assertEqual(dict(next_method.diagnostics[0]["overflow_allocations"]),
                         {"ZC_SYN_NEXT": Decimal("30")})
        self.assertEqual(next_method.diagnostics[0]["unimplemented_notional"], Decimal("1011000"))
        self.assertEqual(dict(sequential.diagnostics[0]["overflow_allocations"])["ZC_SYN_NEXT"],
                         Decimal("30"))
        self.assertAlmostEqual(float(dict(sequential.diagnostics[0]["overflow_allocations"])["ZC_SYN_FAR"]),
                               1011000 / 60200, places=12)
        self.assertEqual(weighted.diagnostics[0]["unimplemented_notional"], Decimal("0"))
        for portfolio in methods.values():
            for row in portfolio.rows:
                self.assertIs(type(row.capacity_limit_contracts), int)
                self.assertGreaterEqual(row.maturity_rank, 1)
                self.assertLessEqual(row.post_capacity_fractional_contracts,
                                     Decimal(row.capacity_limit_contracts))
            self.assertNotIn("implemented_contracts", portfolio.rows[0].to_record())
            self.assertEqual(portfolio.rows[0].dollar_contract_notional, Decimal("20250"))
        self.assertTrue(any(row.post_capacity_fractional_contracts % 1 != 0
                            for row in sequential.rows))
        self.assertEqual({row.contract: row.capacity_limit_contracts for row in sequential.rows},
                         expected_caps)

    def test_builder_does_not_overflow_when_primary_capacity_does_not_bind(self):
        _, target, curve = self._portfolio_inputs()
        target["pre_capacity_contracts"] = "10"
        target["target_notional"] = "202500"
        portfolio = build_capacity_portfolio(
            [target], curve, self.fixture.observations, self.fixture.catalogue,
            CapacityMethod.OVERFLOW_SEQUENTIAL)
        self.assertEqual(len(portfolio.rows), 1)
        self.assertEqual(portfolio.rows[0].contract, "ZC_SYN_NEAR")
        self.assertEqual(portfolio.rows[0].post_capacity_fractional_contracts, Decimal("10"))
        self.assertEqual(portfolio.diagnostics[0]["overflow_allocations"], {})

    def test_builder_rejects_inconsistent_target_notional(self):
        _, target, curve = self._portfolio_inputs()
        target["target_notional"] = "333333"
        with self.assertRaisesRegex(ContractError, "Target notional must match"):
            build_capacity_portfolio([target], curve, self.fixture.observations,
                                     self.fixture.catalogue, CapacityMethod.HARD_TRUNCATE)

    def test_builder_accepts_dollar_only_target_without_exact_decimal_round_trip(self):
        _, target, curve = self._portfolio_inputs()
        target.pop("pre_capacity_contracts")
        target["target_notional"] = "333333.33"
        result = build_capacity_portfolio(
            [target], curve, self.fixture.observations, self.fixture.catalogue,
            CapacityMethod.HARD_TRUNCATE)
        self.assertEqual(result.rows[0].target_notional, Decimal("333333.33"))
        self.assertEqual(result.diagnostics[0]["requested_notional"], Decimal("333333.33"))
        self.assertAlmostEqual(float(result.rows[0].post_capacity_fractional_contracts),
                               333333.33 / 20250, places=12)

    def test_builder_allows_cent_rounding_when_quantity_and_notional_are_both_supplied(self):
        _, target, curve = self._portfolio_inputs()
        target["pre_capacity_contracts"] = str(Decimal("333333.33") / Decimal("20250"))
        target["target_notional"] = "333333.33"
        result = build_capacity_portfolio(
            [target], curve, self.fixture.observations, self.fixture.catalogue,
            CapacityMethod.HARD_TRUNCATE)
        self.assertEqual(result.rows[0].target_notional, Decimal("333333.33"))

    def test_builder_flags_when_deferred_adv_is_unavailable(self):
        _, target, curve = self._portfolio_inputs()
        curve = [dict(row, maturity_rank=None)
                 if row["contract"] in {"ZC_SYN_NEXT", "ZC_SYN_FAR"} else row
                 for row in curve]
        original_adv20 = self.fixture.observations.adv20

        def unavailable_deferred(contract, context):
            if contract in {"ZC_SYN_NEXT", "ZC_SYN_FAR"}:
                return {"available": False, "reason": "fixture_missing_candidate_adv20", "rows": []}
            return original_adv20(contract, context)

        with patch.object(self.fixture.observations, "adv20", side_effect=unavailable_deferred):
            result = build_capacity_portfolio(
                [target], curve, self.fixture.observations, self.fixture.catalogue,
                CapacityMethod.OVERFLOW_ADV_WEIGHTED)
        self.assertEqual(result.diagnostics[0]["unimplemented_notional"], Decimal("2511000"))
        self.assertIn("no_valid_positive_adv20_capacity_or_value", result.diagnostics[0]["flags"])
        candidate_rows = [row for row in result.rows if row.contract != "ZC_SYN_NEAR"]
        self.assertEqual(candidate_rows, [])
        self.assertTrue(all(type(row.capacity_limit_contracts) is int for row in result.rows))
        self.assertTrue(all(row.decision_cutoff == self.fixture.calendar.decision(target["date"]).cutoff_utc
                            for row in result.rows))


if __name__ == "__main__":
    unittest.main()
