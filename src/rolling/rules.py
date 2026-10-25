"""Roll-timing alternatives for the fixed baseline outgoing/incoming pair.

Timing only: destination, capacity, rounding, weights, benchmark and metric are
unchanged, and the shared ``BASELINE`` constant is never mutated. Lookups reuse
``ObservationStore.latest_common`` so publication, revision and freshness rules
stay shared. The crossover search window is supplied explicitly by the caller;
the team-proposed boundary (Issue #23) is documented, never defaulted here.
"""
from dataclasses import dataclass

from src.contracts.eligibility import is_eligible
from src.portfolio.baseline import BASELINE
from src.validation.schemas import ContractError

FIXED_OFFSETS = {"fixed_-5": BASELINE.roll_offset_sessions, "fixed_-10": -10}
CROSSOVER_STATISTICS = {"volume_crossover": "cleared_volume", "oi_crossover": "open_interest"}
RULES = (*FIXED_OFFSETS, *CROSSOVER_STATISTICS)


@dataclass(frozen=True)
class RollPair:
    commodity: str
    outgoing_contract: str
    incoming_contract: str
    anchor: str
    baseline_event_date: str


def build_baseline_roll_pairs(baseline_path):
    """One frozen pair per baseline roll event; every timing rule reuses it."""
    pairs = tuple(RollPair(e["commodity"], e["outgoing_contract"], e["baseline_destination"],
                           e["anchor"], e["date"]) for e in baseline_path.roll_events)
    if len({(p.commodity, p.outgoing_contract) for p in pairs}) != len(pairs):
        raise ContractError("Each outgoing contract may be rolled only once")
    return pairs


def _session_index(calendar, day, name):
    if day not in calendar.indices:
        raise ContractError(f"{name} must be an explicit calendar session")
    return calendar.indices[day]


def _check_roll_session(pair, calendar, catalogue, index):
    """Outgoing still eligible at the decision; incoming eligible when first held."""
    if index < 0 or index + 1 >= len(calendar.dates):
        raise ContractError("Roll decision requires an explicit session and a next session")
    day, held_from = calendar.dates[index], calendar.dates[index + 1]
    if not is_eligible(catalogue[pair.outgoing_contract], day):
        raise ContractError(f"{pair.outgoing_contract} is not eligible on roll decision {day}")
    if not is_eligible(catalogue[pair.incoming_contract], held_from):
        raise ContractError(f"{pair.incoming_contract} is not eligible on {held_from}")
    return day


def fixed_roll_date(pair, calendar, catalogue, offset_sessions):
    """Decision session ``offset_sessions`` observed sessions before the outgoing anchor."""
    if type(offset_sessions) is not int or offset_sessions >= 0:
        raise ContractError("Fixed roll offset must be a negative integer of sessions")
    index = _session_index(calendar, pair.anchor, "Outgoing anchor") + offset_sessions
    return _check_roll_session(pair, calendar, catalogue, index)


def _first_crossover(store, pair, statistic, calendar, search_start, search_deadline):
    """First decision session in [start, deadline) where incoming strictly exceeds outgoing.

    Each session sees only the pair's aligned observations published by its own
    decision cut-off. Missing or unaligned inputs are recorded, never a signal.
    Later qualifiers cannot replace the first one because the search stops there.
    """
    start = _session_index(calendar, search_start, "Search start")
    deadline = _session_index(calendar, search_deadline, "Search deadline")
    if start > deadline:
        raise ContractError("Search start must not follow the search deadline")
    contracts = [pair.outgoing_contract, pair.incoming_contract]
    checked = []
    for index in range(start, deadline):
        day = calendar.dates[index]
        context = calendar.decision(calendar.dates[index + 1])
        source_date, aligned = store.latest_common(contracts, [statistic], context)
        if source_date is None:
            checked.append({"decision_date": day, "outcome": "no_aligned_publication_safe_pair"})
            continue
        outgoing, incoming = (aligned[(c, statistic)].observation for c in contracts)
        record = {"decision_date": day, "source_date": source_date,
                  "outgoing_value": outgoing.statistic_value, "incoming_value": incoming.statistic_value,
                  "observation_ids": (outgoing.observation_id, incoming.observation_id)}
        if incoming.statistic_value > outgoing.statistic_value:
            checked.append({**record, "outcome": "crossover"})
            return {"statistic": statistic, "crossover_date": day, "trigger": record, "checked": checked}
        checked.append({**record, "outcome": "incoming_not_greater"})
    return {"statistic": statistic, "crossover_date": None, "trigger": None, "checked": checked}


def lagged_volume_crossover(store, pair, calendar, search_start, search_deadline):
    return _first_crossover(store, pair, "cleared_volume", calendar, search_start, search_deadline)


def lagged_oi_crossover(store, pair, calendar, search_start, search_deadline):
    return _first_crossover(store, pair, "open_interest", calendar, search_start, search_deadline)


def apply_roll_timing_rule(pair, rule, calendar, catalogue, store=None,
                           search_start=None, search_deadline=None):
    """Roll decision for one pair under one rule; the trade earns only later intervals.

    Crossover rules fall back to the baseline -5 session (flagged) when no
    qualifier is found. The deadline may not follow that session, so the
    fallback can never depend on information arriving after it.
    """
    if rule not in RULES:
        raise ContractError(f"Unknown timing rule {rule!r}")
    fallback, search = False, None
    if rule in FIXED_OFFSETS:
        decision = fixed_roll_date(pair, calendar, catalogue, FIXED_OFFSETS[rule])
    else:
        if store is None or search_start is None or search_deadline is None:
            raise ContractError("Crossover rules require a store and an explicit causal search window")
        baseline_day = fixed_roll_date(pair, calendar, catalogue, BASELINE.roll_offset_sessions)
        if _session_index(calendar, search_deadline, "Search deadline") > calendar.indices[baseline_day]:
            raise ContractError("Search deadline may not follow the -5 fallback decision")
        search = _first_crossover(store, pair, CROSSOVER_STATISTICS[rule], calendar,
                                  search_start, search_deadline)
        if search["crossover_date"] is None:
            decision, fallback = baseline_day, True
        else:
            decision = _check_roll_session(pair, calendar, catalogue,
                                           calendar.indices[search["crossover_date"]])
    index = calendar.indices[decision]
    return {"commodity": pair.commodity, "outgoing_contract": pair.outgoing_contract,
            "incoming_contract": pair.incoming_contract, "anchor": pair.anchor, "rule": rule,
            "roll_decision_date": decision, "decision_cutoff": calendar.sessions[decision].decision_cutoff,
            "first_incoming_interval_end": calendar.dates[index + 1],
            "sessions_before_anchor": calendar.indices[pair.anchor] - index,
            "fallback_flag": fallback, "search_start": search_start, "search_deadline": search_deadline,
            "trigger": search and search["trigger"], "checked": search["checked"] if search else ()}


def build_roll_timing_portfolio(baseline_path, timing_decisions, calendar, catalogue):
    """Baseline contract path with each pair switched at its timed decision.

    A position row's contract is the incoming contract exactly when the row's
    decision session is on or after the timed roll decision. No sizing, capacity
    or rounding is applied; no contract is held on or after its anchor.
    """
    pairs = {(p.commodity, p.outgoing_contract): p for p in build_baseline_roll_pairs(baseline_path)}
    decisions = {}
    for d in timing_decisions:
        key = (d["commodity"], d["outgoing_contract"])
        if key not in pairs or pairs[key].incoming_contract != d["incoming_contract"]:
            raise ContractError("Timing decisions must use the unchanged baseline pairs")
        if key in decisions or (decisions and d["rule"] != next(iter(decisions.values()))["rule"]):
            raise ContractError("Supply exactly one decision per pair, all from one rule")
        decisions[key] = d
    if set(decisions) != set(pairs):
        raise ContractError("Every baseline roll pair requires a timing decision")
    rule = next(iter(decisions.values()))["rule"] if decisions else "fixed_-5"
    by_incoming = {(c, d["incoming_contract"]): d for (c, _), d in decisions.items()}

    positions = []
    for row in baseline_path.positions:
        contract, root = row["contract"], row["commodity"]
        outgoing_roll = decisions.get((root, contract))
        if outgoing_roll and row["decision_date"] >= outgoing_roll["roll_decision_date"]:
            contract = outgoing_roll["incoming_contract"]
        else:
            incoming_roll = by_incoming.get((root, contract))
            if incoming_roll and row["decision_date"] < incoming_roll["roll_decision_date"]:
                contract = incoming_roll["outgoing_contract"]
        if not is_eligible(catalogue[contract], row["date"]):
            raise ContractError(f"Timing would hold {contract} on or after its anchor on {row['date']}")
        positions.append({**row, "contract": contract, "timing_rule": rule})
    return tuple(positions)
