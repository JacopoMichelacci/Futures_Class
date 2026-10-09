"""Revision-preserving as-of observations; independent of any data loader or sample."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from types import MappingProxyType

from src.data.calendar import Decision, SessionCalendar
from src.validation.schemas import ContractError, Identity, utc_ns

STATISTICS = {3: "settlement", 6: "cleared_volume", 9: "open_interest"}


@dataclass(frozen=True)
class Observation:
    identity: Identity
    trading_date: str
    publication_timestamp: str  # ts_recv: conservative capture/availability proxy
    venue_event_timestamp: str
    statistic_type: str
    statistic_value: Decimal | None
    value_unit: str
    stat_flags: int
    update_action: int
    sequence: int
    channel_id: int
    session_index: int | None
    invalid_reason: str | None

    @property
    def version_key(self):
        return (utc_ns(self.publication_timestamp), utc_ns(self.venue_event_timestamp), self.sequence)

    @property
    def is_final(self):
        return bool(self.stat_flags & 1) if self.statistic_type == "settlement" else None

    @property
    def quality(self):
        if self.statistic_type != "settlement":
            return "not_applicable"
        return "actual" if self.stat_flags & 2 else "theoretical"

    @property
    def valid_value(self):
        return self.invalid_reason is None and self.statistic_value is not None

    @property
    def selectable(self):
        return (self.update_action == 1 and self.valid_value
                and (self.statistic_type != "settlement" or (self.is_final and not self.stat_flags & 8)))

    @property
    def observation_id(self):
        payload = (self.identity.__dict__, self.trading_date, self.publication_timestamp,
                   self.venue_event_timestamp, self.statistic_type, str(self.statistic_value),
                   self.stat_flags, self.update_action, self.sequence, self.channel_id)
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def view(self, context: Decision, selected=False, adv20_window_index=None):
        known = utc_ns(self.publication_timestamp) <= context.cutoff_ns
        prior_session = self.session_index is not None and self.session_index <= context.decision_index
        eligible = known and prior_session and self.selectable
        if selected and not eligible:
            raise ContractError("A future, invalid or non-session observation cannot be selected")
        return {
            **self.identity.__dict__, "observation_id": self.observation_id,
            "trading_date": self.trading_date, "publication_timestamp": self.publication_timestamp,
            "publication_timestamp_basis": "ts_recv", "venue_event_timestamp": self.venue_event_timestamp,
            "statistic_type": self.statistic_type,
            "statistic_value": format(self.statistic_value, "f") if self.statistic_value is not None else None,
            "value_unit": self.value_unit, "stat_flags": self.stat_flags,
            "settlement_finality": ("final" if self.is_final else "preliminary") if self.statistic_type == "settlement" else "not_applicable",
            "settlement_quality": self.quality, "update_action": self.update_action,
            "sequence": self.sequence, "channel_id": self.channel_id,
            "session_index": self.session_index, "is_session": self.session_index is not None,
            "invalid_reason": self.invalid_reason, "decision_date": context.decision_date,
            "return_interval_end": context.return_date, "decision_cutoff": context.cutoff_utc,
            "calendar_id": context.calendar_id, "decision_session_index": context.decision_index,
            "return_interval_session_index": context.interval_index,
            "available_as_of": known, "eligible_as_of": eligible, "selected_as_of": selected,
            "lag_from_decision_sessions": context.decision_index - self.session_index if self.session_index is not None else None,
            "lag_from_interval_end_sessions": context.interval_index - self.session_index if self.session_index is not None else None,
            "adv20_window_index": adv20_window_index,
        }


def normalise(raw: dict, catalogue: dict[str, Identity], calendar: SessionCalendar) -> Observation:
    kind = STATISTICS.get(raw.get("stat_type"))
    if kind is None:
        raise ContractError("Only settlement, cleared volume and OI belong in this contract")
    identity = catalogue.get(raw.get("symbol"))
    header = raw.get("hd", {})
    if identity is None or (header.get("publisher_id"), header.get("instrument_id")) != (identity.publisher_id, identity.instrument_id):
        raise ContractError("Symbol and publisher/instrument identity do not reconcile")
    ref = raw.get("ts_ref")
    utc_ns(ref)
    if utc_ns(ref) != utc_ns(ref[:10] + "T00:00:00Z"):
        raise ContractError("Daily ts_ref must encode a date at UTC midnight")
    published, event = raw.get("ts_recv"), header.get("ts_event")
    if utc_ns(published) < utc_ns(event):
        raise ContractError("Capture precedes venue event; resolve timestamp quality before use")
    action = raw.get("update_action")
    if action not in (1, 2):
        raise ContractError("Unsupported statistics action")
    if not (0 <= int(raw["stat_flags"]) <= 255) or int(raw["sequence"]) < 0 or int(raw["channel_id"]) < 0:
        raise ContractError("Invalid flag, sequence or channel field")
    value = None
    error = None
    candidate = raw.get("price") if kind == "settlement" else raw.get("quantity")
    try:
        value = Decimal(str(candidate))
        if not value.is_finite() or value == 2**63 - 1:
            raise InvalidOperation
        if kind == "settlement" and value == Decimal("9223372036.854775807"):
            raise InvalidOperation  # Pretty-encoded undefined int64 price, if encountered.
        if kind != "settlement" and (value == 2**63 - 1 or value < 0 or value != value.to_integral_value()):
            raise InvalidOperation
    except InvalidOperation:
        value, error = None, "missing_or_invalid_statistic_value"
    return Observation(identity, ref[:10], published, event, kind, value,
                       "cents_per_bushel" if kind == "settlement" else "contracts",
                       int(raw["stat_flags"]), action, int(raw["sequence"]), int(raw["channel_id"]),
                       calendar.indices.get(ref[:10]), error)


@dataclass(frozen=True)
class Selection:
    observation: Observation | None
    reason: str | None


class ObservationStore:
    def __init__(self, observations: list[Observation], calendar: SessionCalendar, max_source_age_sessions: int = 1):
        if type(max_source_age_sessions) is not int or max_source_age_sessions < 0:
            raise ContractError("Source-age limit must be a non-negative integer")
        self.max_source_age_sessions = max_source_age_sessions
        self.calendar = calendar
        self.observations = tuple({o.observation_id: o for o in observations}.values())
        self.groups = defaultdict(list)
        identities = {}
        for o in self.observations:
            if o.session_index != calendar.indices.get(o.trading_date):
                raise ContractError("Observation session index disagrees with supplied calendar")
            previous = identities.setdefault(o.identity.contract, o.identity)
            if previous != o.identity:
                raise ContractError("Ambiguous contract alias: instrument identities cannot be mixed")
            self.groups[(o.identity.contract, o.trading_date, o.statistic_type)].append(o)
        self.identities = MappingProxyType(identities)
        self.groups = MappingProxyType({key: tuple(rows) for key, rows in self.groups.items()})

    def validate_context(self, context: Decision):
        if context != self.calendar.decision(context.return_date):
            raise ContractError("Decision context does not match the fixed calendar/cut-off policy")

    def select_version(self, contract: str, day: str, kind: str, cutoff_ns: int) -> Selection:
        if day not in self.calendar.indices:
            return Selection(None, "non_session_or_outside_verified_calendar")
        rows = [o for o in self.groups.get((contract, day, kind), ())
                if utc_ns(o.publication_timestamp) <= cutoff_ns]
        if not rows:
            return Selection(None, "no_observation_published_by_cutoff")
        versions = {}
        for o in rows:
            payload = (o.statistic_value, o.stat_flags, o.update_action, o.invalid_reason)
            if o.version_key in versions and versions[o.version_key] != payload:
                raise ContractError("Conflicting records share a version order; no arbitrary tie-break permitted")
            versions[o.version_key] = payload
        deleted_through = max((o.version_key for o in rows if o.update_action == 2), default=None)
        candidates = [o for o in rows if o.selectable
                      and (deleted_through is None or o.version_key > deleted_through)]
        if not candidates:
            return Selection(None, "deleted_or_no_valid_final_value")
        return Selection(max(candidates, key=lambda o: (o.version_key, o.observation_id)), None)

    def latest_available(self, contract: str, kind: str, context: Decision) -> Selection:
        self.validate_context(context)
        # One shared freshness policy; no unlimited stale-value carry-forward.
        stop = max(-1, context.decision_index - self.max_source_age_sessions - 1)
        for i in range(context.decision_index, stop, -1):
            result = self.select_version(contract, self.calendar.dates[i], kind, context.cutoff_ns)
            if result.observation is not None:
                return result
        return Selection(None, "no_valid_observation_within_source_age_limit")

    def latest_common(self, contracts: list[str], kinds: list[str], context: Decision):
        """Aligned inputs for later comparisons; this does not implement a crossover."""
        if not contracts or not kinds:
            raise ContractError("A nonempty aligned input set is required")
        self.validate_context(context)
        stop = max(-1, context.decision_index - self.max_source_age_sessions - 1)
        for i in range(context.decision_index, stop, -1):
            day = self.calendar.dates[i]
            result = {(c, k): self.select_version(c, day, k, context.cutoff_ns) for c in contracts for k in kinds}
            if all(s.observation is not None for s in result.values()):
                return day, result
        return None, {}

    def adv20(self, contract: str, context: Decision):
        self.validate_context(context)
        # Strictly lagged relative to the decision session, independently of
        # publication timing. Freshness is still measured from that session.
        endpoint = Selection(None, "no_valid_prior_observation_within_source_age_limit")
        stop = max(-1, context.decision_index - self.max_source_age_sessions - 1)
        for i in range(context.decision_index - 1, stop, -1):
            endpoint = self.select_version(contract, self.calendar.dates[i], "cleared_volume", context.cutoff_ns)
            if endpoint.observation is not None:
                break
        if endpoint.observation is None:
            return {"available": False, "reason": endpoint.reason, "rows": []}
        end = endpoint.observation.session_index
        window = self.calendar.window20(end)
        if window is None:
            return {"available": False, "reason": "insufficient_calendar_warmup", "rows": []}
        choices = [self.select_version(contract, d, "cleared_volume", context.cutoff_ns) for d in window]
        missing = [d for d, choice in zip(window, choices) if choice.observation is None]
        if missing:
            return {"available": False, "reason": "missing_interior_window_observation",
                    "missing_dates": missing, "rows": []}
        rows = [s.observation.view(context, selected=True, adv20_window_index=i) for i, s in enumerate(choices)]
        return {"available": True, "reason": None, "observations": 20,
                "window_start": window[0], "window_end": window[-1],
                "endpoint_lag_from_decision_sessions": context.decision_index - end,
                "endpoint_lag_from_interval_end_sessions": context.interval_index - end,
                "adv20_contracts": str(sum(s.observation.statistic_value for s in choices) / Decimal(20)),
                "rows": rows}

    def return_inputs(self, exposures: dict[str, dict[str, object]], return_date: str,
                      snapshot_end_exclusive: str):
        """Required price endpoints for a shared mask; computes no portfolio return."""
        context = self.calendar.decision(return_date)
        freeze = self.calendar.valuation_cutoff_ns(return_date, snapshot_end_exclusive)
        required = set()
        for positions in exposures.values():
            for contract, raw_quantity in positions.items():
                try:
                    quantity = Decimal(str(raw_quantity))
                    if not quantity.is_finite() or quantity < 0:
                        raise InvalidOperation
                except InvalidOperation as exc:
                    raise ContractError("Exposure must be finite and non-negative") from exc
                if quantity != 0:
                    required.add(contract)
        missing, endpoints = [], {}
        for contract in sorted(required):
            pair = [self.select_version(contract, d, "settlement", freeze)
                    for d in (context.decision_date, return_date)]
            for d, s in zip((context.decision_date, return_date), pair):
                if s.observation is None:
                    missing.append({"contract": contract, "date": d, "reason": s.reason})
            if all(s.observation is not None for s in pair):
                endpoints[contract] = tuple(pair)
        return {"common_valid": not missing, "return_date": return_date,
                "previous_session": context.decision_date, "decision_cutoff": context.cutoff_utc,
                "missing_settlements": missing, "price_endpoints": None if missing else endpoints}
