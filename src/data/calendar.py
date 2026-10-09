"""Explicit session and cut-off inputs; no inferred business-day calendar."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from types import MappingProxyType

from src.validation.schemas import ContractError, utc_ns


@dataclass(frozen=True)
class TradingSession:
    trading_date: str
    settlement_at: str
    decision_cutoff: str

    def __post_init__(self):
        date.fromisoformat(self.trading_date)
        if utc_ns(self.decision_cutoff) > utc_ns(self.settlement_at):
            raise ContractError("Decision must not follow the interval's starting settlement")


@dataclass(frozen=True)
class Decision:
    return_date: str
    interval_index: int
    decision_date: str
    decision_index: int
    cutoff_utc: str
    calendar_id: str

    @property
    def cutoff_ns(self):
        return utc_ns(self.cutoff_utc)


class SessionCalendar:
    """Every session supplies its own verified settlement time and decision cut-off.

    Fixture times are synthetic. Production shortened-session schedules remain a
    Phase B data prerequisite; there is deliberately no universal 13:15 default.
    """
    def __init__(self, records):
        records = tuple(records)
        dates = [r.trading_date for r in records]
        if len(records) < 2 or dates != sorted(set(dates)):
            raise ContractError("At least two ordered, unique sessions required")
        for prior, current in zip(records, records[1:]):
            if utc_ns(current.settlement_at) <= utc_ns(prior.settlement_at) or utc_ns(current.decision_cutoff) <= utc_ns(prior.decision_cutoff):
                raise ContractError("Session timestamps must be strictly increasing")
        self.dates = tuple(dates)
        self.indices = MappingProxyType({d: i for i, d in enumerate(dates)})
        self.sessions = MappingProxyType({r.trading_date: r for r in records})
        self.calendar_id = hashlib.sha256(json.dumps([r.__dict__ for r in records], sort_keys=True).encode()).hexdigest()

    def decision(self, return_date):
        if return_date not in self.indices or self.indices[return_date] == 0:
            raise ContractError("Return date requires an explicit preceding session")
        i = self.indices[return_date]
        prior = self.dates[i - 1]
        return Decision(return_date, i, prior, i - 1, self.sessions[prior].decision_cutoff, self.calendar_id)

    def window20(self, end_index):
        if end_index < 19:
            return None
        if end_index >= len(self.dates):
            raise ContractError("Window endpoint lies outside the calendar")
        return self.dates[end_index - 19:end_index + 1]

    def valuation_cutoff_ns(self, return_date, snapshot_end_exclusive):
        if return_date not in self.sessions:
            raise ContractError("Unknown return session")
        cutoff = utc_ns(snapshot_end_exclusive) - 1
        if cutoff < utc_ns(self.sessions[return_date].settlement_at):
            raise ContractError("Valuation snapshot precedes the completed interval")
        return cutoff
