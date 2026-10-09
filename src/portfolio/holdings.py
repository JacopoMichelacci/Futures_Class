"""Canonical long-form holdings supplied by downstream engines, not generated here."""
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from src.validation.schemas import ContractError, HOLDINGS_COLUMNS, decimal_value, utc_ns


@dataclass(frozen=True)
class Holding:
    date: str  # Return interval end; quantities were fixed on the preceding session.
    strategy: str
    commodity: str
    contract: str
    decision_cutoff: str
    formed_at: str
    inputs_as_of: str
    benchmark_fractional_contracts: object | None = None
    implemented_contracts: object | None = None
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        quantities = (self.benchmark_fractional_contracts, self.implemented_contracts)
        if all(q is None for q in quantities):
            raise ContractError("A benchmark or implementation quantity is required")
        if not self.strategy:
            raise ContractError("Strategy identifier is required")
        for name in ("benchmark_fractional_contracts", "implemented_contracts"):
            value = getattr(self, name)
            if value is not None:
                value = decimal_value(value, name)
                if value < 0 or (name == "implemented_contracts" and value != value.to_integral_value()):
                    raise ContractError("Implementation quantities must be non-negative integers")
                object.__setattr__(self, name, value)
        if not utc_ns(self.inputs_as_of) <= utc_ns(self.formed_at) <= utc_ns(self.decision_cutoff):
            raise ContractError("Holdings must be formed from inputs known by the decision cut-off")
        core = set(self.__dataclass_fields__) - {"details"}
        if core.intersection(self.details) or set(self.details) - set(HOLDINGS_COLUMNS):
            raise ContractError("Descriptive fields cannot override holdings provenance")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    @property
    def quantity(self) -> Decimal:
        # An implementation row may retain the benchmark quantity as a reference.
        return self.implemented_contracts if self.implemented_contracts is not None else self.benchmark_fractional_contracts

    def to_record(self):
        record = {name: None for name in HOLDINGS_COLUMNS}
        record.update(self.details)
        record.update({name: getattr(self, name) for name in self.__dataclass_fields__ if name != "details"})
        return record


class HoldingsBook:
    """Requires explicit snapshots, including explicit zero holdings for a flat book."""
    def __init__(self, rows, calendar, catalogue, benchmark_strategy="benchmark"):
        self.calendar = calendar
        self.catalogue = MappingProxyType(dict(catalogue))
        self.benchmark_strategy = benchmark_strategy
        self.rows = tuple(rows)
        self.snapshots = {}
        seen = set()
        for row in self.rows:
            context = calendar.decision(row.date)
            if row.decision_cutoff != context.cutoff_utc:
                raise ContractError("Holdings cut-off does not match the supplied session calendar")
            definition = catalogue.get(row.contract)
            if definition is None or definition.identity.root != row.commodity:
                raise ContractError("Holdings identity does not match the contract catalogue")
            is_benchmark = row.strategy == benchmark_strategy
            if ((is_benchmark and (row.benchmark_fractional_contracts is None or row.implemented_contracts is not None))
                    or (not is_benchmark and row.implemented_contracts is None)):
                raise ContractError("Benchmark and implemented quantity fields are inconsistent")
            key = (row.date, row.strategy, row.commodity, row.contract)
            if key in seen:
                raise ContractError("Duplicate long-form holdings row")
            seen.add(key)
            cap = row.details.get("capacity_limit_contracts")
            if cap is not None and not is_benchmark:
                cap = decimal_value(cap)
                if cap < 0 or cap != cap.to_integral_value() or row.quantity > cap:
                    raise ContractError("Holding violates the supplied non-negative integer capacity ceiling")
            self.snapshots.setdefault((row.date, row.strategy), {})[row.contract] = row.quantity
        self.snapshots = MappingProxyType({key: MappingProxyType(value) for key, value in self.snapshots.items()})

    def snapshot(self, date, strategy):
        return self.snapshots.get((date, strategy))

    def to_records(self):
        return [r.to_record() for r in self.rows]
