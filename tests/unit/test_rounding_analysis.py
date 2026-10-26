"""Frozen-input, accounting, selection and export tests for Issue #8."""
import csv
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.rounding.analysis import (analyse_rounding, load_rounding_experiment,
                                   require_frozen_dates, select_reference)
from src.rounding.exports import write_outputs, rounding_figure, chart_export_matches, register_chart_export
from src.rounding.rules import ROUNDING_METHODS
from src.section0.phase_b import CapturingStore
from src.validation.schemas import ContractError, load_project_config

ROOT = Path(__file__).resolve().parents[2]


class RoundingSelectionTests(unittest.TestCase):
    def test_baseline_wins_tie_strictly_below_point_one(self):
        r = select_reference(dict(nearest="1.099999", toward_zero="1", away_from_zero="2", residual_greedy="1.099999"))
        self.assertEqual(r["preferred_rule"], "nearest")
        self.assertTrue(r["tie_triggered"])

    def test_exact_point_one_is_not_a_tie(self):
        r = select_reference(dict(nearest="1.1", toward_zero="1", away_from_zero="2", residual_greedy="1.1"))
        self.assertEqual(r["preferred_rule"], "toward_zero")
        self.assertEqual(r["tied_rules"], ["toward_zero"])

    def test_unique_best_and_simpler_rule_over_greedy(self):
        r = select_reference(dict(nearest="2", toward_zero="1.04", away_from_zero="3", residual_greedy="1"))
        self.assertEqual(r["preferred_rule"], "toward_zero")
        r = select_reference(dict(nearest="2", toward_zero="3", away_from_zero="1", residual_greedy="2"))
        self.assertEqual(r["preferred_rule"], "away_from_zero")

    def test_unresolved_floor_ceil_tie_does_not_invent_order(self):
        r = select_reference(dict(nearest="2", toward_zero="1", away_from_zero="1.01", residual_greedy="1.02"))
        self.assertIsNone(r["preferred_rule"])
        self.assertEqual(r["reason"], "unresolved_floor_ceil_tie")

    def test_nonfinite_or_extra_metric_methods_rejected(self):
        for scores in (dict(nearest="NaN", toward_zero=1, away_from_zero=1, residual_greedy=1),
                       dict(nearest=1, toward_zero=1, away_from_zero=1, residual_greedy=1, optimiser=0)):
            with self.assertRaises(ContractError):select_reference(scores)


class RoundingAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_project_config(ROOT / "config/project.yaml")
        cls.experiment = load_rounding_experiment(ROOT / "data/section0", cls.config)
        cls.result = analyse_rounding(cls.experiment)

    def test_real_frozen_scope_and_reference_nonbinding(self):
        r = self.result
        self.assertEqual(r["data_kind"], "real_frozen_databento_section0")
        self.assertEqual(len(r["dates"]), 148)
        self.assertEqual(r["selection"]["reference_aum"], 1000000)
        self.assertEqual(r["core_aum_grid"], [1000000, 10000000, 100000000, 1000000000])
        self.assertTrue(all(x["capacity_binding_positions"] == 0 for x in r["summary"]))
        self.assertEqual(len(r["by_aum"]), 16)

    def test_all_rules_use_the_same_dates_and_benchmark_at_each_AUM(self):
        for aum in self.result["core_aum_grid"]:
            rows = [r for r in self.result["daily"] if r["fixed_aum"] == aum]
            for method in ROUNDING_METHODS:
                self.assertEqual([r["date"] for r in rows if r["rule"] == method], self.result["dates"])
            for date in self.result["dates"]:
                day = [r for r in rows if r["date"] == date]
                self.assertEqual(len({r["benchmark_daily_return"] for r in day}), 1)

    def test_exported_daily_differences_reconcile_to_shared_metric(self):
        for summary in self.result["by_aum"]:
            rows = [r for r in self.result["daily"] if r["fixed_aum"] == summary["fixed_aum"] and r["rule"] == summary["rule"]]
            rmse = (sum(r["daily_tracking_difference"] ** 2 for r in rows) / Decimal(148)).sqrt() * 10000
            self.assertEqual(rmse, summary["tracking_rmse_bps_daily"])

    def test_nearest_reproduces_baseline_and_every_holding_respects_ceiling(self):
        baseline = {(r["fixed_aum"],r["date"],r["contract"]):r for r in self.experiment.baseline}
        for r in self.result["holdings"]:
            b = baseline[(r["fixed_aum"],r["date"],r["contract"])]
            self.assertLessEqual(r["implemented_contracts"], Decimal(b["capacity_limit_contracts"]))
            if r["rule"] == "nearest":self.assertEqual(r["implemented_contracts"], Decimal(b["implemented_contracts"]))

    def test_selection_only_uses_reference_rows(self):
        scores = {r["rule"]:r["tracking_rmse_bps_daily"] for r in self.result["summary"]}
        expected = select_reference(scores)
        self.assertEqual(self.result["selection"]["preferred_rule"], expected["preferred_rule"])
        self.assertEqual(self.result["selection"]["tied_rules"], expected["tied_rules"])

    def test_mask_change_stops_even_if_100_date_gate_would_still_pass(self):
        dates = self.result["dates"]
        rows = [SimpleNamespace(date=d,common_valid=i != 0) for i,d in enumerate(dates)]
        with self.assertRaises(ContractError):require_frozen_dates(rows,dates,self.config)

    def test_missing_settlement_stops_before_metric_or_selection(self):
        x = self.experiment
        observations = [o for o in x.shared.observations.observations if not
                        (o.identity.contract == "ZCZ6" and o.trading_date == "2026-09-30" and o.statistic_type == "settlement")]
        shared = replace(x.shared, observations=CapturingStore(observations,x.shared.calendar))
        with patch('src.rounding.analysis.tracking_rmse_bps') as metric:
            with self.assertRaises(ContractError):analyse_rounding(replace(x,shared=shared))
            metric.assert_not_called()

    def test_modified_frozen_input_fails_hash_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'section0';shutil.copytree(ROOT/'data/section0',path)
            with (path/'freeze.json').open('a') as stream:stream.write(' ')
            with self.assertRaises(ContractError):load_rounding_experiment(path,self.config)

    def test_exported_reference_and_grid_tables_are_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);manifest=write_outputs(self.result,p,include_chart=False)
            with (p/'rounding_summary.csv').open() as stream:summary=list(csv.DictReader(stream))
            with (p/'rounding_by_aum.csv').open() as stream:grid=list(csv.DictReader(stream))
            self.assertEqual(len(summary),4);self.assertEqual(len(grid),16)
            self.assertTrue(all(r['fixed_aum']=='1000000' for r in summary))
            self.assertEqual(manifest['data_kind'],'real_frozen_databento_section0')
            self.assertEqual(json.loads((p/'analysis_manifest.json').read_text())['selection']['reference_aum'],1000000)

    def test_plot_uses_exact_grid_scores_and_reference_only_selection(self):
        figure = rounding_figure(self.result)
        for i, method in enumerate(ROUNDING_METHODS):
            rows = [r for r in self.result["by_aum"] if r["rule"] == method]
            self.assertEqual(list(figure.data[i].x), [r["fixed_aum"] for r in rows])
            self.assertEqual(list(figure.data[i].y), [float(r["tracking_rmse_bps_daily"]) for r in rows])
        self.assertEqual(list(figure.data[4].x), [1000000])
        self.assertEqual(len(figure.data), 10)

    def test_chart_receipt_detects_changed_figure_or_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_outputs(self.result, p)
            self.assertFalse(chart_export_matches(p))
            # This tests hash linkage, not PNG rendering or visual correctness.
            register_chart_export(p, b"\x89PNG\r\n\x1a\nsynthetic receipt test")
            self.assertTrue(chart_export_matches(p))
            figure = (p / "rounding_figure.json").read_text()
            (p / "rounding_figure.json").write_text(figure + " ")
            self.assertFalse(chart_export_matches(p))
            (p / "rounding_figure.json").write_text(figure)
            (p / "tracking_rmse_vs_aum.png").write_bytes(b"changed")
            self.assertFalse(chart_export_matches(p))


if __name__ == '__main__':unittest.main()
