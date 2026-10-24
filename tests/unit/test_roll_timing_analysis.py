"""Part 3 timing comparison harness (Issue #7) on the synthetic matched experiment."""
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.portfolio.baseline import BASELINE
from src.rolling.analysis import (BASELINE_RULE, compare_timing_rules, crossover_search_window,
                                  roll_event_records, select_preferred_rule, timing_holdings)
from src.rolling.fixture_experiment import build_synthetic_experiment
from src.rolling.rules import RULES, build_baseline_roll_pairs
from src.validation.schemas import ContractError

ROOT = Path(__file__).resolve().parents[2]


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = build_synthetic_experiment()
        cls.r = compare_timing_rules(cls.e)

    def test_all_rules_share_one_common_date_mask_and_aum(self):
        self.assertEqual({row["rule"] for row in self.r["summary"]}, set(RULES))
        self.assertEqual({row["common_valid_dates"] for row in self.r["summary"]}, {106})
        self.assertTrue(all(d.fixed_aum == Decimal("100000000") for d in self.r["daily"]))
        self.assertTrue(all(set(d.returns) == {"benchmark", *(f"timing:{x}" for x in RULES)}
                            for d in self.r["daily"] if d.common_valid))
        self.assertTrue(self.r["gates"]["passed"])

    def test_every_rule_uses_the_unchanged_baseline_pairs(self):
        pairs = {(p.commodity, p.outgoing_contract, p.incoming_contract)
                 for p in build_baseline_roll_pairs(self.e.baseline_path)}
        for decisions in self.r["decisions"].values():
            self.assertEqual({(d["commodity"], d["outgoing_contract"], d["incoming_contract"])
                              for d in decisions}, pairs)
        self.assertEqual(BASELINE.roll_offset_sessions, -5)

    def test_baseline_rule_reproduces_baseline_contract_path(self):
        _, rows = timing_holdings(self.e, BASELINE_RULE, self.r["decisions"][BASELINE_RULE])
        expected = {(p["date"], p["contract"]) for p in self.e.baseline_path.positions}
        self.assertEqual({(h.date, h.contract) for h in rows}, expected)

    def test_only_timing_changes_quantities_follow_supplied_sizing(self):
        # Same supplied size per commodity under every rule; only the contract held differs.
        sizes = {}
        for rule in RULES:
            _, rows = timing_holdings(self.e, rule, self.r["decisions"][rule])
            for h in rows:
                sizes.setdefault(h.commodity, set()).add(h.implemented_contracts)
        self.assertTrue(all(len(v) == 1 for v in sizes.values()))

    def test_search_window_is_agreed_causal_boundary(self):
        for pair in build_baseline_roll_pairs(self.e.baseline_path):
            start, deadline = crossover_search_window(self.e.baseline_path, pair, self.e.calendar, self.e.catalogue)
            self.assertEqual(deadline, pair.baseline_event_date)
            self.assertLessEqual(start, deadline)
        for decision in self.r["decisions"]["volume_crossover"] + self.r["decisions"]["oi_crossover"]:
            self.assertGreaterEqual(decision["sessions_before_anchor"], 5)

    def test_fallback_frequency_and_trigger_reporting(self):
        rows = {row["rule"]: row for row in self.r["summary"]}
        self.assertEqual(rows["fixed_-5"]["fallback_events"], 0)
        self.assertEqual(rows["volume_crossover"]["fallback_events"], 1)  # ZW volume never leads.
        self.assertEqual(rows["oi_crossover"]["fallback_events"], 1)  # ZC OI leads only after -5.
        events = [x for x in roll_event_records(self.r) if x["rule"] == "volume_crossover"]
        zc = next(x for x in events if x["commodity"] == "ZC")
        self.assertEqual((zc["sessions_before_anchor"], zc["fallback_flag"]), (8, False))
        self.assertIsNotNone(zc["trigger_source_date"])
        self.assertLess(zc["trigger_source_date"], zc["roll_decision_date"])  # Lagged, published data.

    def test_rmse_is_positive_finite_daily_bp(self):
        for row in self.r["summary"]:
            self.assertTrue(row["tracking_rmse_bps"].is_finite() and row["tracking_rmse_bps"] > 0)

    def test_gate_failure_is_reported_not_hidden(self):
        result = compare_timing_rules(self.e, min_common_dates=500)
        self.assertFalse(result["gates"]["passed"])

    def test_failed_gate_selects_no_preferred_rule(self):
        for kwargs in ({"min_common_dates": 500}, {"min_survival": Decimal("1.01")}):
            result = compare_timing_rules(self.e, **kwargs)
            self.assertIsNone(result["selection"]["preferred_rule"])
            self.assertFalse(result["selection"]["resolved"])
            self.assertEqual(result["selection"]["blocked_reason"], "coverage_gate_failed")
            self.assertEqual(len(result["summary"]), 4)  # RMSE is still reported.
        self.assertEqual(self.r["selection"]["preferred_rule"], "fixed_-10")  # Passing gate selects.


class SelectionTests(unittest.TestCase):
    def test_clear_winner(self):
        out = select_preferred_rule({"fixed_-5": Decimal("10"), "fixed_-10": Decimal("9"),
                                     "volume_crossover": Decimal("8"), "oi_crossover": Decimal("9.5")})
        self.assertEqual((out["preferred_rule"], out["tied_rules"]), ("volume_crossover", ("volume_crossover",)))

    def test_difference_below_tolerance_prefers_simpler_and_baseline(self):
        out = select_preferred_rule({"fixed_-5": Decimal("10.09"), "fixed_-10": Decimal("10.2"),
                                     "volume_crossover": Decimal("10"), "oi_crossover": Decimal("11")})
        self.assertEqual(out["preferred_rule"], "fixed_-5")
        self.assertEqual(out["lowest_rmse_rule"], "volume_crossover")

    def test_exactly_tolerance_is_not_a_tie(self):
        out = select_preferred_rule({"fixed_-5": Decimal("10.1"), "fixed_-10": Decimal("11"),
                                     "volume_crossover": Decimal("10"), "oi_crossover": Decimal("11")})
        self.assertEqual(out["preferred_rule"], "volume_crossover")

    def test_crossover_only_tie_prefers_volume_over_oi(self):
        for volume, oi in (("10", "10.05"), ("10.05", "10")):
            out = select_preferred_rule({"fixed_-5": Decimal("11"), "fixed_-10": Decimal("11"),
                                         "volume_crossover": Decimal(volume), "oi_crossover": Decimal(oi)})
            self.assertTrue(out["resolved"])
            self.assertEqual(out["preferred_rule"], "volume_crossover")

    def test_requires_all_four_rules(self):
        with self.assertRaises(ContractError):
            select_preferred_rule({"fixed_-5": Decimal("1")})


class ScriptTests(unittest.TestCase):
    def test_script_requires_fixture_mode_and_writes_outputs(self):
        refused = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run_person2.py")],
                                 capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("exactly one of --fixtures or --real", refused.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            ran = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run_person2.py"), "--fixtures",
                                  "--out", tmp], capture_output=True, text=True, check=True)
            self.assertIn('"findings": false', ran.stdout)
            for name in ("roll_timing_summary.csv", "roll_events_by_rule.csv"):
                self.assertTrue((Path(tmp) / name).is_file())


if __name__ == "__main__":
    unittest.main()
