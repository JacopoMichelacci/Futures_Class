"""Unit tests for Issue #12, using entirely synthetic observed sessions."""
from datetime import date, timedelta

import pytest

from src.contracts.eligibility import Contract
from src.data.calendar import SessionCalendar, TradingSession
from src.portfolio.baseline import BaselinePath, build_baseline_path
from src.selection.rules import (
    deferred_candidates, select_next_deferred, select_second_deferred,
    select_target_days_deferred, build_destination_schedule,
    apply_destination_rule_to_baseline,
)
from src.validation.schemas import Identity, ContractError


def case():
    days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(120)]
    calendar = SessionCalendar(TradingSession(d, d + "T18:00:00Z", d + "T17:00:00Z") for d in days)
    anchors = {"ZCH26": 10, "ZCK26": 35, "ZCN26": 55, "ZCU26": 85}
    catalogue = {
        s: Contract(Identity("GLBX.MDP3", 1, i + 1, s, "ZC"), days[0], days[n],
                    days[n + 1], 50, "synthetic")
        for i, (s, n) in enumerate(anchors.items())
    }
    curve = [
        {"date": day, "commodity": "ZC", "contract": s,
         "anchor": c.anchor, "days_to_anchor": calendar.indices[c.anchor] - calendar.indices[day],
         "eligible": c.activation <= day < c.anchor}
        for day in days[1:4] for s, c in catalogue.items()
    ]
    event = {"date": days[1], "commodity": "ZC", "outgoing_contract": "ZCH26",
             "baseline_destination": "ZCK26", "anchor": catalogue["ZCH26"].anchor}
    baseline = BaselinePath(tuple({"date": days[i + 1], "decision_date": days[i],
                                   "commodity": "ZC", "contract": "ZCK26"}
                                  for i in (1, 2, 3)), (event,))
    return days, calendar, catalogue, curve, baseline


def test_candidates_deterministic_and_eligible():
    days, cal, cat, curve, _ = case()
    expected = ("ZCK26", "ZCN26", "ZCU26")
    assert deferred_candidates(curve, "ZCH26", days[1], cat) == expected
    assert deferred_candidates(list(reversed(curve)), "ZCH26", days[1], cat) == expected
    assert select_next_deferred(expected) == "ZCK26"
    assert select_second_deferred(expected) == ("ZCN26", False)
    assert select_second_deferred(("ZCK26",)) == ("ZCK26", True)
    assert select_second_deferred(()) == (None, False)


def test_targets_and_ties():
    days, cal, cat, curve, _ = case()
    candidates = deferred_candidates(curve, "ZCH26", days[1], cat)
    assert select_target_days_deferred(candidates, days[1], 45, cal, cat) == "ZCN26"
    assert select_target_days_deferred(candidates, days[1], 75, cal, cat) == "ZCU26"
    assert select_target_days_deferred((), days[1], 45, cal, cat) is None
    with pytest.raises(ContractError):
        select_target_days_deferred(candidates, days[1], 60, cal, cat)
    # Decision index 10: anchors 35 and 75 are equidistant from target 45.
    alt = dict(cat)
    alt["ZCU26"] = Contract(cat["ZCU26"].identity, days[0], days[75], days[76], 50, "synthetic")
    assert select_target_days_deferred(("ZCK26", "ZCU26"), days[10], 45, cal, alt) == "ZCK26"


@pytest.mark.parametrize("rule,expected", [
    ("next_deferred", "ZCK26"), ("second_deferred", "ZCN26"),
    ("target_45", "ZCN26"), ("target_75", "ZCU26"),
])
def test_schedule_and_fixed_position_dates(rule, expected):
    days, cal, cat, curve, baseline = case()
    schedule = build_destination_schedule(baseline, curve, cal, cat, rule)
    assert schedule.events[0]["destination"] == expected
    result = apply_destination_rule_to_baseline(baseline, schedule, curve, cal, cat)
    assert [p["date"] for p in result.positions] == [p["date"] for p in baseline.positions]
    assert [p["decision_date"] for p in result.positions] == [p["decision_date"] for p in baseline.positions]
    assert [p["contract"] for p in result.positions] == [expected] * 3
    assert all(p["implemented"] for p in result.positions)


def test_no_deferred_is_unimplemented():
    days, cal, cat, curve, baseline = case()
    curve = [r for r in curve if r["contract"] == "ZCH26"]
    schedule = build_destination_schedule(baseline, curve, cal, cat, "target_45")
    assert schedule.events[0]["implemented"] is False
    result = apply_destination_rule_to_baseline(baseline, schedule, curve, cal, cat)
    assert all(p["contract"] is None and not p["implemented"] for p in result.positions)


def test_second_deferred_fallback_flag():
    days, cal, cat, curve, baseline = case()
    curve = [r for r in curve if r["contract"] in ("ZCH26", "ZCK26")]
    schedule = build_destination_schedule(baseline, curve, cal, cat, "second_deferred")
    assert schedule.events[0]["destination"] == "ZCK26"
    assert schedule.events[0]["fallback_flag"] is True
    assert "second_deferred_fallback_to_next" in schedule.events[0]["flags"]


def test_duplicate_and_invalid_curve_rejected():
    days, cal, cat, curve, baseline = case()
    with pytest.raises(ContractError):
        deferred_candidates(curve + [curve[0]], "ZCH26", days[1], cat)
    bad = [dict(r) for r in curve]
    bad[0]["days_to_anchor"] = 999
    with pytest.raises(ContractError):
        build_destination_schedule(baseline, bad, cal, cat, "next_deferred")


def test_multiple_events_same_day():
    days, cal, cat, curve, baseline = case()
    first = baseline.roll_events[0]
    second = {**first, "outgoing_contract": "ZCK26", "baseline_destination": "ZCN26",
              "anchor": cat["ZCK26"].anchor}
    baseline = BaselinePath(baseline.positions, (first, second))
    schedule = build_destination_schedule(baseline, curve, cal, cat, "next_deferred")
    assert [e["destination"] for e in schedule.events] == ["ZCK26", "ZCN26"]
    result = apply_destination_rule_to_baseline(baseline, schedule, curve, cal, cat)
    assert all(p["contract"] == "ZCN26" for p in result.positions)


def test_becoming_ineligible_does_not_trigger_unscheduled_roll():
    days, cal, cat, curve, baseline = case()
    # Explicitly withdraw eligibility of selected contract on next decision date.
    changed = [dict(r) for r in curve]
    for row in changed:
        if row["date"] == days[2] and row["contract"] == "ZCK26":
            row["eligible"] = False
    schedule = build_destination_schedule(baseline, changed, cal, cat, "next_deferred")
    result = apply_destination_rule_to_baseline(baseline, schedule, changed, cal, cat)
    assert result.positions[0]["implemented"]
    assert not result.positions[1]["implemented"]
    assert "unimplemented_active_contract_ineligible" in result.positions[1]["flags"]
    assert result.positions[2]["contract"] is None
    assert len(result.roll_events) == 1


@pytest.mark.parametrize("rule,expected", [
    ("next_deferred", "ZCK26"),
    ("second_deferred", "ZCN26"),
    ("target_45", "ZCN26"),
    ("target_75", "ZCU26"),
])
def test_fixture_destination_end_to_end(rule, expected):
    """Synthetic Section 0 fixture -> baseline roll events -> alternative path.

    This is a synthetic contract-path demonstration, not a P&L experiment.
    """
    days, calendar, catalogue, _, _ = case()
    decision_dates = days[1:9]
    curve = [
        {
            "date": day, "commodity": "ZC", "contract": symbol,
            "anchor": contract.anchor,
            "days_to_anchor": calendar.indices[contract.anchor] - calendar.indices[day],
            "eligible": contract.activation <= day < contract.anchor,
        }
        for day in decision_dates
        for symbol, contract in catalogue.items()
    ]
    baseline = build_baseline_path(curve, decision_dates, ("ZC",), calendar, catalogue)
    assert len(baseline.roll_events) == 1
    assert baseline.roll_events[0]["date"] == days[5]

    schedule = build_destination_schedule(baseline, curve, calendar, catalogue, rule)
    result = apply_destination_rule_to_baseline(
        baseline, schedule, curve, calendar, catalogue
    )
    assert [e["date"] for e in result.roll_events] == [
        e["date"] for e in baseline.roll_events
    ]
    assert [p["date"] for p in result.positions] == [
        p["date"] for p in baseline.positions
    ]
    assert [p["decision_date"] for p in result.positions] == decision_dates
    assert [p["decision_cutoff"] for p in result.positions] == [
        p["decision_cutoff"] for p in baseline.positions
    ]
    assert schedule.events[0]["destination"] == expected
    assert [p["contract"] for p in result.positions[:4]] == ["ZCH26"] * 4
    assert [p["contract"] for p in result.positions[4:]] == [expected] * 4
    assert all(p["implemented"] for p in result.positions)
