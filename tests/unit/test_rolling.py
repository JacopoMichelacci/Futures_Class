"""Hand cases for the roll-timing engine (Issue #23); synthetic and offline."""
from datetime import date, timedelta
from pathlib import Path
import unittest

from src.contracts.eligibility import Contract
from src.data.asof import ObservationStore, normalise
from src.data.calendar import SessionCalendar, TradingSession
from src.data.fixtures import load_fixture
from src.portfolio.baseline import BASELINE, BaselinePath, build_baseline_path
from src.rolling.rules import (RULES, RollPair, apply_roll_timing_rule, build_baseline_roll_pairs,
                               build_roll_timing_portfolio, fixed_roll_date, lagged_oi_crossover,
                               lagged_volume_crossover)
from src.validation.schemas import ContractError, Identity

ROOT = Path(__file__).resolve().parents[2]
HOLIDAY = "2031-01-20"  # Explicit non-session weekday inside the roll window.


def _sessions(n=30):
    day, out = date(2031, 1, 2), []
    while len(out) < n:
        if day.weekday() < 5 and day.isoformat() != HOLIDAY:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


DATES = _sessions()
CALENDAR = SessionCalendar(TradingSession(d, f"{d}T18:15:00Z", f"{d}T18:15:00Z") for d in DATES)
ANCHOR = DATES[20]  # 2031-01-31
IDS = {"OUT": 1, "IN": 2}
CATALOGUE = {
    "OUT": Contract(Identity("GLBX.MDP3", 1, 1, "OUT", "ZC"), DATES[0], ANCHOR, DATES[22], "50", "synthetic"),
    "IN": Contract(Identity("GLBX.MDP3", 1, 2, "IN", "ZC"), DATES[0], "2031-06-02", "2031-06-04", "50", "synthetic"),
}
PAIR = RollPair("ZC", "OUT", "IN", ANCHOR, DATES[15])
MINUS_5, MINUS_10 = DATES[15], DATES[10]


def raw(contract, day, kind, value, published=None, sequence=1):
    """Statistic for trading date ``day`` (ts_ref), published by default before its cut-off."""
    published = published or f"{day}T17:00:00Z"
    return {"symbol": contract, "hd": {"publisher_id": 1, "instrument_id": IDS[contract], "ts_event": published},
            "ts_recv": published, "ts_ref": f"{day}T00:00:00Z",
            "stat_type": {"cleared_volume": 6, "open_interest": 9}[kind], "quantity": value,
            "stat_flags": 0, "update_action": 1, "sequence": sequence, "channel_id": 1}


def series(kind, values):
    """{trading_date: (outgoing, incoming)} -> raw records for both contracts."""
    return [raw(c, d, kind, v) for d, pair in values.items() for c, v in zip(("OUT", "IN"), pair)]


def store(raws):
    identities = {s: c.identity for s, c in CATALOGUE.items()}
    return ObservationStore([normalise(r, identities, CALENDAR) for r in raws], CALENDAR, 1)


def timing(rule, s=None, start=DATES[5], deadline=MINUS_5):
    return apply_roll_timing_rule(PAIR, rule, CALENDAR, CATALOGUE, s, start, deadline)


class FixedOffsetTests(unittest.TestCase):
    def test_offsets_count_observed_sessions_across_non_session_gap(self):
        self.assertNotIn(HOLIDAY, CALENDAR.indices)
        self.assertEqual(fixed_roll_date(PAIR, CALENDAR, CATALOGUE, -5), "2031-01-24")
        # Generic weekday counting would give 2031-01-17 because it counts the holiday.
        self.assertEqual(fixed_roll_date(PAIR, CALENDAR, CATALOGUE, -10), "2031-01-16")
        for rule, sessions in (("fixed_-5", 5), ("fixed_-10", 10)):
            out = timing(rule)
            self.assertEqual(out["sessions_before_anchor"], sessions)
            self.assertFalse(out["fallback_flag"])
        self.assertEqual(timing("fixed_-10")["first_incoming_interval_end"], "2031-01-17")

    def test_baseline_constant_is_unchanged_and_offset_validated(self):
        self.assertEqual(BASELINE.roll_offset_sessions, -5)
        for bad in (0, 3, -5.0):
            with self.assertRaises(ContractError):
                fixed_roll_date(PAIR, CALENDAR, CATALOGUE, bad)
        with self.assertRaises(ContractError):
            fixed_roll_date(PAIR, CALENDAR, CATALOGUE, -25)  # Before the explicit calendar.


class CrossoverTests(unittest.TestCase):
    def test_same_pair_for_every_rule(self):
        s = store(series("cleared_volume", {d: (100, 200) for d in DATES[5:16]})
                  + series("open_interest", {d: (100, 200) for d in DATES[5:16]}))
        for rule in RULES:
            out = timing(rule, s)
            self.assertEqual((out["commodity"], out["outgoing_contract"], out["incoming_contract"], out["anchor"]),
                             ("ZC", "OUT", "IN", ANCHOR))

    def test_incoming_must_strictly_exceed_outgoing(self):
        s = store(series("cleared_volume", {DATES[5]: (100, 100), DATES[6]: (100, 99), DATES[7]: (100, 101)}))
        out = lagged_volume_crossover(s, PAIR, CALENDAR, DATES[5], MINUS_5)
        self.assertEqual(out["crossover_date"], DATES[7])
        self.assertEqual([c["outcome"] for c in out["checked"][:3]],
                         ["incoming_not_greater", "incoming_not_greater", "crossover"])

    def test_trading_date_comes_from_ts_ref_not_publication(self):
        # Trading date DATES[6] published the next morning: unknown at the DATES[6] cut-off.
        day, next_day = DATES[6], DATES[7]
        s = store([raw("OUT", day, "cleared_volume", 100, f"{next_day}T10:00:00Z"),
                   raw("IN", day, "cleared_volume", 200, f"{next_day}T10:00:00Z")])
        out = lagged_volume_crossover(s, PAIR, CALENDAR, DATES[5], MINUS_5)
        self.assertEqual(out["crossover_date"], next_day)
        self.assertEqual(out["trigger"]["source_date"], day)

    def test_publication_at_cutoff_counts_after_cutoff_does_not(self):
        day = DATES[6]
        at = store([raw("OUT", day, "open_interest", 100, f"{day}T18:15:00Z"),
                    raw("IN", day, "open_interest", 200, f"{day}T18:15:00Z")])
        after = store([raw("OUT", day, "open_interest", 100, f"{day}T18:15:00.000000001Z"),
                       raw("IN", day, "open_interest", 200, f"{day}T18:15:00.000000001Z")])
        self.assertEqual(lagged_oi_crossover(at, PAIR, CALENDAR, day, MINUS_5)["crossover_date"], day)
        self.assertEqual(lagged_oi_crossover(after, PAIR, CALENDAR, day, MINUS_5)["crossover_date"], DATES[7])

    def test_later_revision_is_not_used_before_it_is_published(self):
        day = DATES[6]
        s = store([raw("OUT", day, "cleared_volume", 100),
                   raw("IN", day, "cleared_volume", 90),
                   raw("IN", day, "cleared_volume", 150, f"{day}T19:00:00Z", sequence=2)])
        out = lagged_volume_crossover(s, PAIR, CALENDAR, day, MINUS_5)
        self.assertEqual(out["checked"][0]["outcome"], "incoming_not_greater")
        self.assertEqual(out["crossover_date"], DATES[7])  # Revision visible by the next cut-off.
        self.assertEqual(out["trigger"]["incoming_value"], 150)

    def test_volume_and_oi_are_independent(self):
        volume_only = store(series("cleared_volume", {DATES[6]: (100, 200)}))
        self.assertEqual(timing("volume_crossover", volume_only)["roll_decision_date"], DATES[6])
        oi = timing("oi_crossover", volume_only)
        self.assertTrue(oi["fallback_flag"])  # Missing OI is not a signal.
        self.assertTrue(all(c["outcome"] == "no_aligned_publication_safe_pair" for c in oi["checked"]))

        oi_only = store(series("open_interest", {DATES[8]: (100, 200)}))
        self.assertEqual(timing("oi_crossover", oi_only)["roll_decision_date"], DATES[8])
        self.assertTrue(timing("volume_crossover", oi_only)["fallback_flag"])

    def test_one_missing_leg_is_not_a_crossover(self):
        s = store([raw("IN", DATES[6], "cleared_volume", 500)])
        self.assertIsNone(lagged_volume_crossover(s, PAIR, CALENDAR, DATES[5], MINUS_5)["crossover_date"])

    def test_first_qualifier_is_kept(self):
        values = {DATES[7]: (100, 200), DATES[9]: (100, 50), DATES[11]: (100, 300)}
        s = store(series("cleared_volume", values))
        out = lagged_volume_crossover(s, PAIR, CALENDAR, DATES[5], MINUS_5)
        self.assertEqual(out["crossover_date"], DATES[7])
        self.assertEqual(out["checked"][-1]["decision_date"], DATES[7])  # Search stopped there.
        more = store(series("cleared_volume", {**values, DATES[12]: (100, 900)}))
        self.assertEqual(lagged_volume_crossover(more, PAIR, CALENDAR, DATES[5], MINUS_5)["crossover_date"], DATES[7])

    def test_deadline_session_itself_is_not_searched(self):
        s = store(series("cleared_volume", {MINUS_5: (100, 200)}))
        self.assertIsNone(lagged_volume_crossover(s, PAIR, CALENDAR, DATES[5], MINUS_5)["crossover_date"])


class FallbackTests(unittest.TestCase):
    """Per the proposed causal boundary in Issue #23 -- pending team sign-off."""

    def test_no_crossover_falls_back_to_minus_five_and_flags(self):
        s = store(series("cleared_volume", {d: (200, 100) for d in DATES[5:20]}))
        out = timing("volume_crossover", s)
        self.assertEqual(out["roll_decision_date"], MINUS_5)
        self.assertTrue(out["fallback_flag"])
        self.assertIsNone(out["trigger"])
        # A qualifier after the deadline cannot influence the decision.
        self.assertTrue(all(c["decision_date"] < MINUS_5 for c in out["checked"]))

    def test_crossover_between_minus_five_and_anchor_is_ignored(self):
        # Qualifies two sessions after -5 but before the anchor: too late for a causal -5 fallback.
        s = store(series("cleared_volume", {d: (200, 100) for d in DATES[5:15]}
                         | {DATES[17]: (100, 200)}))
        out = timing("volume_crossover", s)
        self.assertEqual(out["roll_decision_date"], MINUS_5)
        self.assertTrue(out["fallback_flag"])
        self.assertNotIn(DATES[17], [c["decision_date"] for c in out["checked"]])

    def test_deadline_after_minus_five_is_rejected(self):
        with self.assertRaises(ContractError):
            timing("volume_crossover", store([]), deadline=DATES[16])

    def test_crossover_requires_explicit_window_and_store(self):
        for kwargs in ({"s": None}, {"s": store([]), "start": None}, {"s": store([]), "deadline": None}):
            with self.assertRaises(ContractError):
                timing("oi_crossover", **kwargs)
        with self.assertRaises(ContractError):
            timing("fixed_-7")


class PortfolioTests(unittest.TestCase):
    @staticmethod
    def baseline_path():
        rows = tuple({"date": DATES[i + 1], "decision_date": DATES[i],
                      "decision_cutoff": CALENDAR.sessions[DATES[i]].decision_cutoff, "commodity": "ZC",
                      "contract": "OUT" if i < 15 else "IN"} for i in range(5, 20))
        event = {"date": DATES[15], "commodity": "ZC", "outgoing_contract": "OUT",
                 "baseline_destination": "IN", "anchor": ANCHOR}
        return BaselinePath(rows, (event,))

    def test_pairs_come_from_baseline_events(self):
        self.assertEqual(build_baseline_roll_pairs(self.baseline_path()), (PAIR,))

    def test_switch_at_decision_and_no_preceding_interval_return(self):
        decision = timing("fixed_-10")
        rows = build_roll_timing_portfolio(self.baseline_path(), [decision], CALENDAR, CATALOGUE)
        first_in = next(r for r in rows if r["contract"] == "IN")
        self.assertEqual(first_in["decision_date"], MINUS_10)
        self.assertEqual(first_in["date"], decision["first_incoming_interval_end"])
        # Incoming is never held over an interval ending on or before its decision session.
        self.assertTrue(all(r["date"] > MINUS_10 for r in rows if r["contract"] == "IN"))
        self.assertTrue(all(r["contract"] == "OUT" for r in rows if r["decision_date"] < MINUS_10))
        self.assertEqual({r["timing_rule"] for r in rows}, {"fixed_-10"})

    def test_baseline_rule_reproduces_baseline_path(self):
        path = self.baseline_path()
        rows = build_roll_timing_portfolio(path, [timing("fixed_-5")], CALENDAR, CATALOGUE)
        self.assertEqual([r["contract"] for r in rows], [r["contract"] for r in path.positions])

    def test_no_position_on_or_after_anchor(self):
        late = {**timing("fixed_-5"), "roll_decision_date": DATES[20]}
        with self.assertRaises(ContractError):
            build_roll_timing_portfolio(self.baseline_path(), [late], CALENDAR, CATALOGUE)

    def test_changed_or_missing_pair_is_rejected(self):
        with self.assertRaises(ContractError):
            build_roll_timing_portfolio(self.baseline_path(), [{**timing("fixed_-5"), "incoming_contract": "X"}],
                                        CALENDAR, CATALOGUE)
        with self.assertRaises(ContractError):
            build_roll_timing_portfolio(self.baseline_path(), [], CALENDAR, CATALOGUE)


class SharedFixtureTests(unittest.TestCase):
    def test_phase_a_baseline_pair_under_all_rules(self):
        f = load_fixture(ROOT / "data/fixtures/phase_a.json")
        case = f.raw["baseline_path_case"]
        path = build_baseline_path(case["eligible_curve"], case["decision_dates"], case["commodities"],
                                   f.calendar, f.catalogue)
        (pair,) = build_baseline_roll_pairs(path)
        self.assertEqual((pair.outgoing_contract, pair.incoming_contract), ("ZC_SYN_NEAR", "ZC_SYN_NEXT"))
        self.assertEqual(fixed_roll_date(pair, f.calendar, f.catalogue, -5), pair.baseline_event_date)
        self.assertEqual(fixed_roll_date(pair, f.calendar, f.catalogue, -10), "2030-01-25")
        start = case["decision_dates"][0]
        for rule in ("volume_crossover", "oi_crossover"):
            out = apply_roll_timing_rule(pair, rule, f.calendar, f.catalogue, f.observations,
                                         start, pair.baseline_event_date)
            self.assertEqual((out["roll_decision_date"], out["trigger"]["source_date"]), ("2030-01-30", "2030-01-29"))
            rows = build_roll_timing_portfolio(path, [out], f.calendar, f.catalogue)
            self.assertTrue(all(r["contract"] == "ZC_SYN_NEXT" for r in rows))


if __name__ == "__main__":
    unittest.main()
