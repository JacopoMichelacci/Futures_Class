"""Synthetic production boundaries; never loads a private cache or network source."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import unittest

from src.data.asof import ObservationStore
from src.data.calendar import SessionCalendar
from src.data.fixtures import load_fixture
from src.validation.production import (deserialize_observations, observation_availability,
    serialize_observations, validate_catalogue, validate_complete_curve,
    validate_experiment_targets, validate_post_capacity_targets)
from src.validation.schemas import ContractError, ROOTS

ROOT = Path(__file__).resolve().parents[2]


def catalogue_records(fixture):
    # Explicit synthetic provenance, not a claim about real CME notice dates.
    return [dict(r, commodity=r["root"], first_notice=r["anchor"],
                 first_notice_source_url="https://example.invalid/synthetic-calendar",
                 first_notice_retrieved_at="2029-12-01T10:00:00Z",
                 source_note="Synthetic standard-grain contract-value hand case",
                 unit_of_measure="BU", value_unit="cents_per_bushel",
                 unit_of_measure_qty="5000", derived_price_scale="0.01",
                 min_price_increment="0.25", tick_value="12.50",
                 min_price_increment_amount="1.0") for r in fixture.raw["contracts"]]


class ProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = load_fixture(ROOT / "data/fixtures/phase_a.json")
        cls.records = catalogue_records(cls.f)
        cls.cat = validate_catalogue(cls.records)
        cls.ids = {s: c.identity for s, c in cls.cat.contracts.items()}
        cls.day = "2030-02-01"
        cls.context = cls.f.calendar.decision(cls.day)

    def curve(self, day=None):
        day = day or self.context.decision_date
        return [dict(date=day, commodity=c.identity.root, contract=s, anchor=c.anchor,
                     days_to_anchor=self.f.calendar.indices[c.anchor] - self.f.calendar.indices[day],
                     eligible=c.activation <= day < c.anchor)
                for s, c in self.cat.contracts.items() if c.activation <= day <= c.expiration]

    def history(self, observations=None):
        return serialize_observations(self.f.observations.observations if observations is None else observations,
                                      self.ids, self.f.calendar)

    def test_catalogue_retains_provenance_and_hash_is_order_independent(self):
        row = self.cat.records["ZC_SYN_NEAR"]
        self.assertEqual(row["first_notice_source_url"], self.records[0]["first_notice_source_url"])
        self.assertEqual(row["first_notice_retrieved_at"], "2029-12-01T10:00:00Z")
        self.assertEqual(validate_catalogue(reversed(self.records)).catalogue_id, self.cat.catalogue_id)
        with self.assertRaises(TypeError):
            row["first_notice"] = "2030-01-01"

    def test_missing_fnd_never_uses_expiration_or_anchor(self):
        for missing in (None, ""):
            rows = [dict(r) for r in self.records]
            rows[0]["first_notice"] = missing
            with self.subTest(missing=missing), self.assertRaisesRegex(ContractError, "first_notice"):
                validate_catalogue(rows)

    def test_expiration_cannot_substitute_for_fnd(self):
        rows = [dict(r) for r in self.records]
        rows[0]["anchor"] = rows[0]["first_notice"] = rows[0]["expiration"]
        with self.assertRaisesRegex(ContractError, "not a substitute"):
            validate_catalogue(rows)

    def test_anchor_must_equal_explicit_fnd(self):
        rows = [dict(r) for r in self.records]
        rows[0]["anchor"] = "2030-02-07"
        with self.assertRaisesRegex(ContractError, "anchor"):
            validate_catalogue(rows)

    def test_provenance_and_retrieval_are_required(self):
        for field, value in (("first_notice_source_url", ""), ("anchor_source", ""),
                             ("source_note", ""), ("first_notice_retrieved_at", ""),
                             ("first_notice_retrieved_at", "2029-12-01T10:00:00")):
            rows = [dict(r) for r in self.records]; rows[0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_catalogue(rows)
        rows = [dict(r, first_notice_retrieved_at="2029-12-01") for r in self.records]
        self.assertEqual(len(validate_catalogue(rows).contracts), 9)

    def test_duplicate_or_wrong_catalogue_identity_fails(self):
        with self.assertRaises(ContractError):
            validate_catalogue([*self.records, self.records[0]])
        with self.assertRaises(ContractError):
            validate_catalogue([dict(self.records[0], dataset="OTHER")])

    def test_missing_active_contract_rejected_for_any_root(self):
        rows = self.curve()
        for root in ROOTS:
            removed = next(r["contract"] for r in rows if r["commodity"] == root)
            with self.subTest(root=root), self.assertRaisesRegex(ContractError, "missing_required_contract_rows"):
                validate_complete_curve([r for r in rows if r["contract"] != removed],
                                        [self.context.decision_date], self.cat, self.f.calendar)

    def test_present_post_fnd_ineligible_row_is_accepted(self):
        day = self.cat.contracts["ZC_SYN_NEAR"].anchor
        rows = self.curve(day)
        self.assertFalse(next(r for r in rows if r["contract"] == "ZC_SYN_NEAR")["eligible"])
        evidence = validate_complete_curve(rows, [day], self.cat, self.f.calendar)
        self.assertTrue(evidence["complete_relative_to_catalogue"])
        self.assertEqual(evidence["catalogue_id"], self.cat.catalogue_id)
        self.assertEqual(len(evidence["checks"]), 3)

    def test_missing_data_cannot_be_encoded_as_false_eligibility(self):
        rows = self.curve(); rows[0]["eligible"] = False
        with self.assertRaisesRegex(ContractError, "no missing-data override"):
            validate_complete_curve(rows, [self.context.decision_date], self.cat, self.f.calendar)

    def test_duplicate_rows_and_incorrect_distances_fail(self):
        rows = self.curve()
        for bad in ([*rows, rows[0]], [dict(rows[0], days_to_anchor=999), *rows[1:]]):
            with self.assertRaises(ContractError):
                validate_complete_curve(bad, [self.context.decision_date], self.cat, self.f.calendar)

    def test_curve_requires_each_date_and_each_root(self):
        with self.assertRaises(ContractError):
            validate_complete_curve(self.curve(), [self.context.decision_date, self.day], self.cat, self.f.calendar)
        with self.assertRaises(ContractError):
            validate_complete_curve(self.curve(), [self.context.decision_date],
                                    validate_catalogue(self.records[:3]), self.f.calendar)

    def test_missing_commodity_rejected_and_zero_preserved(self):
        rows = [dict(date=self.day, commodity=r, target_notional="0" if r == "ZW" else "100") for r in ROOTS]
        result = validate_experiment_targets(rows, [self.day])
        self.assertEqual(result[-1]["target_notional"], Decimal(0))
        for root in ROOTS:
            with self.subTest(root=root), self.assertRaisesRegex(ContractError, "Missing required commodity"):
                validate_experiment_targets([r for r in rows if r["commodity"] != root], [self.day])
        self.assertEqual(rows[-1]["target_notional"], "0")

    def test_duplicate_negative_or_null_experiment_targets_fail(self):
        rows = [dict(date=self.day, commodity=r, target_notional="100") for r in ROOTS]
        for bad in ([*rows, rows[0]], [dict(rows[0], target_notional=None), *rows[1:]],
                    [dict(rows[0], target_notional=-1), *rows[1:]]):
            with self.assertRaises(ContractError):
                validate_experiment_targets(bad, [self.day])

    def test_all_revision_fields_round_trip_without_collapsing_history(self):
        original = self.f.observations.observations
        restored = deserialize_observations(self.history(), self.ids, self.f.calendar)
        self.assertEqual(restored, original)
        self.assertEqual([o.observation_id for o in restored], [o.observation_id for o in original])

    def test_later_revision_cannot_enter_earlier_decision_after_round_trip(self):
        restored = ObservationStore(deserialize_observations(self.history(), self.ids, self.f.calendar), self.f.calendar)
        self.assertEqual(restored.latest_available("ZC_SYN_NEAR", "cleared_volume", self.context).observation.statistic_value, 1190)
        late = next(o for o in restored.observations if o.statistic_value == 999999)
        self.assertFalse(late.view(self.context)["available_as_of"])
        self.assertEqual(restored.adv20("ZC_SYN_NEAR", self.context), self.f.observations.adv20("ZC_SYN_NEAR", self.context))

    def test_deletions_invalid_values_and_non_sessions_survive(self):
        row = self.f.observations.observations[0]
        rows = [replace(row, update_action=2, statistic_value=None),
                replace(row, statistic_value=None, invalid_reason="missing_or_invalid_statistic_value"),
                replace(row, trading_date="2030-01-21", session_index=None)]
        self.assertEqual(deserialize_observations(self.history(rows), self.ids, self.f.calendar), tuple(rows))

    def test_wide_snapshot_and_decision_view_are_not_source_history(self):
        view = self.f.observations.observations[0].view(self.context)
        for fake in ([dict(trading_date=self.day, settlement="400", cleared_volume=100)], [view]):
            with self.assertRaises(ContractError):
                deserialize_observations(json.dumps(fake), self.ids, self.f.calendar)
            envelope = json.loads(self.history()); envelope["observations"] = fake
            with self.assertRaises(ContractError):
                deserialize_observations(json.dumps(envelope), self.ids, self.f.calendar)

    def test_changed_calendar_identity_or_record_hash_fails(self):
        payload = json.loads(self.history())
        for field, value in (("sequence", 9999), ("instrument_id", 9999), ("statistic_value", 2.5)):
            bad = json.loads(json.dumps(payload)); bad["observations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                deserialize_observations(json.dumps(bad), self.ids, self.f.calendar)
        records = list(self.f.calendar.sessions.values())
        records[0] = replace(records[0], decision_cutoff=records[0].trading_date + "T17:00:00Z")
        with self.assertRaises(ContractError):
            deserialize_observations(self.history(), self.ids, SessionCalendar(records))

    def test_missing_and_not_yet_published_are_distinct(self):
        original = self.f.observations.latest_available("ZC_SYN_NEAR", "cleared_volume", self.context).observation
        late = replace(original, publication_timestamp=self.day + "T10:00:00Z",
                       venue_event_timestamp=self.day + "T10:00:00Z")
        absent = observation_availability(ObservationStore([], self.f.calendar), "ZC_SYN_NEAR",
                                         original.trading_date, "cleared_volume", self.context)
        future = observation_availability(ObservationStore([late], self.f.calendar), "ZC_SYN_NEAR",
                                         original.trading_date, "cleared_volume", self.context)
        self.assertEqual(absent.reason, "missing_observation")
        self.assertEqual(future.reason, "unavailable_by_decision_cutoff")
        self.assertIsNone(absent.observation)
        self.assertIsNone(future.observation)

    def test_expected_capacity_handoff_accepts_supplied_metadata_and_zero(self):
        rows = [dict(r, maturity_rank=1) for r in self.f.raw["post_capacity_targets"]]
        rows[0]["post_capacity_fractional_contracts"] = "0"
        result = validate_post_capacity_targets(rows)
        self.assertEqual(result[0]["post_capacity_fractional_contracts"], 0)
        self.assertEqual(len(validate_post_capacity_targets(rows[:1])), 1)

    def test_capacity_handoff_rejects_null_metadata_or_negative_quantity(self):
        row = dict(self.f.raw["post_capacity_targets"][0], maturity_rank=1)
        for key, value in (("target_weight", None), ("target_notional", None),
                           ("capacity_limit_contracts", None), ("maturity_rank", None),
                           ("post_capacity_fractional_contracts", "-1E-27"),
                           ("post_capacity_fractional_contracts", "99999")):
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_post_capacity_targets([dict(row, **{key: value})])

    def test_undefined_settlement_sentinels_rejected_on_both_boundaries(self):
        original = next(o for o in self.f.observations.observations if o.statistic_type == "settlement")
        for sentinel in ("9223372036.854775807", "9223372036854775807"):
            bad = replace(original, statistic_value=Decimal(sentinel), invalid_reason=None)
            with self.subTest(sentinel=sentinel), self.assertRaisesRegex(ContractError, "Undefined statistic"):
                self.history([bad])
            envelope = json.loads(self.history([original]))
            envelope["observations"][0].update(statistic_value=sentinel, observation_id=bad.observation_id)
            with self.subTest(reload=sentinel), self.assertRaisesRegex(ContractError, "Undefined statistic"):
                deserialize_observations(json.dumps(envelope), self.ids, self.f.calendar)

    def test_undefined_volume_and_oi_sentinels_rejected(self):
        for kind in ("cleared_volume", "open_interest"):
            original = next(o for o in self.f.observations.observations if o.statistic_type == kind)
            bad = replace(original, statistic_value=Decimal("9223372036854775807"), invalid_reason=None)
            with self.subTest(kind=kind), self.assertRaisesRegex(ContractError, "Undefined statistic"):
                self.history([bad])
            envelope = json.loads(self.history([original]))
            envelope["observations"][0].update(statistic_value=str(bad.statistic_value),
                                                observation_id=bad.observation_id)
            with self.subTest(reload=kind), self.assertRaisesRegex(ContractError, "Undefined statistic"):
                deserialize_observations(json.dumps(envelope), self.ids, self.f.calendar)

    def test_normalized_missing_values_remain_null_and_unselectable(self):
        for kind in ("settlement", "cleared_volume", "open_interest"):
            original = next(o for o in self.f.observations.observations if o.statistic_type == kind)
            missing = replace(original, statistic_value=None, invalid_reason="missing_or_invalid_statistic_value")
            (restored,) = deserialize_observations(self.history([missing]), self.ids, self.f.calendar)
            self.assertIsNone(restored.statistic_value)
            self.assertEqual(restored.invalid_reason, missing.invalid_reason)
            self.assertFalse(restored.selectable)

    def test_duplicate_envelope_json_member_is_rejected(self):
        payload = self.history()
        for duplicate in ('"observations": []', '"project_spec_version": "8.0"'):
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ContractError, "Duplicate JSON member"):
                deserialize_observations(payload[:-1] + "," + duplicate + "}", self.ids, self.f.calendar)

    def test_duplicate_observation_json_member_is_rejected(self):
        envelope = json.loads(self.history([self.f.observations.observations[0]]))
        event = json.dumps(envelope["observations"][0])
        for duplicate in ('"sequence": 999', '"statistic_value": "0"'):
            payload = json.dumps(envelope).replace(event, event[:-1] + "," + duplicate + "}", 1)
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ContractError, "Duplicate JSON member"):
                deserialize_observations(payload, self.ids, self.f.calendar)

    def test_malformed_rows_raise_contract_error(self):
        cases = [lambda: validate_catalogue([None]),
                 lambda: validate_complete_curve([None], [self.context.decision_date], self.cat, self.f.calendar),
                 lambda: validate_experiment_targets([None], [self.day]),
                 lambda: validate_experiment_targets([dict(date=self.day, commodity=[], target_notional="1")], [self.day]),
                 lambda: validate_post_capacity_targets([None]),
                 lambda: serialize_observations([None], self.ids, self.f.calendar)]
        for index, call in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ContractError):
                call()

    def test_malformed_collections_and_date_keys_raise_contract_error(self):
        for bad in (None, 42, "not a record collection", {}):
            for call in (lambda: validate_catalogue(bad),
                         lambda: validate_complete_curve(bad, [self.context.decision_date], self.cat, self.f.calendar),
                         lambda: validate_experiment_targets(bad, [self.day]),
                         lambda: validate_post_capacity_targets(bad)):
                with self.subTest(bad=bad), self.assertRaises(ContractError):
                    call()
        for bad in (None, [[]], [None]):
            with self.subTest(dates=bad), self.assertRaises(ContractError):
                validate_complete_curve(self.curve(), bad, self.cat, self.f.calendar)

    def test_nested_or_arbitrary_catalogue_evidence_is_rejected(self):
        for value in ({"revision": "mutable"}, ["mutable"], {"mutable"}, object(), float("nan")):
            rows = [dict(r) for r in self.records]
            rows[0]["source_evidence"] = value
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(ContractError, "flat immutable"):
                validate_catalogue(rows)

    def test_catalogue_evidence_is_detached_from_original_and_hash_stable(self):
        rows = [dict(r) for r in self.records]
        cat = validate_catalogue(rows)
        symbol = rows[0]["contract"]
        old_note, old_hash = cat.records[symbol]["source_note"], cat.catalogue_id
        rows[0]["source_note"] = "Changed in caller after validation"
        self.assertEqual(cat.records[symbol]["source_note"], old_note)
        self.assertEqual(cat.catalogue_id, old_hash)
        self.assertNotEqual(validate_catalogue(rows).catalogue_id, old_hash)
        with self.assertRaises(TypeError):
            cat.records[symbol]["source_note"] = "Cannot mutate retained evidence"

    def test_fnd_source_url_requires_scheme_and_host(self):
        for url in ("https://", "example.invalid/calendar", "   ", "https:///calendar", "https://bad host/path"):
            rows = [dict(r) for r in self.records]; rows[0]["first_notice_source_url"] = url
            with self.subTest(url=url), self.assertRaises(ContractError):
                validate_catalogue(rows)
        for url in ("https://example.invalid/calendar?id=1", "http://example.invalid/calendar"):
            rows = [dict(r) for r in self.records]; rows[0]["first_notice_source_url"] = url
            self.assertEqual(validate_catalogue(rows).records[rows[0]["contract"]]["first_notice_source_url"], url)

    def test_production_target_amounts_reject_binary_floats(self):
        rows = [dict(date=self.day, commodity=r, target_notional="100") for r in ROOTS]
        rows[0]["target_notional"] = 0.1 + 0.2
        with self.assertRaisesRegex(ContractError, "floats"):
            validate_experiment_targets(rows, [self.day])
        row = dict(self.f.raw["post_capacity_targets"][0], maturity_rank=1,
                   post_capacity_fractional_contracts=0.1 + 0.2)
        with self.assertRaisesRegex(ContractError, "floats"):
            validate_post_capacity_targets([row])
        with self.assertRaisesRegex(ContractError, "floats"):
            self.history([replace(self.f.observations.observations[0], statistic_value=0.1 + 0.2)])

    def test_unknown_curve_contract_is_rejected(self):
        rows = self.curve()
        with self.assertRaisesRegex(ContractError, "outside the declared scope"):
            validate_complete_curve([*rows, dict(rows[0], contract="UNKNOWN")],
                                    [self.context.decision_date], self.cat, self.f.calendar)

    def test_catalogue_known_contract_outside_active_range_is_rejected(self):
        contract = self.cat.contracts["ZC_SYN_NEAR"]
        day = next(d for d in self.f.calendar.dates if d > contract.expiration)
        expired = dict(date=day, commodity="ZC", contract="ZC_SYN_NEAR", anchor=contract.anchor,
                       days_to_anchor=self.f.calendar.indices[contract.anchor] - self.f.calendar.indices[day],
                       eligible=False)
        with self.assertRaisesRegex(ContractError, "unexpected_contract_rows"):
            validate_complete_curve([*self.curve(day), expired], [day], self.cat, self.f.calendar)

    def test_malformed_and_unknown_curve_roots_are_rejected(self):
        rows = self.curve()
        for root in ([], None, "ES"):
            with self.subTest(root=root), self.assertRaises(ContractError):
                validate_complete_curve([dict(rows[0], commodity=root), *rows[1:]],
                                        [self.context.decision_date], self.cat, self.f.calendar)

    def test_present_row_strictly_after_fnd_is_distinct_from_missing(self):
        contract = self.cat.contracts["ZC_SYN_NEAR"]
        day = next(d for d in self.f.calendar.dates if contract.anchor < d < contract.expiration)
        rows = self.curve(day)
        self.assertFalse(next(r for r in rows if r["contract"] == "ZC_SYN_NEAR")["eligible"])
        validate_complete_curve(rows, [day], self.cat, self.f.calendar)
        with self.assertRaisesRegex(ContractError, "missing_required_contract_rows"):
            validate_complete_curve([r for r in rows if r["contract"] != "ZC_SYN_NEAR"],
                                    [day], self.cat, self.f.calendar)


if __name__ == "__main__":
    unittest.main()
