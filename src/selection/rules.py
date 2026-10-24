
"""Alternative deferred destinations on the *fixed* Section 0 baseline event dates.
Pure contract-identity logic: no changes to timing, sizing, capacity, rounding,
weights, benchmark, or accounting. Incomplete paths are explicitly flagged.

Production curve completeness is validated upstream by shared Section 0.
"""

from __future__ import annotations
from dataclasses import dataclass
from src.contracts.eligibility import is_eligible
from src.contracts.maturity import days_to_anchor
from src.portfolio.baseline import BaselinePath
from src.validation.schemas import ContractError

RULE_TARGETS = {"next_deferred": None, "second_deferred": None,
                "target_45": 45, "target_75": 75}


@dataclass(frozen=True)
class DestinationSchedule:
    events: tuple[dict, ...]


def _curves(eligible_curve, catalogue, calendar):
    """Validate supplied curve rows, including the Section 0 as-of fields.

    Requires a complete curve validated by Section 0 upstream.
    Missing rows must not be interpreted as confirmed ineligibility.
    """
    curves = {}
    for row in eligible_curve:
        day, root, symbol = row["date"], row["commodity"], row["contract"]
        contract = catalogue.get(symbol)
        if (contract is None or contract.identity.contract != symbol
                or contract.identity.root != root):
            raise ContractError(f"Curve/catalogue identity mismatch: {symbol}")
        if day not in calendar.indices:
            raise ContractError(f"Unknown decision session: {day}")
        if row["anchor"] != contract.anchor:
            raise ContractError(f"Curve anchor mismatch: {symbol}")
        if "days_to_anchor" in row and row["days_to_anchor"] != days_to_anchor(calendar, day, contract.anchor):
            raise ContractError(f"Curve distance mismatch: {symbol}")
        if type(row["eligible"]) is not bool:
            raise ContractError("Curve eligible must be a boolean")
        if row["eligible"] and not is_eligible(contract, day):
            raise ContractError(f"Curve incorrectly marks contract eligible: {symbol}")
        key = (day, root)
        bucket = curves.setdefault(key, {})
        if symbol in bucket:
            raise ContractError(f"Duplicate curve contract: {symbol} on {day}")
        bucket[symbol] = row
    return curves


def deferred_candidates(curve, outgoing_contract, decision_date, catalogue):
    """Eligible same-root contracts strictly later by anchor, sorted deterministically.

    Requires a complete decision-date curve validated by Section 0.
    """
    outgoing = catalogue.get(outgoing_contract)
    if outgoing is None or outgoing.identity.contract != outgoing_contract:
        raise ContractError(f"Unknown outgoing contract: {outgoing_contract}")
    root = outgoing.identity.root
    seen, candidates = set(), []
    for row in curve:
        if row["date"] != decision_date or row["commodity"] != root:
            continue
        symbol = row["contract"]
        if symbol in seen:
            raise ContractError(f"Duplicate curve contract: {symbol}")
        seen.add(symbol)
        contract = catalogue.get(symbol)
        if (contract is None or contract.identity.contract != symbol
                or contract.identity.root != root or row["anchor"] != contract.anchor):
            raise ContractError(f"Curve/catalogue mismatch: {symbol}")
        if type(row["eligible"]) is not bool:
            raise ContractError("Curve eligible must be a boolean")
        if row["eligible"] and not is_eligible(contract, decision_date):
            raise ContractError(f"Ineligible contract marked eligible: {symbol}")
        if row["eligible"] and contract.anchor > outgoing.anchor:
            candidates.append(symbol)
    return tuple(sorted(candidates, key=lambda s: (catalogue[s].anchor, s)))


def select_next_deferred(candidates):
    return candidates[0] if candidates else None


def select_second_deferred(candidates):
    """Return (symbol, fallback_flag); flag is True only for one-candidate fallback."""
    if len(candidates) >= 2:
        return candidates[1], False
    if len(candidates) == 1:
        return candidates[0], True
    return None, False


def select_target_days_deferred(candidates, decision_date, target_days, calendar, catalogue):
    """Nearest observed-session distance; earlier anchor wins exact ties."""
    if target_days not in (45, 75):
        raise ContractError("Destination targets are locked to 45 or 75 sessions")
    return min(candidates, key=lambda s: (
        abs(days_to_anchor(calendar, decision_date, catalogue[s].anchor) - target_days),
        catalogue[s].anchor, s,
    )) if candidates else None


def _choose(candidates, day, rule, calendar, catalogue):
    if rule == "second_deferred":
        symbol, fallback = select_second_deferred(candidates)
        return symbol, fallback
    if rule == "next_deferred":
        return select_next_deferred(candidates), False
    return select_target_days_deferred(candidates, day, RULE_TARGETS[rule], calendar, catalogue), False


def build_destination_schedule(baseline_path, eligible_curve, calendar, catalogue, rule):
    """Process each baseline event in order, using the alternative's actual outgoing.
    Multiple baseline events on one date are allowed. No additional event dates
    are introduced. An impossible transition remains explicitly unimplemented.

    Production callers must use Section 0 completeness-validated curves.
    """
    if rule not in RULE_TARGETS:
        raise ContractError(f"Unknown destination rule: {rule}")
    curves = _curves(eligible_curve, catalogue, calendar)
    # Baseline positions are the source of initial active contracts for each root.
    first_by_root = {}
    for pos in baseline_path.positions:
        first_by_root.setdefault(pos["commodity"], pos)
    first_events = {}
    for event in baseline_path.roll_events:
        first_events.setdefault(event["commodity"], event)
    active = {}
    for root, pos in first_by_root.items():
        first = first_events.get(root)
        active[root] = (first["outgoing_contract"] if first and first["date"] == pos["decision_date"]
                        else pos["contract"])
    events = []
    last_index = -1
    for event in baseline_path.roll_events:
        day, root = event["date"], event["commodity"]
        index = calendar.indices.get(day)
        if index is None or index < last_index:
            raise ContractError("Baseline events must be ordered by decision session")
        last_index = index
        if root not in active:
            raise ContractError(f"No initial active contract for {root}")
        key = (day, root)
        if key not in curves:
            raise ContractError(f"Missing decision curve for {root} on {day}")
        outgoing = active[root]
        if outgoing is None:
            destination, fallback = None, False
        else:
            candidates = deferred_candidates(curves[key].values(), outgoing, day, catalogue)
            destination, fallback = _choose(candidates, day, rule, calendar, catalogue)
        flags = []
        if fallback:
            flags.append("second_deferred_fallback_to_next")
        if destination is None:
            flags.append("unimplemented_no_eligible_deferred")
        events.append({**event, "strategy": rule, "actual_outgoing_contract": outgoing,
                       "destination": destination, "fallback_flag": fallback,
                       "flags": tuple(flags), "implemented": destination is not None})
        active[root] = destination
    return DestinationSchedule(tuple(events))


def apply_destination_rule_to_baseline(baseline_path, destination_schedule,
                                       eligible_curve, calendar, catalogue):
    """Apply decisions to baseline position dates, checking eligibility each day.
    A contract becoming ineligible between scheduled events marks the path
    unimplemented; the engine never inserts an unscheduled replacement roll.

    Production callers must use Section 0 completeness-validated curves.
    """
    curves = _curves(eligible_curve, catalogue, calendar)
    baseline_events = baseline_path.roll_events
    if len(destination_schedule.events) != len(baseline_events):
        raise ContractError("Destination event count differs from baseline")
    for original, selected in zip(baseline_events, destination_schedule.events):
        if any(original[k] != selected[k] for k in
               ("date", "commodity", "outgoing_contract", "baseline_destination", "anchor")):
            raise ContractError("Destination schedule changes baseline event identity")
    events_by_key = {}
    for event in destination_schedule.events:
        events_by_key.setdefault((event["date"], event["commodity"]), []).append(event)
    first_event = {}
    for event in baseline_events:
        first_event.setdefault(event["commodity"], event)
    active = {}
    used = set()
    positions = []
    for position in baseline_path.positions:
        day, root = position["decision_date"], position["commodity"]
        key = (day, root)
        if key not in curves:
            raise ContractError(f"Missing decision curve for {root} on {day}")
        if root not in active:
            initial = first_event.get(root)
            active[root] = (initial["outgoing_contract"] if initial and initial["date"] == day
                            else position["contract"])
        flags = []
        for event in events_by_key.get(key, ()):
            used.add(key)
            if event["actual_outgoing_contract"] != active[root]:
                raise ContractError("Destination event chain is inconsistent")
            active[root] = event["destination"]
            flags.extend(event["flags"])
        selected = active[root]
        if selected is None:
            flags.append("unimplemented_no_active_contract")
        elif (selected not in curves[key] or not curves[key][selected]["eligible"]
              or not is_eligible(catalogue[selected], day)):
            flags.append("unimplemented_active_contract_ineligible")
        implemented = not any(f.startswith("unimplemented") for f in flags)
        # Once an alternative path is invalid, do not silently resume it later.
        if not implemented:
            active[root] = None
        positions.append({**position, "contract": selected if implemented else None,
                          "implemented": implemented, "flags": tuple(dict.fromkeys(flags))})
    if used != set(events_by_key):
        raise ContractError("Destination events do not match baseline position dates")
    return BaselinePath(tuple(positions), destination_schedule.events)
