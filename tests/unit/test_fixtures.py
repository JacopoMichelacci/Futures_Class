"""Shared fixture/configuration and independent reproduction checks."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from src.data.fixtures import load_fixture
from src.portfolio.baseline import BASELINE
from src.validation.schemas import (ContractError, HOLDINGS_COLUMNS, OBSERVATION_COLUMNS,
                                    TABLE_COLUMNS, TABLE_SCHEMA_STATUS, load_project_config)

ROOT = Path(__file__).resolve().parents[2]


class FixtureTests(unittest.TestCase):
    def test_configuration_locks_version8_and_all_roots(self):
        config = load_project_config(ROOT / "config/project.yaml")
        self.assertEqual(config["project_spec_version"], "8.0")
        self.assertEqual(config["roots"], ["ZC", "ZS", "ZW"])
        self.assertEqual(config["tracking_min_common_dates"], 100)

    def test_observation_event_schema_is_distinct_from_processed_daily_table(self):
        f = load_fixture(ROOT / "data/fixtures/phase_a.json")
        context = f.calendar.decision(f.raw["return_dates"][0])
        row = f.observations.observations[0].view(context)
        self.assertEqual(set(row), set(OBSERVATION_COLUMNS))
        self.assertEqual(TABLE_COLUMNS["statistic_observations"], OBSERVATION_COLUMNS)
        self.assertTrue({"settlement", "cleared_volume", "open_interest", "eligible"}.issubset(TABLE_COLUMNS["daily_contract_data"]))
        self.assertNotIn("statistic_type", TABLE_COLUMNS["daily_contract_data"])
        self.assertEqual(f.raw["fixture_format"], "section0_phase_a_cases_v1")
        self.assertNotIn("daily_contract_data", f.raw)

    def test_phase_b_metadata_and_benchmark_names_are_reserved_not_fabricated(self):
        required_metadata = {"commodity", "root", "contract", "maturity_month", "maturity_year", "first_notice",
                             "min_price_increment", "min_price_increment_amount", "display_factor", "main_fraction",
                             "sub_fraction", "unit_of_measure", "unit_of_measure_qty", "derived_price_scale", "tick_value",
                             "source_note", "first_notice_source_url", "first_notice_retrieved_at"}
        self.assertTrue(required_metadata.issubset(TABLE_COLUMNS["contracts"]))
        self.assertEqual(TABLE_COLUMNS["benchmark_holdings"], ("date", "commodity", "contract", "target_weight", "interpolation_weight", "target_notional", "fractional_contracts"))
        self.assertIn("benchmark_fractional_contracts", HOLDINGS_COLUMNS)
        self.assertNotIn("fractional_contracts", HOLDINGS_COLUMNS)
        for name in ("contracts", "daily_contract_data", "benchmark_holdings", "contract_returns"):
            self.assertEqual(TABLE_SCHEMA_STATUS[name], "phase_b_deferred_declaration")
        self.assertEqual(set(TABLE_COLUMNS), set(TABLE_SCHEMA_STATUS))

    def test_legacy_contract_return_name_is_not_redefined_as_price_change(self):
        columns = TABLE_COLUMNS["contract_returns"]
        self.assertIn("contract_return", columns)
        self.assertNotIn("settlement_change", columns)
        self.assertTrue({"trading_date", "commodity", "contract", "tracking_price_eligible", "quality_flags"}.issubset(columns))

    def test_baseline_interface_matches_single_locked_project_configuration(self):
        config = load_project_config(ROOT / "config/project.yaml")
        self.assertEqual(BASELINE.roll_offset_sessions, config["baseline_roll_offset_trading_days"])
        self.assertEqual(BASELINE.capacity_participation_rate, config["capacity_participation_rate"])
        self.assertEqual(BASELINE.liquidation_horizon_days, config["capacity_liquidation_horizon_days"])
        self.assertEqual(BASELINE.adv_lookback_sessions, config["adv_lookback_days"])

    def test_conflicting_locked_configuration_rejected(self):
        config = json.loads((ROOT / "config/project.yaml").read_text())
        config["capacity_participation_rate"] = 0.05
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.yaml"
            path.write_text(json.dumps(config))
            with self.assertRaises(ContractError):
                load_project_config(path)

    def test_timing_policy_values_types_and_presence_are_locked(self):
        original = json.loads((ROOT / "config/project.yaml").read_text())
        bad_values = {
            "max_liquidity_source_age_sessions": [None, True, 1.0, "1", 0, -1, 2],
            "decision_cutoff_policy": [None, True, "universal_1315", ""],
            "real_data_timing_status": [None, True, "verified", ""],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.yaml"
            for key, values in bad_values.items():
                for value in values:
                    with self.subTest(key=key, value=value):
                        path.write_text(json.dumps(dict(original, **{key: value})))
                        with self.assertRaises(ContractError):
                            load_project_config(path)
                missing = dict(original)
                del missing[key]
                path.write_text(json.dumps(missing))
                with self.subTest(missing=key), self.assertRaises(ContractError):
                    load_project_config(path)

    def test_fixture_contains_only_explicit_synthetic_provenance(self):
        f = load_fixture(ROOT / "data/fixtures/phase_a.json")
        self.assertTrue(f.raw["provenance"].startswith("Entirely synthetic"))
        self.assertTrue(all("SYN" in c for c in f.catalogue))
        self.assertTrue(all(c.anchor_source.startswith("synthetic") for c in f.catalogue.values()))

    def test_shared_code_has_no_pilot_cache_or_network_dependency(self):
        for path in (ROOT / "src").rglob("*.py"):
            content = path.read_text()
            for forbidden in ("local_section0_pilot", "load_september", "import databento", "urllib", "import requests"):
                self.assertNotIn(forbidden, content, str(path))

    def test_all_deferred_fixture_contracts_have_accounting_endpoints(self):
        f = load_fixture(ROOT / "data/fixtures/phase_a.json")
        for day in f.raw["return_dates"]:
            result = f.observations.return_inputs({"synthetic": {s: 1 for s in f.catalogue}}, day, f.raw["snapshot_end_exclusive"])
            self.assertTrue(result["common_valid"])
        self.assertEqual(len(f.raw["post_capacity_targets"]), 3)

    def test_demo_requires_explicit_fixture_mode(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run_section0.py")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Real-data Section 0 is Phase B", result.stderr)

    def test_clean_copy_demo_needs_no_pilot_or_installed_project_package(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ("src", "config", "data/fixtures", "scripts"):
                shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__"))
            result = subprocess.run([sys.executable, "-I", "-B", str(target / "scripts/run_section0.py"), "--fixtures"], cwd=target, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["common_valid_dates"], 4)
            self.assertEqual(output["tracking_rmse_bps"]["fixture_baseline"], "0")
            self.assertGreater(float(output["tracking_rmse_bps"]["half_size_fixture"]), 0)
            self.assertFalse(output["real_data_gate_run"])
            self.assertEqual(output["baseline_roll_events"], [{"date": "2030-02-01", "commodity": "ZC",
                             "outgoing_contract": "ZC_SYN_NEAR", "baseline_destination": "ZC_SYN_NEXT", "anchor": "2030-02-08"}])
            self.assertEqual(len(output["baseline_contract_path"]), 8)


if __name__ == "__main__":
    unittest.main()
