"""Synthetic, sample-independent tests of promoted as-of and session conventions."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import unittest

from src.data.asof import ObservationStore
from src.data.calendar import SessionCalendar, TradingSession
from src.data.fixtures import load_fixture
from src.validation.schemas import ContractError, utc_ns

ROOT = Path(__file__).resolve().parents[2]


class AsOfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = load_fixture(ROOT / "data/fixtures/phase_a.json")
        cls.context = cls.f.calendar.decision(cls.f.raw["return_dates"][0])

    def test_latest_volume_before_cutoff_not_later_revision(self):
        selected = self.f.observations.latest_available("ZC_SYN_NEAR", "cleared_volume", self.context).observation
        self.assertEqual(selected.statistic_value, 1190)
        self.assertTrue(selected.view(self.context)["available_as_of"])
        late = next(r for r in self.f.observations.observations if r.statistic_value == 999999)
        self.assertFalse(late.view(self.context)["available_as_of"])

    def test_adv20_exactly_twenty_prior_sessions_and_holiday_excluded(self):
        result = self.f.observations.adv20("ZC_SYN_NEAR", self.context)
        self.assertEqual(result["observations"], 20)
        self.assertEqual(Decimal(result["adv20_contracts"]), Decimal("1095"))
        self.assertEqual([r["session_index"] for r in result["rows"]], list(range(20)))
        self.assertNotIn(self.f.raw["synthetic_non_session"], [r["trading_date"] for r in result["rows"]])

    def test_missing_interior_adv_member_not_skipped(self):
        missing = self.f.raw["perturbations"]["missing_volume"]
        store = ObservationStore([r for r in self.f.observations.observations if not (r.identity.contract == missing["contract"] and r.trading_date == missing["trading_date"] and r.statistic_type == "cleared_volume")], self.f.calendar)
        self.assertFalse(store.adv20("ZC_SYN_NEAR", self.context)["available"])

    def test_adv20_excludes_decision_session_even_if_already_published(self):
        row = self.f.observations.latest_available("ZC_SYN_NEAR", "cleared_volume", self.context).observation
        same_day = replace(row, trading_date=self.context.decision_date,
                           session_index=self.context.decision_index, statistic_value=Decimal(999999),
                           publication_timestamp=self.context.decision_date + "T10:00:00Z",
                           venue_event_timestamp=self.context.decision_date + "T10:00:00Z")
        store = ObservationStore([*self.f.observations.observations, same_day], self.f.calendar)
        self.assertTrue(same_day.view(self.context)["available_as_of"])
        self.assertEqual(store.latest_available("ZC_SYN_NEAR", "cleared_volume", self.context).observation, same_day)
        result = store.adv20("ZC_SYN_NEAR", self.context)
        self.assertEqual(result, self.f.observations.adv20("ZC_SYN_NEAR", self.context))
        self.assertEqual(len(result["rows"]), 20)
        self.assertTrue(all(r["session_index"] < self.context.decision_index for r in result["rows"]))

    def test_adv20_lag_does_not_relax_freshness_or_publication_rules(self):
        prior_day = self.f.calendar.dates[self.context.decision_index - 1]
        rows = [replace(r, publication_timestamp=self.context.decision_date + "T19:00:00Z",
                        venue_event_timestamp=self.context.decision_date + "T19:00:00Z")
                if r.identity.contract == "ZC_SYN_NEAR" and r.statistic_type == "cleared_volume" and r.trading_date == prior_day
                else r for r in self.f.observations.observations]
        self.assertFalse(ObservationStore(rows, self.f.calendar, 1).adv20("ZC_SYN_NEAR", self.context)["available"])
        later = self.f.calendar.decision("2030-02-04")
        rows = [r for r in self.f.observations.observations if not (r.identity.contract == "ZC_SYN_NEAR" and r.statistic_type == "cleared_volume" and r.trading_date == "2030-01-31")]
        self.assertFalse(ObservationStore(rows, self.f.calendar, 1).adv20("ZC_SYN_NEAR", later)["available"])
        relaxed = ObservationStore(rows, self.f.calendar, 2).adv20("ZC_SYN_NEAR", later)
        self.assertTrue(relaxed["available"])
        self.assertEqual(relaxed["endpoint_lag_from_decision_sessions"], 2)
        self.assertEqual(len(relaxed["rows"]), 20)
        self.assertFalse(ObservationStore(rows, self.f.calendar, 0).adv20("ZC_SYN_NEAR", later)["available"])

    def test_no_insufficient_or_unbounded_stale_history_fallback(self):
        self.assertFalse(self.f.observations.adv20("ZC_SYN_NEAR", self.f.calendar.decision(self.f.calendar.dates[5]))["available"])
        self.assertIsNone(self.f.observations.latest_available("ZC_SYN_NEAR", "cleared_volume", self.f.calendar.decision(self.f.calendar.dates[50])).observation)

    def test_session_cutoffs_are_supplied_not_universal(self):
        normal = self.f.calendar.decision(self.f.raw["return_dates"][0])
        shortened = self.f.calendar.decision(self.f.raw["return_dates"][-1])
        self.assertTrue(normal.cutoff_utc.endswith("18:15:00Z"))
        self.assertTrue(shortened.cutoff_utc.endswith("17:10:00Z"))
        records = list(self.f.calendar.sessions.values())
        records[0] = replace(records[0], decision_cutoff=records[0].trading_date + "T17:00:00Z")
        self.assertNotEqual(SessionCalendar(records).calendar_id, self.f.calendar.calendar_id)

    def test_cutoff_after_settlement_is_rejected(self):
        with self.assertRaises(ContractError):
            TradingSession("2030-01-02", "2030-01-02T17:00:00Z", "2030-01-02T18:00:00Z")

    def test_publication_boundary_preserves_nanoseconds(self):
        row = self.f.observations.latest_available("ZC_SYN_NEAR", "cleared_volume", self.context).observation
        at = replace(row, publication_timestamp=self.context.cutoff_utc)
        after = replace(row, publication_timestamp=self.context.cutoff_utc[:-1] + ".000000001Z")
        self.assertTrue(at.view(self.context)["available_as_of"])
        self.assertFalse(after.view(self.context)["available_as_of"])

    def test_decision_context_cannot_be_shifted(self):
        with self.assertRaises(ContractError):
            self.f.observations.adv20("ZC_SYN_NEAR", replace(self.context, cutoff_utc=self.context.return_date + "T23:00:00Z"))

    def test_final_flags_preserved_and_preliminary_not_promoted(self):
        final = next(r for r in self.f.observations.observations if r.statistic_type == "settlement")
        self.assertTrue(replace(final, stat_flags=7).selectable)
        self.assertTrue(replace(final, stat_flags=1).selectable)
        self.assertEqual(replace(final, stat_flags=1).quality, "theoretical")
        self.assertFalse(replace(final, stat_flags=2).selectable)
        self.assertFalse(replace(final, stat_flags=11).selectable)

    def test_deleted_and_conflicting_revisions_fail_safely(self):
        row = self.f.observations.latest_available("ZC_SYN_NEAR", "cleared_volume", self.context).observation
        deletion = replace(row, publication_timestamp=self.context.cutoff_utc, venue_event_timestamp=self.context.cutoff_utc, update_action=2, statistic_value=None)
        store = ObservationStore([row, deletion], self.f.calendar)
        self.assertIsNone(store.select_version(row.identity.contract, row.trading_date, row.statistic_type, self.context.cutoff_ns).observation)
        conflict = replace(row, statistic_value=row.statistic_value + 1)
        with self.assertRaises(ContractError):
            ObservationStore([row, conflict], self.f.calendar).select_version(row.identity.contract, row.trading_date, row.statistic_type, self.context.cutoff_ns)

    def test_exact_identity_aliases_cannot_be_mixed(self):
        row = self.f.observations.observations[0]
        conflict = replace(row, identity=replace(row.identity, instrument_id=row.identity.instrument_id + 10000))
        with self.assertRaises(ContractError):
            ObservationStore([row, conflict], self.f.calendar)

    def test_source_date_alignment_and_input_order_independence(self):
        day, selection = self.f.observations.latest_common(["ZC_SYN_NEAR", "ZC_SYN_NEXT"], ["cleared_volume", "open_interest"], self.context)
        self.assertTrue(all(s.observation.trading_date == day for s in selection.values()))
        reversed_store = ObservationStore(list(reversed(self.f.observations.observations)), self.f.calendar)
        self.assertEqual(reversed_store.adv20("ZC_SYN_NEAR", self.context), self.f.observations.adv20("ZC_SYN_NEAR", self.context))


if __name__ == "__main__":
    unittest.main()
