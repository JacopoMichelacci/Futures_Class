"""Tests for Issue #14 evaluation logic, independent of real Section 0 data."""
import unittest
from unittest.mock import patch
from src.selection.analysis import (
    RULES, DestinationInputs, StrategyResult, compare_daily, run_experiment
)


class TestPart3(unittest.TestCase):
    def sample(self, n=120):
        dates = tuple(f"session{i:03}" for i in range(n))
        benchmark = {d: 0.001 for d in dates}
        returns = {rule: {d: 0.001 + i * 0.0001 for d in dates}
                   for i, rule in enumerate(RULES)}
        return dates, benchmark, returns

    def test_common_mask_and_fixed_aum(self):
        dates, benchmark, returns = self.sample()
        returns["target_45"][dates[0]] = None
        result = compare_daily(returns, benchmark, dates, 100_000_000)
        self.assertEqual(len(result["common_dates"]), 119)
        self.assertTrue(result["coverage_passed"])
        self.assertEqual(len(result["daily"]), 120)
        self.assertFalse(result["daily"][0]["in_common_mask"])
        self.assertTrue(all(r["reference_aum_usd"] == 100_000_000
                            for r in result["summary"]))

    def test_minimum_dates_and_survival(self):
        dates, benchmark, returns = self.sample(99)
        result = compare_daily(returns, benchmark, dates, 100_000_000)
        self.assertFalse(result["coverage_passed"])
        self.assertIsNone(result["preferred_rule"])
        dates, benchmark, returns = self.sample()
        for rule in RULES:
            for d in dates[70:]:
                returns[rule][d] = None
        result = compare_daily(returns, benchmark, dates, 100_000_000)
        self.assertFalse(result["coverage_passed"])

    def test_tie_unresolved_without_locked_order(self):
        dates, benchmark, returns = self.sample()
        returns["second_deferred"] = dict(returns["next_deferred"])
        result = compare_daily(returns, benchmark, dates, 100_000_000)
        self.assertTrue(result["tie_unresolved"])
        self.assertIsNone(result["preferred_rule"])
        result = compare_daily(returns, benchmark, dates, 100_000_000,
                               tie_priority=RULES)
        self.assertEqual(result["preferred_rule"], "next_deferred")

    def test_tie_threshold_strict(self):
        dates, benchmark, returns = self.sample()
        # 0.1bp exactly above best: not a tie.
        returns["second_deferred"] = {d: .00101 for d in dates}
        result = compare_daily(returns, benchmark, dates, 100_000_000)
        self.assertEqual(result["preferred_rule"], "next_deferred")

    def test_reject_unlocked_rules(self):
        dates, benchmark, returns = self.sample()
        returns.pop("target_75")
        with self.assertRaises(ValueError):
            compare_daily(returns, benchmark, dates, 100_000_000)

    def test_evaluate_callback_receives_same_aum(self):
        dates, benchmark, returns = self.sample()
        inputs = DestinationInputs(object(), (), object(), {}, benchmark,
                                   dates, 100_000_000)
        calls = []
        class Schedule:
            events = ({"fallback_flag": False},)
        def evaluate(rule, path, aum):
            calls.append((rule, path, aum))
            return StrategyResult(returns[rule], (45., 50.), ("session005",))
        with patch("src.selection.analysis.build_paths",
                   return_value=({r: object() for r in RULES},
                                 {r: Schedule() for r in RULES})):
            result = run_experiment(inputs, evaluate)
        self.assertEqual(len(calls), 4)
        self.assertEqual({c[2] for c in calls}, {100_000_000})
        self.assertEqual(result["summary"][0]["mean_maturity_sessions"], 47.5)

    def test_no_synthetic_canonical_export(self):
        from src.selection.exports import export_results
        with self.assertRaises(ValueError):
            export_results({}, "outputs/person1", provenance="synthetic")


if __name__ == "__main__":
    unittest.main()
