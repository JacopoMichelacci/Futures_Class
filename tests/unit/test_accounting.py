"""Fixture-only Phase A accounting and common-mask acceptance tests."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import unittest

from src.data.asof import ObservationStore
from src.data.fixtures import load_fixture
from src.metrics.portfolio_pnl import daily_records, portfolio_pnl
from src.metrics.tracking_rmse_bps import tracking_rmse_bps
from src.portfolio.holdings import Holding, HoldingsBook
from src.validation.schemas import ContractError, HOLDINGS_COLUMNS, Identity, TABLE_COLUMNS

ROOT = Path(__file__).resolve().parents[2]


class AccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = load_fixture(ROOT / "data/fixtures/phase_a.json")

    def evaluate(self, book=None, store=None, aum=None, validity=None):
        f = self.f
        return portfolio_pnl(book or f.holdings, store or f.observations, f.raw["return_dates"],
                             f.raw["strategies"], f.raw["fixed_aum"] if aum is None else aum,
                             f.raw["snapshot_end_exclusive"], validity)

    def missing_settlement_store(self):
        missing = self.f.raw["perturbations"]["missing_settlement"]
        return ObservationStore([r for r in self.f.observations.observations
            if not (r.identity.contract == missing["contract"] and r.trading_date == missing["trading_date"] and r.statistic_type == "settlement")], self.f.calendar)

    def test_hand_calculated_exact_contract_pnl(self):
        daily = self.evaluate()
        self.assertEqual([r.pnl["benchmark"] for r in daily], [Decimal(x) for x in self.f.raw["expected"]["benchmark_pnl"]])
        self.assertEqual([r.pnl["half_size_fixture"] for r in daily], [Decimal(x) for x in self.f.raw["expected"]["half_pnl"]])

    def test_identical_holdings_give_zero_tracking_rmse(self):
        self.assertEqual(tracking_rmse_bps(self.evaluate())["fixture_baseline"], 0)

    def test_half_size_has_nonzero_daily_basis_point_rmse(self):
        score = tracking_rmse_bps(self.evaluate())["half_size_fixture"]
        self.assertEqual(score, Decimal("3.09375").sqrt())
        self.assertGreater(score, 0)

    def test_one_fixed_denominator_not_invested_notional_or_compounding(self):
        daily = self.evaluate()
        self.assertEqual(daily[0].returns["benchmark"], Decimal("0.0002"))
        self.assertEqual(daily[1].returns["benchmark"], Decimal("0.0005"))
        self.assertEqual(daily[1].returns["half_size_fixture"], Decimal("0.00025"))
        self.assertEqual({r.fixed_aum for r in daily}, {Decimal(1000000)})

    def test_missing_settlement_invalidates_both_adjacent_intervals_for_every_method(self):
        daily = self.evaluate(store=self.missing_settlement_store())
        self.assertEqual([r.common_valid for r in daily], [True, False, False, True])
        self.assertIsNone(daily[1].pnl)
        self.assertIsNone(daily[1].returns)
        self.assertIsNone(daily[2].returns)
        self.assertTrue(daily[1].invalid_reasons)

    def test_invalid_daily_records_are_null_not_zero(self):
        rows = daily_records(self.evaluate(store=self.missing_settlement_store()), self.f.raw["strategies"])
        invalid = [r for r in rows if not r["common_valid_date"]]
        self.assertEqual(len(invalid), 4)
        self.assertTrue(all(r["strategy_daily_return"] is None and r["squared_tracking_difference"] is None for r in invalid))
        self.assertEqual(set(rows[0]), set(TABLE_COLUMNS["strategy_daily"]))

    def test_reserved_daily_diagnostics_are_unknown_not_zero(self):
        for daily in (self.evaluate(), self.evaluate(store=self.missing_settlement_store())):
            records = daily_records(daily, self.f.raw["strategies"])
            self.assertTrue(all(r["implemented_notional_share"] is None and r["quality_flags"] is None for r in records))
            self.assertTrue(all(set(r) == set(TABLE_COLUMNS["strategy_daily"]) for r in records))

    def test_roll_does_not_splice_maturity_price_levels(self):
        day = self.f.raw["return_dates"][1]
        endpoints = self.f.observations.return_inputs({"synthetic": {"ZC_SYN_NEXT": 2}}, day, self.f.raw["snapshot_end_exclusive"])
        previous, current = endpoints["price_endpoints"]["ZC_SYN_NEXT"]
        self.assertEqual(previous.observation.identity, current.observation.identity)
        self.assertEqual(current.observation.statistic_value - previous.observation.statistic_value, 2)
        self.assertEqual(self.evaluate()[1].pnl["benchmark"], 500)

    def test_zero_holdings_need_no_price(self):
        additions = []
        for original in self.f.holdings.rows:
            if original.commodity == "ZC":
                additions.append(replace(original, contract="ZC_SYN_FAR",
                    benchmark_fractional_contracts=Decimal(0) if original.strategy == "benchmark" else None,
                    implemented_contracts=None if original.strategy == "benchmark" else Decimal(0)))
        book = HoldingsBook(self.f.holdings.rows + tuple(additions), self.f.calendar, self.f.catalogue)
        store = ObservationStore([r for r in self.f.observations.observations if not (r.identity.contract == "ZC_SYN_FAR" and r.statistic_type == "settlement")], self.f.calendar)
        self.assertTrue(all(r.common_valid for r in self.evaluate(book=book, store=store)))

    def test_missing_snapshot_is_not_assumed_flat(self):
        date = self.f.raw["return_dates"][0]
        book = HoldingsBook([r for r in self.f.holdings.rows if not (r.date == date and r.strategy == "fixture_baseline")], self.f.calendar, self.f.catalogue)
        self.assertFalse(self.evaluate(book=book)[0].common_valid)

    def test_missing_decision_inputs_invalidate_shared_date(self):
        valid = {(d, s): True for d in self.f.raw["return_dates"] for s in self.f.raw["strategies"]}
        del valid[(self.f.raw["return_dates"][0], "half_size_fixture")]
        self.assertFalse(self.evaluate(validity=valid)[0].common_valid)

    def test_holdings_cannot_be_formed_from_future_inputs(self):
        row = self.f.holdings.rows[0]
        with self.assertRaises(ContractError):
            replace(row, inputs_as_of=row.date + "T23:00:00Z")
        with self.assertRaises(ContractError):
            replace(row, formed_at=row.date + "T23:00:00Z")

    def test_holding_cutoff_must_match_explicit_calendar(self):
        rows = list(self.f.holdings.rows)
        rows[0] = replace(rows[0], decision_cutoff=rows[0].date + "T18:15:00Z")
        with self.assertRaises(ContractError):
            HoldingsBook(rows, self.f.calendar, self.f.catalogue)

    def test_implemented_quantities_integer_benchmark_fractional(self):
        candidate = next(r for r in self.f.holdings.rows if r.strategy == "fixture_baseline")
        with self.assertRaises(ContractError):
            replace(candidate, implemented_contracts="0.5")
        benchmark = next(r for r in self.f.holdings.rows if r.strategy == "benchmark")
        self.assertEqual(replace(benchmark, benchmark_fractional_contracts="0.5").quantity, Decimal("0.5"))

    def test_duplicate_and_mislabeled_holdings_rejected(self):
        with self.assertRaises(ContractError):
            HoldingsBook(self.f.holdings.rows + (self.f.holdings.rows[0],), self.f.calendar, self.f.catalogue)
        with self.assertRaises(ContractError):
            HoldingsBook([replace(self.f.holdings.rows[0], commodity="ZW")], self.f.calendar, self.f.catalogue)

    def test_supplied_capacity_ceiling_validated_without_capacity_algorithm(self):
        row = next(r for r in self.f.holdings.rows if r.strategy == "fixture_baseline")
        for cap in ("1", "2.5", "-1"):
            with self.assertRaises(ContractError):
                HoldingsBook([replace(row, details={"capacity_limit_contracts": cap})], self.f.calendar, self.f.catalogue)

    def test_invalid_aum_rejected(self):
        for value in (0, -1, "NaN", "Infinity"):
            with self.assertRaises(ContractError):
                self.evaluate(aum=value)

    def test_metric_rejects_changed_aum_and_mismatched_method_dates(self):
        daily = list(self.evaluate())
        with self.assertRaises(ContractError):
            tracking_rmse_bps([daily[0], replace(daily[1], fixed_aum=Decimal(2))])
        with self.assertRaises(ContractError):
            tracking_rmse_bps([daily[0], replace(daily[1], returns={"benchmark": Decimal(0)})])

    def test_empty_common_sample_is_unavailable_not_zero(self):
        store = ObservationStore([r for r in self.f.observations.observations if r.statistic_type != "settlement"], self.f.calendar)
        with self.assertRaises(ContractError):
            tracking_rmse_bps(self.evaluate(store=store))

    def test_canonical_long_form_columns(self):
        self.assertEqual(set(self.f.holdings.to_records()[0]), set(HOLDINGS_COLUMNS))

    def test_benchmark_reference_field_does_not_replace_implementation_quantity(self):
        rows = [replace(r, benchmark_fractional_contracts="50") if r.strategy == "fixture_baseline" else r for r in self.f.holdings.rows]
        book = HoldingsBook(rows, self.f.calendar, self.f.catalogue)
        self.assertEqual(tracking_rmse_bps(self.evaluate(book=book))["fixture_baseline"], 0)

    def test_shared_holdings_snapshots_cannot_be_mutated(self):
        snapshot = self.f.holdings.snapshot(self.f.raw["return_dates"][0], "benchmark")
        with self.assertRaises(TypeError):
            snapshot["ZC_SYN_NEAR"] = Decimal(999)


if __name__ == "__main__":
    unittest.main()
