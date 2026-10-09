"""Frozen baseline settings and a pure contract-path state machine.

The path consumes explicit per-decision curves; it does not size positions,
apply capacity/rounding, acquire data or construct the historical pipeline.
"""
from dataclasses import dataclass
from decimal import Decimal

from src.contracts.eligibility import is_eligible
from src.contracts.maturity import days_to_anchor
from src.validation.schemas import ContractError, ROOTS


@dataclass(frozen=True)
class BaselineSpec:
    initial_contract: str = "nearest_eligible"
    destination: str = "next_deferred_eligible"
    roll_offset_sessions: int = -5
    capacity_participation_rate: Decimal = Decimal("0.025")
    liquidation_horizon_days: int = 1
    adv_lookback_sessions: int = 20
    residual_handling: str = "unimplemented"
    rounding: str = "nearest_integer_after_capacity"


BASELINE = BaselineSpec()


@dataclass(frozen=True)
class BaselinePath:
    positions: tuple[dict, ...]
    roll_events: tuple[dict, ...]


def build_baseline_path(eligible_curve, decision_dates, commodities, calendar, catalogue):
    """Generate active contracts and roll events from supplied decision-time curves.

    Event ``date`` is the decision session. Position ``date`` is the next
    session, the end of the interval for which that contract is already fixed.
    Curves/metadata must be known by each supplied session's decision cut-off.
    Missing inputs or a required deferred destination fail explicitly; they
    never cause an expired position to be carried or a flat position invented.
    """
    dates, roots = tuple(decision_dates), tuple(commodities)
    if not dates or len(set(dates)) != len(dates):
        raise ContractError("Nonempty unique decision sessions required")
    if not roots or len(set(roots)) != len(roots) or any(r not in ROOTS for r in roots):
        raise ContractError("Unique locked commodity roots required")
    indices = [calendar.indices.get(day) for day in dates]
    if any(i is None or i + 1 >= len(calendar.dates) for i in indices):
        raise ContractError("Every decision requires a verified next session")
    if any(b != a + 1 for a, b in zip(indices, indices[1:])):
        raise ContractError("Decision sessions must be consecutive and ordered")
    curves = {(day, root): {} for day in dates for root in roots}
    for row in eligible_curve:
        key = (row["date"], row["commodity"])
        if key not in curves:
            raise ContractError("Curve row lies outside the requested path")
        symbol = row["contract"]
        contract = catalogue.get(symbol)
        if contract is None or contract.identity.contract != symbol or contract.identity.root != key[1]:
            raise ContractError("Curve contract identity does not match the catalogue")
        if symbol in curves[key]:
            raise ContractError("Duplicate contract in decision curve")
        if row["anchor"] != contract.anchor or row["days_to_anchor"] != days_to_anchor(calendar, key[0], contract.anchor):
            raise ContractError("Curve anchor/distance disagrees with the explicit calendar")
        if type(row["eligible"]) is not bool or (row["eligible"] and not is_eligible(contract, key[0])):
            raise ContractError("Curve cannot make an inactive or expired contract eligible")
        curves[key][symbol] = row

    active, positions, events = {}, [], []
    for day, index in zip(dates, indices):
        for root in sorted(roots):
            curve = curves[(day, root)]
            candidates = sorted((s for s, r in curve.items() if r["eligible"]),
                                key=lambda s: (catalogue[s].anchor, s))
            if not candidates:
                raise ContractError(f"No eligible curve for {root} on {day}")
            current = active.get(root, candidates[0])
            # Also prevents retaining an outgoing contract whose eligibility
            # disappears before its scheduled deadline. On late sample entry,
            # an already-due nearest contract is immediately rolled forward.
            while (current not in candidates or
                   days_to_anchor(calendar, day, catalogue[current].anchor) <= -BASELINE.roll_offset_sessions):
                deferred = [s for s in candidates if catalogue[s].anchor > catalogue[current].anchor]
                if not deferred:
                    raise ContractError(f"No deferred eligible destination after {current} on {day}")
                incoming = deferred[0]
                events.append({"date": day, "commodity": root, "outgoing_contract": current,
                               "baseline_destination": incoming, "anchor": catalogue[current].anchor})
                current = incoming
            active[root] = current
            positions.append({"date": calendar.dates[index + 1], "decision_date": day,
                              "decision_cutoff": calendar.sessions[day].decision_cutoff,
                              "commodity": root, "contract": current})
    return BaselinePath(tuple(positions), tuple(events))
