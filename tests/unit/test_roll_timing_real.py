"""Real-data roll-timing analysis (Issue #7) on the committed frozen Section 0 handoff."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from src.contracts.eligibility import is_eligible
from src.rolling.analysis import BASELINE_RULE, compare_timing_rules
from src.rolling.exports import event_rows, summary_rows, write_outputs
from src.rolling.handoff import load_timing_experiment, verify_baseline_reproduction
from src.rolling.rules import RULES
from src.section0.phase_b import read_table
from src.validation.schemas import ContractError, decimal_value, load_project_config

ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "data/section0"


class RealTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_project_config(ROOT / "config/project.yaml")
        cls.e = load_timing_experiment(HANDOFF, cls.config)
        cls.r = compare_timing_rules(cls.e)

    def test_frozen_scope_reference_and_mask(self):
        self.assertEqual(self.e.fixed_aum, Decimal("10000000"))
        self.assertEqual((len(self.e.dates), self.e.dates[0], self.e.dates[-1]), (148, "2026-03-02", "2026-09-30"))
        self.assertEqual(self.e.calendar.decision(self.e.dates[0]).decision_date, "2026-02-27")
        self.assertTrue(all(self.e.validity[d] for d in self.e.dates))
        self.assertEqual(len(self.e.baseline_path.roll_events), 10)
        self.assertEqual((self.r["gates"]["common_valid_dates"], self.r["gates"]["passed"]), (148, True))

    def test_baseline_rule_reproduces_section0_exactly(self):
        self.assertTrue(verify_baseline_reproduction(self.r, HANDOFF))

    def test_crossovers_reconcile_to_section0_timing_inputs(self):
        rows = read_table(HANDOFF / "timing_inputs.parquet")
        for rule, stat in (("volume_crossover", "cleared_volume"), ("oi_crossover", "open_interest")):
            for d in self.r["decisions"][rule]:
                window = sorted((x for x in rows if x["statistic"] == stat
                                 and (x["outgoing_contract"], x["incoming_contract"])
                                 == (d["outgoing_contract"], d["incoming_contract"])
                                 and d["search_start"] <= x["decision_date"] < d["search_deadline"]),
                                key=lambda x: x["decision_date"])
                first = next((x["decision_date"] for x in window
                              if decimal_value(x["incoming_value"]) > decimal_value(x["outgoing_value"])), None)
                self.assertEqual(d["roll_decision_date"], first or d["search_deadline"])
                self.assertEqual(d["fallback_flag"], first is None)

    def test_same_pairs_dates_and_no_holding_at_or_after_anchor(self):
        pairs = {(e["commodity"], e["outgoing_contract"], e["baseline_destination"])
                 for e in self.e.baseline_path.roll_events}
        for rule in RULES:
            self.assertEqual({(d["commodity"], d["outgoing_contract"], d["incoming_contract"])
                              for d in self.r["decisions"][rule]}, pairs)
            self.assertTrue(all(is_eligible(self.e.catalogue[h.contract], h.date) for h in self.r["holdings"][rule]))
        self.assertTrue(all(d.common_valid and d.date in self.e.dates for d in self.r["daily"]))

    def test_selection_follows_lowest_rmse_and_locked_tie_rule(self):
        rows = summary_rows(self.r)
        best = min(rows, key=lambda r: r["tracking_rmse_bps_daily"])
        self.assertEqual(self.r["selection"]["preferred_rule"], best["rule"])
        self.assertEqual(self.r["selection"]["tied_rules"], (best["rule"],))
        self.assertEqual(best["rule"], "oi_crossover")  # Frozen-sample result, reproduced.

    def test_first_window_session_triggers_are_reported(self):
        early = [e for e in event_rows(self.r) if e["triggered_on_first_window_session"]]
        self.assertTrue(all(not e["fallback_flag"] and e["rule"] != BASELINE_RULE for e in early))
        self.assertEqual({(e["outgoing_contract"], e["incoming_contract"]) for e in early},
                         {("ZCU6", "ZCZ6"), ("ZSU6", "ZSX6")})

    def test_mask_is_honoured_not_dropped(self):
        masked = replace(self.e, validity={**self.e.validity, self.e.dates[10]: False})
        result = compare_timing_rules(masked)
        self.assertEqual(result["gates"]["common_valid_dates"], 147)
        self.assertFalse(result["daily"][10].common_valid)

    def test_tampered_handoff_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "section0"
            shutil.copytree(HANDOFF, copy)
            freeze = json.loads((copy / "freeze.json").read_text())
            freeze["preflight"]["references"]["timing"] = 100000000
            (copy / "freeze.json").write_text(json.dumps(freeze))
            with self.assertRaises(ContractError):
                load_timing_experiment(copy, self.config)

    def test_outputs_are_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_outputs(self.r, tmp, {})
            summary = read_csv(Path(tmp) / "roll_timing_summary.csv")
            self.assertEqual({r["rule"]: Decimal(r["tracking_rmse_bps_daily"]) for r in summary},
                             {r["rule"]: r["tracking_rmse_bps"] for r in self.r["summary"]})
            self.assertEqual(len(read_csv(Path(tmp) / "roll_events_by_rule.csv")), 40)
            self.assertEqual(len(read_csv(Path(tmp) / "roll_timing_daily.csv")), 148)
            self.assertIn("PERSON 2 TIMING HANDOFF READY", (Path(tmp) / "HANDOFF.md").read_text())
            self.assertEqual(manifest["selection"]["preferred_rule"], "oi_crossover")


def read_csv(path):
    import csv
    with Path(path).open() as stream:
        return list(csv.DictReader(stream))


if __name__ == "__main__":
    unittest.main()
