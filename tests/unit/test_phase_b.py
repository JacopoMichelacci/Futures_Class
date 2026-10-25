"""Shared Phase B safeguards and frozen empirical-input reproduction."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from src.contracts.maturity import days_to_anchor
from src.data.calendar import SessionCalendar, TradingSession
from src.section0.phase_b import (CapturingStore, build, choose_reference, coverage_gate,
    export, holdings_from_contract_path, load_inputs, read_table, target_and_cap)
from src.validation.production import validate_complete_curve, validate_post_capacity_targets
from src.validation.schemas import ContractError, load_project_config, utc_ns

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/section0"


class PhaseBPrimitivesTests(unittest.TestCase):
    def test_forward_maturity_dates_do_not_invent_future_decision_times(self):
        records = [TradingSession(d, d + "T18:15:00Z", d + "T18:15:00Z")
                   for d in ("2026-04-01", "2026-04-02")]
        original = SessionCalendar(records)
        extended = SessionCalendar(records, ["2026-04-01", "2026-04-02", "2026-04-06", "2026-04-07"])
        self.assertEqual(days_to_anchor(extended, "2026-04-02", "2026-04-07"), 2)
        self.assertEqual(original.calendar_id, extended.calendar_id)
        self.assertNotEqual(original.maturity_calendar_id, extended.maturity_calendar_id)
        with self.assertRaises(ContractError):
            extended.decision("2026-04-06")

    def test_forward_calendar_rejects_changed_prefix_and_bad_dates(self):
        records = [TradingSession(d, d + "T18:15:00Z", d + "T18:15:00Z")
                   for d in ("2026-04-01", "2026-04-02")]
        for horizon in (["2026-04-01", "2026-04-03"], ["2026-04-01"],
                        ["2026-04-01", "2026-04-02", "2026-04-02"],
                        ["2026-04-01", "2026-04-02", None]):
            with self.subTest(horizon=horizon), self.assertRaises(ContractError):
                SessionCalendar(records, horizon)

    def test_baseline_sizing_uses_dollar_price_unit_and_half_up(self):
        # 50,000 / (400 cents x $50) = 2.5 contracts, not 0.025.
        r = target_and_cap("150000", "0.3333333333333333333333333333", "400", "50", "1000", ".025")
        self.assertEqual(r["pre_capacity_contracts"], Decimal("2.5"))
        self.assertEqual(r["implemented_contracts"], 3)

    def test_capacity_precedes_rounding_and_preserves_original_target(self):
        r = target_and_cap("50000", "1", "400", "50", "80", ".025")
        self.assertEqual(r["target_notional"], 50000)
        self.assertEqual(r["pre_capacity_contracts"], Decimal("2.5"))
        self.assertEqual(r["post_capacity_fractional_contracts"], 2)
        self.assertEqual(r["implemented_contracts"], 2)
        self.assertTrue(r["capacity_binding"])

    def test_zero_capacity_is_explicit_zero_not_missing(self):
        r = target_and_cap(1000000, 1, 400, 50, 0, ".025")
        self.assertEqual(r["capacity_limit_contracts"], 0)
        self.assertEqual(r["implemented_contracts"], 0)
        self.assertEqual(r["target_notional"], 1000000)

    def test_invalid_sizing_does_not_create_holdings(self):
        for price in (0, -1, "NaN"):
            with self.assertRaises(ContractError):
                target_and_cap(1000000, 1, price, 50, 100, ".025")

    def test_reference_fallback_depends_only_on_capacity_and_stops(self):
        seen = []
        def check(a):
            seen.append(a)
            return {"binding_rows": int(a == 100000000)}
        selected, checks = choose_reference(100000000, 10000000, check)
        self.assertEqual(selected, 10000000)
        self.assertEqual(seen, [100000000, 10000000])
        with self.assertRaises(ContractError):
            choose_reference(100000000, 10000000, lambda a: {"binding_rows": 1})

    def test_no_unneeded_fallback_and_no_capacity_tuning(self):
        calls = []
        selected, _ = choose_reference(1000000, 250000,
            lambda a: calls.append(a) or {"binding_rows": 0})
        self.assertEqual((selected, calls), (1000000, [1000000]))
        selected, checks = choose_reference(1000000000, 2500000000,
            lambda a: {"binding_rows": 0}, require_binding=True)
        self.assertEqual(selected, 2500000000)
        self.assertEqual(len(checks), 2)

    def test_148_candidate_gate_requires_104_dates(self):
        config = load_project_config(ROOT / "config/project.yaml")
        self.assertFalse(coverage_gate([True] * 103 + [False] * 45, 148, config)["passed"])
        self.assertTrue(coverage_gate([True] * 104 + [False] * 44, 148, config)["passed"])


class FrozenPhaseBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_project_config(ROOT / "config/project.yaml")
        cls.inputs = load_inputs(DATA / "inputs", cls.config)
        cls.tables, cls.metadata = build(cls.inputs, cls.config)

    def test_frozen_scope_dates_AUM_and_all_four_masks(self):
        self.assertEqual(self.metadata["coverage"]["common_valid_dates"], 148)
        self.assertEqual(self.inputs.calendar.decision(self.inputs.dates[0]).decision_date, "2026-02-27")
        self.assertEqual(self.metadata["preflight"]["references"],
                         dict(destination=10000000, timing=10000000, capacity=1000000000, rounding=1000000))
        masks = self.tables["common_masks"]
        for strand in ("destination", "timing", "capacity", "rounding"):
            self.assertEqual([r["date"] for r in masks if r["strand"] == strand and r["common_valid_date"]], self.inputs.dates)
        self.assertFalse(self.metadata["metric_comparisons_run"])

    def test_full_curve_keeps_explicit_ineligibility_and_metadata_only_contract(self):
        curve = self.tables["eligible_curve"]
        first = [r for r in curve if r["date"] == "2026-02-27"]
        self.assertEqual(len(first), 26)
        self.assertEqual({r["contract"] for r in first if not r["eligible"]}, {"ZCH6", "ZSH6", "ZWH6"})
        self.assertTrue(next(r for r in first if r["contract"] == "ZSK7")["eligible"])
        dates = [self.inputs.calendar.decision(d).decision_date for d in self.inputs.dates]
        with self.assertRaises(ContractError):
            validate_complete_curve(curve[1:], dates, self.inputs.catalogue, self.inputs.calendar)

    def test_benchmark_is_fractional_daily_uncapped_with_locked_interpolation(self):
        grouped = {}
        for r in self.tables["benchmark_holdings"]:
            key = (r["date"], r["commodity"], r["fixed_aum"])
            grouped.setdefault(key, []).append(r)
            expected = r["target_notional"] / (r["price"] * 50)
            self.assertEqual(r["fractional_contracts"], expected)
            self.assertLessEqual(utc_ns(r["inputs_as_of"]), utc_ns(r["decision_cutoff"]))
        for key, rows in grouped.items():
            self.assertEqual(sum(r["interpolation_weight"] for r in rows), 1)
            total = sum(r["target_notional"] for r in rows)
            self.assertLess(abs(total - Decimal(key[2]) * self.config["target_weights"][key[1]]), Decimal("0.000000000001"))
        self.assertEqual(len(self.metadata["benchmark_fallbacks"]), 6)
        self.assertTrue(any(r["fractional_contracts"] % 1 != 0 for r in self.tables["benchmark_holdings"]))

    def test_baseline_events_and_shared_adapter_preserve_five_session_rule(self):
        self.assertEqual(len(self.tables["roll_events"]), 10)
        for event in self.tables["roll_events"]:
            self.assertEqual(days_to_anchor(self.inputs.calendar, event["date"], event["anchor"]), 5)
        holdings, targets = holdings_from_contract_path(self.tables["baseline_contract_path"],
            self.tables["decision_inputs"], self.inputs.catalogue.contracts, self.inputs.calendar,
            self.config, 1000000, strategy="engine_path")
        self.assertEqual(len(holdings), 444)
        self.assertEqual([h.quantity for h in holdings], [r["implemented_contracts"] for r in
                         self.tables["baseline_holdings"] if r["fixed_aum"] == 1000000])
        for day in self.inputs.dates:
            validate_post_capacity_targets([{k: v for k, v in r.items() if k not in ("date", "fixed_aum", "pre_capacity_contracts", "implemented_contracts", "capacity_binding")}
                                            for r in targets if r["date"] == day])

    def test_publication_safe_inputs_and_independent_statistics(self):
        for r in self.tables["decision_inputs"]:
            self.assertLess(r["adv20_window_end"], r["decision_date"])
            self.assertEqual(len(r["adv20_observation_ids"]), 20)
            self.assertLessEqual(utc_ns(r["adv20_latest_publication"]), utc_ns(r["decision_cutoff"]))
            for kind in ("settlement", "cleared_volume", "open_interest"):
                self.assertLessEqual(utc_ns(r[kind + "_published_at"]), utc_ns(r["decision_cutoff"]))
        self.assertEqual({r["statistic"] for r in self.tables["timing_inputs"]}, {"cleared_volume", "open_interest"})
        self.assertNotIn("2026-04-03", self.inputs.calendar.dates)
        with self.assertRaises(ContractError):
            self.inputs.observations.latest_available("ZCZ6", "settlement", self.inputs.calendar.decision("2026-10-01"))

    def test_exact_endpoints_and_fixed_denominators(self):
        for r in self.tables["contract_returns"]:
            self.assertEqual(r["previous_session"], self.inputs.calendar.decision(r["trading_date"]).decision_date)
            self.assertEqual(r["settlement_change"], r["settlement"] - r["previous_settlement"])
        for r in self.tables["baseline_daily"]:
            self.assertEqual(r["benchmark_daily_return"], r["benchmark_pnl"] / Decimal(r["fixed_aum"]))
            self.assertEqual(r["baseline_daily_return"], r["baseline_pnl"] / Decimal(r["fixed_aum"]))

    def test_missing_final_endpoint_invalidates_all_masks_without_zero_fill(self):
        original = self.inputs.observations
        events = [o for o in original.observations if not (o.identity.contract == "ZCZ6"
                  and o.trading_date == "2026-09-30" and o.statistic_type == "settlement")]
        altered = replace(self.inputs, observations=CapturingStore(events, self.inputs.calendar))
        tables, metadata = build(altered, self.config)
        self.assertEqual(metadata["coverage"]["common_valid_dates"], 147)
        self.assertTrue(all(not r["common_valid_date"] for r in tables["common_masks"] if r["date"] == "2026-09-30"))
        self.assertTrue(all(r["baseline_pnl"] is None for r in tables["baseline_daily"] if r["date"] == "2026-09-30"))

    def test_tampered_input_hash_fails_and_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "inputs"; shutil.copytree(DATA / "inputs", copy)
            with (copy / "catalogue.json").open("a") as f:
                f.write(" ")
            with self.assertRaises(ContractError):
                load_inputs(copy, self.config)
            with self.assertRaises(ContractError):
                export(self.inputs, self.config, copy)

    def test_clean_copy_reproduces_every_output_hash_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp)
            for name in ("src", "scripts", "config"):
                shutil.copytree(ROOT / name, clean / name, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(DATA / "inputs", clean / "data/section0/inputs")
            # Imports and inputs are from the clean tree; no raw/personal directory.
            code = "import socket,runpy,sys; socket.create_connection=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('network disabled')); sys.argv=['scripts/run_section0.py','--reproduce','--output-dir','reproduced']; runpy.run_path(sys.argv[0],run_name='__main__')"
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=clean, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((clean / "reproduced/output_hashes.json").read_text()),
                             json.loads((DATA / "output_hashes.json").read_text()))


if __name__ == "__main__":
    unittest.main()
