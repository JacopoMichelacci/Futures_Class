"""Shared benchmark interpolation and metadata primitives, without strand rules."""
from decimal import Decimal
from pathlib import Path
import unittest

from src.benchmark.constant_maturity import constant_maturity_weights
from src.contracts.eligibility import is_eligible
from src.contracts.maturity import days_to_anchor
from src.data.fixtures import load_fixture
from src.portfolio.baseline import BASELINE, build_baseline_path
from src.validation.schemas import ContractError

ROOT = Path(__file__).resolve().parents[2]


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = load_fixture(ROOT / "data/fixtures/phase_a.json")

    def test_interpolation_and_fallback_match_hand_fixtures(self):
        for case in self.f.raw["interpolation_cases"]:
            result = constant_maturity_weights(case["days"])
            self.assertEqual(result.weights, {k: Decimal(v) for k, v in case["expected_weights"].items()})
            self.assertEqual(sum(result.weights.values()), 1)
            self.assertEqual(result.benchmark_fallback, case["fallback"])

    def test_empty_or_ineligible_maturities_fail(self):
        for values in ({}, {"expired": 0}, {"bad": -1}):
            with self.assertRaises(ContractError):
                constant_maturity_weights(values)

    def test_fixture_curve_days_match_explicit_calendar(self):
        for row in self.f.raw["eligible_curve"]:
            contract = self.f.catalogue[row["contract"]]
            self.assertEqual(days_to_anchor(self.f.calendar, row["date"], contract.anchor), row["days_to_anchor"])
            self.assertTrue(is_eligible(contract, row["date"]))
            self.assertFalse(is_eligible(contract, contract.anchor))

    def test_unknown_forward_calendar_fails_instead_of_guessing(self):
        with self.assertRaises(ContractError):
            days_to_anchor(self.f.calendar, self.f.calendar.dates[0], "2099-01-01")

    def test_frozen_baseline_interface_and_hand_event_consistent(self):
        self.assertEqual(BASELINE.roll_offset_sessions, -5)
        self.assertEqual(BASELINE.capacity_participation_rate, Decimal("0.025"))
        event = self.f.raw["roll_events"][0]
        outgoing = self.f.catalogue[event["outgoing_contract"]]
        incoming = self.f.catalogue[event["baseline_destination"]]
        self.assertEqual(event["anchor"], outgoing.anchor)
        self.assertEqual(days_to_anchor(self.f.calendar, event["date"], outgoing.anchor), 5)
        self.assertGreater(incoming.anchor, outgoing.anchor)

    def baseline_path(self, rows=None, dates=None):
        case = self.f.raw["baseline_path_case"]
        return build_baseline_path(case["eligible_curve"] if rows is None else rows,
                                   case["decision_dates"] if dates is None else dates,
                                   case["commodities"], self.f.calendar, self.f.catalogue)

    def test_generated_baseline_starts_nearest_holds_and_rolls_on_hand_date(self):
        path = self.baseline_path()
        self.assertEqual([p["contract"] for p in path.positions], self.f.raw["baseline_path_case"]["expected_contracts"])
        expected = {k: v for k, v in self.f.raw["roll_events"][0].items() if k != "note"}
        self.assertEqual(path.roll_events, (expected,))
        # Fri 1 Feb is five supplied sessions before Fri 8 Feb; calendar days
        # would give a different answer. Incoming exposure starts next interval.
        self.assertEqual(path.positions[1]["date"], "2030-02-01")
        self.assertEqual(path.positions[1]["contract"], "ZC_SYN_NEAR")
        self.assertEqual(path.positions[2]["date"], "2030-02-04")
        self.assertEqual(path.positions[2]["contract"], "ZC_SYN_NEXT")
        for p in path.positions:
            self.assertEqual(p["decision_cutoff"], self.f.calendar.decision(p["date"]).cutoff_utc)
            self.assertTrue(is_eligible(self.f.catalogue[p["contract"]], p["date"]))

    def test_generated_baseline_is_input_order_independent_and_never_reselects_nearer(self):
        rows = self.f.raw["baseline_path_case"]["eligible_curve"]
        self.assertEqual(self.baseline_path(list(reversed(rows))), self.baseline_path())
        self.assertTrue(all(p["contract"] == "ZC_SYN_NEXT" for p in self.baseline_path().positions[2:]))

    def test_baseline_skips_ineligible_next_deferred(self):
        rows = [dict(r, eligible=False) if r["contract"] == "ZC_SYN_NEXT" else dict(r)
                for r in self.f.raw["baseline_path_case"]["eligible_curve"]]
        path = self.baseline_path(rows)
        self.assertEqual(path.roll_events[0]["baseline_destination"], "ZC_SYN_FAR")
        self.assertEqual(path.roll_events[0]["date"], "2030-02-01")

    def test_baseline_exits_when_current_contract_becomes_ineligible(self):
        rows = [dict(r, eligible=False) if r["contract"] == "ZC_SYN_NEAR" and r["date"] >= "2030-01-31" else dict(r)
                for r in self.f.raw["baseline_path_case"]["eligible_curve"]]
        path = self.baseline_path(rows)
        self.assertEqual(path.roll_events[0]["date"], "2030-01-31")
        self.assertTrue(all(p["contract"] == "ZC_SYN_NEXT" for p in path.positions[1:]))

    def test_baseline_missing_destination_or_curve_fails_without_carrying(self):
        rows = self.f.raw["baseline_path_case"]["eligible_curve"]
        for invalid in ([r for r in rows if r["contract"] == "ZC_SYN_NEAR"],
                        [r for r in rows if r["date"] != "2030-02-01"],
                        [dict(r, eligible=False) for r in rows]):
            with self.subTest(rows=len(invalid)), self.assertRaises(ContractError):
                self.baseline_path(invalid)

    def test_baseline_rejects_skipped_sessions_duplicates_and_false_eligibility(self):
        case = self.f.raw["baseline_path_case"]
        with self.assertRaises(ContractError):
            self.baseline_path(dates=case["decision_dates"][::2])
        with self.assertRaises(ContractError):
            self.baseline_path(case["eligible_curve"] + [case["eligible_curve"][0]])
        rows = [dict(r, eligible=True) for r in case["eligible_curve"]]
        with self.assertRaises(ContractError):
            self.baseline_path(rows)

    def test_baseline_late_entry_rolls_due_nearest_immediately(self):
        case = self.f.raw["baseline_path_case"]
        dates = case["decision_dates"][3:]
        path = self.baseline_path([r for r in case["eligible_curve"] if r["date"] in dates], dates)
        self.assertEqual(path.roll_events[0]["date"], dates[0])
        self.assertTrue(all(p["contract"] == "ZC_SYN_NEXT" for p in path.positions))


if __name__ == "__main__":
    unittest.main()
