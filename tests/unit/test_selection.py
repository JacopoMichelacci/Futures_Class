
"""Unit tests for Issue #12, using entirely synthetic observed sessions."""

import unittest
from datetime import date, timedelta

from src.contracts.eligibility import Contract
from src.contracts.maturity import days_to_anchor
from src.data.calendar import SessionCalendar, TradingSession
from src.portfolio.baseline import BaselinePath, build_baseline_path
from src.selection.rules import (
    deferred_candidates,
    select_next_deferred,
    select_second_deferred,
    select_target_days_deferred,
    build_destination_schedule,
    apply_destination_rule_to_baseline,
)
from src.validation.schemas import Identity, ContractError


def case():
    # Explicit observed sessions: weekends are excluded.
    days = [
        d.isoformat()
        for i in range(180)
        if (d := date(2026, 1, 1) + timedelta(days=i)).weekday() < 5
    ][:120]

    calendar = SessionCalendar(
        TradingSession(d, d + "T18:00:00Z", d + "T17:00:00Z")
        for d in days
    )

    anchors = {
        "ZCH26": 10,
        "ZCK26": 35,
        "ZCN26": 55,
        "ZCU26": 85,
    }

    catalogue = {
        s: Contract(
            Identity("GLBX.MDP3", 1, i + 1, s, "ZC"),
            days[0],
            days[n],
            days[n + 1],
            50,
            "synthetic",
        )
        for i, (s, n) in enumerate(anchors.items())
    }

    curve = [
        {
            "date": day,
            "commodity": "ZC",
            "contract": s,
            "anchor": c.anchor,
            "days_to_anchor": (
                calendar.indices[c.anchor] - calendar.indices[day]
            ),
            "eligible": c.activation <= day < c.anchor,
        }
        for day in days[1:4]
        for s, c in catalogue.items()
    ]

    event = {
        "date": days[1],
        "commodity": "ZC",
        "outgoing_contract": "ZCH26",
        "baseline_destination": "ZCK26",
        "anchor": catalogue["ZCH26"].anchor,
    }

    baseline = BaselinePath(
        tuple(
            {
                "date": days[i + 1],
                "decision_date": days[i],
                "commodity": "ZC",
                "contract": "ZCK26",
            }
            for i in (1, 2, 3)
        ),
        (event,),
    )

    return days, calendar, catalogue, curve, baseline


class TestSelection(unittest.TestCase):

    def test_candidates_deterministic_and_eligible(self):
        days, cal, cat, curve, _ = case()
        expected = ("ZCK26", "ZCN26", "ZCU26")

        self.assertEqual(
            deferred_candidates(curve, "ZCH26", days[1], cat),
            expected,
        )
        self.assertEqual(
            deferred_candidates(
                list(reversed(curve)), "ZCH26", days[1], cat
            ),
            expected,
        )
        self.assertEqual(select_next_deferred(expected), "ZCK26")
        self.assertEqual(
            select_second_deferred(expected),
            ("ZCN26", False),
        )
        self.assertEqual(
            select_second_deferred(("ZCK26",)),
            ("ZCK26", True),
        )
        self.assertEqual(
            select_second_deferred(()),
            (None, False),
        )

    def test_targets_and_ties(self):
        days, cal, cat, curve, _ = case()
        candidates = deferred_candidates(
            curve, "ZCH26", days[1], cat
        )

        self.assertEqual(
            select_target_days_deferred(
                candidates, days[1], 45, cal, cat
            ),
            "ZCN26",
        )
        self.assertEqual(
            select_target_days_deferred(
                candidates, days[1], 75, cal, cat
            ),
            "ZCU26",
        )
        self.assertIsNone(
            select_target_days_deferred(
                (), days[1], 45, cal, cat
            )
        )

        with self.assertRaises(ContractError):
            select_target_days_deferred(
                candidates, days[1], 60, cal, cat
            )

        # Decision index 10: anchors 35 and 75 are equally
        # distant from target 45. Earlier maturity wins.
        alt = dict(cat)
        alt["ZCU26"] = Contract(
            cat["ZCU26"].identity,
            days[0],
            days[75],
            days[76],
            50,
            "synthetic",
        )

        self.assertEqual(
            select_target_days_deferred(
                ("ZCK26", "ZCU26"),
                days[10],
                45,
                cal,
                alt,
            ),
            "ZCK26",
        )

    def test_observed_sessions_skip_weekends(self):
        days, cal, cat, _, _ = case()

        # The calendar contains non-consecutive calendar dates.
        self.assertTrue(
            any(
                (date.fromisoformat(days[i + 1])
                 - date.fromisoformat(days[i])).days > 1
                for i in range(len(days) - 1)
            )
        )

        # Distance is measured in observed sessions.
        self.assertEqual(
            days_to_anchor(
                cal, days[1], cat["ZCN26"].anchor
            ),
            54,
        )
        self.assertEqual(
            days_to_anchor(
                cal, days[1], cat["ZCU26"].anchor
            ),
            84,
        )

        # Calendar-day distance is greater because of weekends.
        self.assertGreater(
            (
                date.fromisoformat(cat["ZCN26"].anchor)
                - date.fromisoformat(days[1])
            ).days,
            54,
        )

    def test_schedule_and_fixed_position_dates(self):
        cases = [
            ("next_deferred", "ZCK26"),
            ("second_deferred", "ZCN26"),
            ("target_45", "ZCN26"),
            ("target_75", "ZCU26"),
        ]

        for rule, expected in cases:
            with self.subTest(rule=rule):
                days, cal, cat, curve, baseline = case()

                schedule = build_destination_schedule(
                    baseline, curve, cal, cat, rule
                )

                self.assertEqual(
                    schedule.events[0]["destination"],
                    expected,
                )

                result = apply_destination_rule_to_baseline(
                    baseline, schedule, curve, cal, cat
                )

                self.assertEqual(
                    [p["date"] for p in result.positions],
                    [p["date"] for p in baseline.positions],
                )
                self.assertEqual(
                    [p["decision_date"] for p in result.positions],
                    [p["decision_date"] for p in baseline.positions],
                )
                self.assertEqual(
                    [p["contract"] for p in result.positions],
                    [expected] * 3,
                )
                self.assertTrue(
                    all(p["implemented"] for p in result.positions)
                )

    def test_no_deferred_is_unimplemented(self):
        days, cal, cat, curve, baseline = case()

        # Retain all curve rows. Explicitly mark deferred
        # contracts ineligible instead of omitting their data.
        curve = [
            {
                **row,
                "eligible": (
                    row["eligible"]
                    and row["contract"] == "ZCH26"
                ),
            }
            for row in curve
        ]

        schedule = build_destination_schedule(
            baseline, curve, cal, cat, "target_45"
        )

        self.assertFalse(
            schedule.events[0]["implemented"]
        )
        self.assertIn(
            "unimplemented_no_eligible_deferred",
            schedule.events[0]["flags"],
        )

        result = apply_destination_rule_to_baseline(
            baseline, schedule, curve, cal, cat
        )

        self.assertTrue(
            all(
                p["contract"] is None
                and not p["implemented"]
                for p in result.positions
            )
        )

    def test_second_deferred_fallback_flag(self):
        days, cal, cat, curve, baseline = case()

        # Preserve complete rows while leaving only one
        # eligible deferred candidate.
        curve = [
            {
                **row,
                "eligible": (
                    row["eligible"]
                    and row["contract"] in ("ZCH26", "ZCK26")
                ),
            }
            for row in curve
        ]

        schedule = build_destination_schedule(
            baseline, curve, cal, cat, "second_deferred"
        )

        self.assertEqual(
            schedule.events[0]["destination"],
            "ZCK26",
        )
        self.assertTrue(
            schedule.events[0]["fallback_flag"]
        )
        self.assertIn(
            "second_deferred_fallback_to_next",
            schedule.events[0]["flags"],
        )

    def test_duplicate_and_invalid_curve_rejected(self):
        days, cal, cat, curve, baseline = case()

        with self.assertRaises(ContractError):
            deferred_candidates(
                curve + [curve[0]],
                "ZCH26",
                days[1],
                cat,
            )

        bad = [dict(r) for r in curve]
        bad[0]["days_to_anchor"] = 999

        with self.assertRaises(ContractError):
            build_destination_schedule(
                baseline, bad, cal, cat, "next_deferred"
            )

    def test_multiple_events_same_day(self):
        days, cal, cat, curve, baseline = case()

        first = baseline.roll_events[0]
        second = {
            **first,
            "outgoing_contract": "ZCK26",
            "baseline_destination": "ZCN26",
            "anchor": cat["ZCK26"].anchor,
        }

        baseline = BaselinePath(
            baseline.positions,
            (first, second),
        )

        schedule = build_destination_schedule(
            baseline, curve, cal, cat, "next_deferred"
        )

        self.assertEqual(
            [e["destination"] for e in schedule.events],
            ["ZCK26", "ZCN26"],
        )

        result = apply_destination_rule_to_baseline(
            baseline, schedule, curve, cal, cat
        )

        self.assertTrue(
            all(
                p["contract"] == "ZCN26"
                for p in result.positions
            )
        )

    def test_becoming_ineligible_does_not_trigger_unscheduled_roll(self):
        days, cal, cat, curve, baseline = case()

        changed = [dict(r) for r in curve]

        for row in changed:
            if (
                row["date"] == days[2]
                and row["contract"] == "ZCK26"
            ):
                row["eligible"] = False

        schedule = build_destination_schedule(
            baseline, changed, cal, cat, "next_deferred"
        )

        result = apply_destination_rule_to_baseline(
            baseline, schedule, changed, cal, cat
        )

        self.assertTrue(
            result.positions[0]["implemented"]
        )
        self.assertFalse(
            result.positions[1]["implemented"]
        )
        self.assertIn(
            "unimplemented_active_contract_ineligible",
            result.positions[1]["flags"],
        )
        self.assertIsNone(
            result.positions[2]["contract"]
        )
        self.assertEqual(
            len(result.roll_events),
            1,
        )

    def test_fixture_destination_end_to_end(self):
        """Synthetic Section 0 fixture -> baseline -> alternative path."""
        cases = [
            ("next_deferred", "ZCK26"),
            ("second_deferred", "ZCN26"),
            ("target_45", "ZCN26"),
            ("target_75", "ZCU26"),
        ]

        for rule, expected in cases:
            with self.subTest(rule=rule):
                days, calendar, catalogue, _, _ = case()

                decision_dates = days[1:9]

                curve = [
                    {
                        "date": day,
                        "commodity": "ZC",
                        "contract": symbol,
                        "anchor": contract.anchor,
                        "days_to_anchor": (
                            calendar.indices[contract.anchor]
                            - calendar.indices[day]
                        ),
                        "eligible": (
                            contract.activation <= day < contract.anchor
                        ),
                    }
                    for day in decision_dates
                    for symbol, contract in catalogue.items()
                ]

                baseline = build_baseline_path(
                    curve,
                    decision_dates,
                    ("ZC",),
                    calendar,
                    catalogue,
                )

                self.assertEqual(
                    len(baseline.roll_events),
                    1,
                )
                self.assertEqual(
                    baseline.roll_events[0]["date"],
                    days[5],
                )

                schedule = build_destination_schedule(
                    baseline,
                    curve,
                    calendar,
                    catalogue,
                    rule,
                )

                result = apply_destination_rule_to_baseline(
                    baseline,
                    schedule,
                    curve,
                    calendar,
                    catalogue,
                )

                self.assertEqual(
                    [e["date"] for e in result.roll_events],
                    [e["date"] for e in baseline.roll_events],
                )
                self.assertEqual(
                    [p["date"] for p in result.positions],
                    [p["date"] for p in baseline.positions],
                )
                self.assertEqual(
                    [p["decision_date"] for p in result.positions],
                    decision_dates,
                )
                self.assertEqual(
                    [p["decision_cutoff"] for p in result.positions],
                    [
                        p["decision_cutoff"]
                        for p in baseline.positions
                    ],
                )
                self.assertEqual(
                    schedule.events[0]["destination"],
                    expected,
                )
                self.assertEqual(
                    [p["contract"] for p in result.positions[:4]],
                    ["ZCH26"] * 4,
                )
                self.assertEqual(
                    [p["contract"] for p in result.positions[4:]],
                    [expected] * 4,
                )
                self.assertTrue(
                    all(
                        p["implemented"]
                        for p in result.positions
                    )
                )


if __name__ == "__main__":
    unittest.main()
