"""Capacity limits and fractional residual allocation before integer rounding.

Capacity caps contract quantities using strictly lagged ADV20. Residual target
notional is allocated to later eligible contracts using each contract's own
publication-safe decision-time dollar value. This module never rounds holdings.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from src.contracts.eligibility import is_eligible
from src.data.asof import ObservationStore
from src.data.calendar import Decision
from src.validation.schemas import ContractError, decimal_value, utc_ns


class CapacityMethod(str, Enum):
    """The baseline plus three frozen residual-handling alternatives."""

    HARD_TRUNCATE = "hard_truncate"
    OVERFLOW_NEXT = "overflow_next"
    OVERFLOW_SEQUENTIAL = "overflow_sequential_two"
    OVERFLOW_ADV_WEIGHTED = "overflow_adv_weighted"


@dataclass(frozen=True)
class ADV20Result:
    """Availability and provenance for one strictly lagged ADV20 value."""

    contract: str
    available: bool
    value: Decimal | None
    reason: str | None
    window_start: str | None
    window_end: str | None
    observations: int
    source_dates: tuple[str, ...]


@dataclass(frozen=True)
class ContractValueResult:
    """A decision-time dollar value per contract and its settlement source."""

    contract: str
    available: bool
    value: Decimal | None
    settlement: Decimal | None
    settlement_date: str | None
    publication_timestamp: str | None
    reason: str | None


@dataclass(frozen=True)
class PrimaryCap:
    """Fractional requested/capped quantities and dollar residual."""

    requested_contracts: Decimal
    capacity_limit_contracts: int
    implemented_contracts: Decimal
    residual_contracts: Decimal
    dollar_contract_notional: Decimal
    requested_notional: Decimal
    implemented_notional: Decimal
    residual_notional: Decimal

    @property
    def capacity_binds(self) -> bool:
        return self.residual_notional > 0


@dataclass(frozen=True)
class OverflowAllocation:
    """Fractional contract allocations and any unsupported residual notional."""

    allocations: Mapping[str, Decimal]
    demand_contracts: Mapping[str, Decimal]
    demand_notional: Mapping[str, Decimal]
    unimplemented_notional: Decimal
    flags: tuple[str, ...]


@dataclass(frozen=True)
class CapacityRow:
    """Fractional output passed to a downstream, separate rounding stage."""

    date: str
    strategy: str
    commodity: str
    contract: str
    target_weight: Decimal | None
    target_notional: Decimal | None
    pre_capacity_contracts: Decimal
    post_capacity_fractional_contracts: Decimal
    capacity_limit_contracts: int | None
    dollar_contract_notional: Decimal | None
    post_capacity_notional: Decimal
    maturity_rank: int | None
    days_to_anchor: int | None
    decision_cutoff: str
    formed_at: str
    inputs_as_of: str
    flags: tuple[str, ...]

    def to_record(self) -> dict:
        return {
            "date": self.date, "strategy": self.strategy,
            "commodity": self.commodity, "contract": self.contract,
            "target_weight": self.target_weight, "target_notional": self.target_notional,
            "pre_capacity_contracts": self.pre_capacity_contracts,
            "post_capacity_fractional_contracts": self.post_capacity_fractional_contracts,
            "capacity_limit_contracts": self.capacity_limit_contracts,
            "dollar_contract_notional": self.dollar_contract_notional,
            "post_capacity_notional": self.post_capacity_notional,
            "maturity_rank": self.maturity_rank, "days_to_anchor": self.days_to_anchor,
            "decision_cutoff": self.decision_cutoff, "formed_at": self.formed_at,
            "inputs_as_of": self.inputs_as_of, "flags": ";".join(self.flags) or None,
        }


@dataclass(frozen=True)
class CapacityPortfolio:
    """Fractional capacity rows and auditable diagnostics; no rounding applied."""

    method: CapacityMethod
    rows: tuple[CapacityRow, ...]
    diagnostics: tuple[dict, ...]


def _nonnegative_integer(value, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractError(f"{name} must be a non-negative integer")
    return value


def _positive_integer(value, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ContractError(f"{name} must be a positive integer")
    return value


def _normalise_method(method: CapacityMethod | str) -> CapacityMethod:
    try:
        return method if isinstance(method, CapacityMethod) else CapacityMethod(method)
    except ValueError as exc:
        raise ContractError(f"Unknown capacity method: {method}") from exc


def _validate_candidates(candidates: Sequence[str]) -> tuple[str, ...]:
    values = tuple(candidates)
    if any(not isinstance(value, str) or not value for value in values):
        raise ContractError("Overflow candidates must be non-empty contract identifiers")
    if len(values) != len(set(values)):
        raise ContractError("Overflow candidates must be unique")
    return values


def compute_adv20(observations: ObservationStore, contract: str,
                  context: Decision) -> ADV20Result:
    """Read exactly 20 prior verified sessions from the shared as-of store."""
    if not isinstance(observations, ObservationStore):
        raise ContractError("compute_adv20 requires the shared ObservationStore")
    if not isinstance(contract, str) or contract not in observations.identities:
        raise ContractError("ADV20 requires a known exact contract identity")
    observations.validate_context(context)
    result = observations.adv20(contract, context)
    if not result.get("available"):
        return ADV20Result(contract, False, None, result.get("reason"),
                           result.get("window_start"), result.get("window_end"),
                           int(result.get("observations", 0)), ())
    value = decimal_value(result.get("adv20_contracts"), "ADV20")
    if value < 0:
        raise ContractError("ADV20 cannot be negative")
    rows = tuple(result.get("rows", ()))
    source_dates = tuple(row["trading_date"] for row in rows)
    if len(rows) != 20 or len(set(source_dates)) != 20:
        raise ContractError("Available ADV20 must contain 20 distinct sessions")
    if any(row["session_index"] >= context.decision_index for row in rows):
        raise ContractError("ADV20 must exclude the decision session and later sessions")
    return ADV20Result(contract, True, value, None, result.get("window_start"),
                       result.get("window_end"), 20, source_dates)


def decision_time_dollar_contract_value(observations: ObservationStore, catalogue: Mapping,
                                        contract: str, context: Decision) -> ContractValueResult:
    """Use the latest valid settlement published by the decision cut-off.

    The source settlement is in the shared quote unit; multiplying by the
    catalogue's verified ``dollars_per_price_unit`` gives USD per contract.
    """
    if not isinstance(observations, ObservationStore):
        raise ContractError("Contract value requires the shared ObservationStore")
    definition = catalogue.get(contract)
    if definition is None or definition.identity.contract != contract:
        raise ContractError("Contract value requires a known exact catalogue identity")
    observations.validate_context(context)
    selection = observations.latest_available(contract, "settlement", context)
    if selection.observation is None:
        return ContractValueResult(contract, False, None, None, None, None, selection.reason)
    settlement = selection.observation.statistic_value
    if settlement is None or settlement <= 0:
        return ContractValueResult(contract, False, None, settlement,
                                   selection.observation.trading_date,
                                   selection.observation.publication_timestamp,
                                   "non_positive_or_missing_settlement")
    value = settlement * decimal_value(definition.dollars_per_price_unit,
                                       "dollars_per_price_unit")
    if value <= 0:
        raise ContractError("Decision-time dollar contract value must be positive")
    return ContractValueResult(contract, True, value, settlement,
                               selection.observation.trading_date,
                               selection.observation.publication_timestamp, None)


def integer_capacity_limit(adv20_contracts, participation_rate=Decimal("0.025"),
                           liquidation_horizon_days=1) -> int | None:
    """Return floor(participation * lagged ADV20 * horizon); None means unavailable."""
    if adv20_contracts is None:
        return None
    adv = decimal_value(adv20_contracts, "ADV20")
    rate = decimal_value(participation_rate, "participation_rate")
    if adv < 0 or rate <= 0:
        raise ContractError("ADV20 must be non-negative and participation rate positive")
    horizon = _nonnegative_integer(liquidation_horizon_days, "liquidation_horizon_days")
    if horizon == 0:
        raise ContractError("Liquidation horizon must be positive")
    return int((adv * rate * horizon).to_integral_value(rounding=ROUND_FLOOR))


def apply_primary_cap(pre_capacity_contracts, capacity_limit_contracts: int,
                      dollar_contract_notional, requested_notional=None) -> PrimaryCap:
    """Apply a per-contract ceiling without rounding fractional target quantity."""
    requested = decimal_value(pre_capacity_contracts, "pre_capacity_contracts")
    limit = _nonnegative_integer(capacity_limit_contracts, "capacity_limit_contracts")
    contract_value = decimal_value(dollar_contract_notional, "dollar_contract_notional")
    if requested < 0 or contract_value <= 0:
        raise ContractError("Target quantity must be non-negative and contract value positive")
    implemented = min(requested, Decimal(limit))
    residual = requested - implemented
    requested_dollars = (requested * contract_value if requested_notional is None
                         else decimal_value(requested_notional, "requested_notional"))
    if requested_dollars < 0:
        raise ContractError("Requested notional must be non-negative")
    implemented_notional = implemented * contract_value
    residual_notional = (max(requested_dollars - implemented_notional, Decimal(0))
                         if residual > 0 else Decimal(0))
    return PrimaryCap(requested, limit, implemented, residual, contract_value,
                      requested_dollars, implemented_notional, residual_notional)


def deferred_overflow_candidates(primary_contract: str, eligible_curve: Iterable[dict],
                                  catalogue: Mapping, trading_date: str) -> tuple[str, ...]:
    """Validate a complete date curve and return deferred eligible maturities.

    Every catalogue contract whose explicit activation/expiration range covers
    the date must have a curve row. A missing row is not treated as an
    ineligible contract.
    """
    primary = catalogue.get(primary_contract)
    if primary is None or primary.identity.contract != primary_contract:
        raise ContractError("Primary contract is missing or mismatched in the catalogue")
    if not is_eligible(primary, trading_date):
        raise ContractError("Primary contract is not eligible on the decision date")
    candidates = []
    seen = set()
    for row in eligible_curve:
        if row.get("date") != trading_date or row.get("commodity") != primary.identity.root:
            raise ContractError("Overflow curve must contain rows for one date and primary commodity")
        symbol = row.get("contract")
        contract = catalogue.get(symbol)
        if (contract is None or contract.identity.contract != symbol
                or contract.identity.root != primary.identity.root):
            raise ContractError("Overflow curve identity does not match its contract catalogue")
        if symbol in seen:
            raise ContractError("Duplicate contract in overflow curve")
        seen.add(symbol)
        eligible = row.get("eligible")
        if type(eligible) is not bool:
            raise ContractError("Curve eligibility must be explicit")
        if row.get("anchor") != contract.anchor:
            raise ContractError("Curve anchor does not match its contract catalogue")
        if eligible and not is_eligible(contract, trading_date):
            raise ContractError("Curve marks an inactive or expired contract eligible")
        if symbol == primary_contract:
            if not eligible:
                raise ContractError("Primary contract is not eligible in the supplied curve")
            continue
        if eligible and contract.anchor > primary.anchor:
            candidates.append(symbol)
    if primary_contract not in seen:
        raise ContractError("Primary contract is absent from the supplied eligible curve")
    expected = {
        symbol for symbol, contract in catalogue.items()
        if contract.identity.root == primary.identity.root
        and contract.activation <= trading_date <= contract.expiration
    }
    missing = expected - seen
    unexpected = seen - expected
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing contracts: {', '.join(sorted(missing))}")
        if unexpected:
            details.append(f"contracts outside active catalogue range: {', '.join(sorted(unexpected))}")
        raise ContractError("Decision-date eligible curve is incomplete (" + "; ".join(details) + ")")
    return tuple(sorted(candidates, key=lambda symbol: (catalogue[symbol].anchor, symbol)))


def _valid_candidate(contract: str, capacity_limits: Mapping[str, int | None],
                     contract_values: Mapping[str, Decimal | None]) -> tuple[int, Decimal] | None:
    cap = capacity_limits.get(contract)
    value = contract_values.get(contract)
    if cap is None or value is None:
        return None
    cap = _nonnegative_integer(cap, f"capacity[{contract}]")
    value = decimal_value(value, f"dollar_contract_notional[{contract}]")
    if cap == 0 or value <= 0:
        return None
    return cap, value


def _overflow_result(allocations: dict[str, Decimal], demands: dict[str, Decimal],
                     notionals: dict[str, Decimal], residual: Decimal,
                     flags: Iterable[str]) -> OverflowAllocation:
    return OverflowAllocation(MappingProxyType(allocations), MappingProxyType(demands),
                              MappingProxyType(notionals), residual, tuple(flags))


def allocate_overflow_next(residual_notional, candidates: Sequence[str],
                           capacity_limits: Mapping[str, int | None],
                           contract_values: Mapping[str, Decimal | None]) -> OverflowAllocation:
    """Send dollar residual to only the nearest deferred contract up to its cap."""
    residual = decimal_value(residual_notional, "residual_notional")
    if residual < 0:
        raise ContractError("Residual notional must be non-negative")
    candidates = _validate_candidates(candidates)
    if residual == 0:
        return _overflow_result({}, {}, {}, Decimal(0), ())
    if not candidates:
        return _overflow_result({}, {}, {}, residual, ("no_eligible_deferred_candidate",))
    contract = candidates[0]
    valid = _valid_candidate(contract, capacity_limits, contract_values)
    if valid is None:
        return _overflow_result({contract: Decimal(0)}, {contract: Decimal(0)},
                                {contract: Decimal(0)}, residual,
                                ("no_valid_candidate_capacity_or_value", "overflow_residual_unimplemented"))
    cap, value = valid
    demand = residual / value
    amount = min(demand, Decimal(cap))
    notional = amount * value
    remaining = residual - notional
    flags = ("overflow_residual_unimplemented",) if remaining > 0 else ()
    return _overflow_result({contract: amount}, {contract: demand}, {contract: notional},
                            remaining, flags)


def allocate_overflow_sequential(residual_notional, candidates: Sequence[str],
                                 capacity_limits: Mapping[str, int | None],
                                 contract_values: Mapping[str, Decimal | None]) -> OverflowAllocation:
    """Fill the next two contracts in order, converting dollars at each value."""
    residual = decimal_value(residual_notional, "residual_notional")
    if residual < 0:
        raise ContractError("Residual notional must be non-negative")
    candidates = _validate_candidates(candidates)[:2]
    allocations: dict[str, Decimal] = {}
    demands: dict[str, Decimal] = {}
    notionals: dict[str, Decimal] = {}
    remaining = residual
    unavailable = False
    for contract in candidates:
        valid = _valid_candidate(contract, capacity_limits, contract_values)
        if valid is None:
            allocations[contract] = Decimal(0)
            demands[contract] = Decimal(0)
            notionals[contract] = Decimal(0)
            unavailable = True
            continue
        cap, value = valid
        demand = remaining / value
        amount = min(demand, Decimal(cap))
        notional = amount * value
        allocations[contract] = amount
        demands[contract] = demand
        notionals[contract] = notional
        remaining -= notional
    flags = []
    if not candidates:
        flags.append("no_eligible_deferred_candidate")
    if unavailable and remaining > 0:
        flags.append("candidate_capacity_or_value_unavailable")
    if remaining > 0:
        flags.append("overflow_residual_unimplemented")
    return _overflow_result(allocations, demands, notionals, remaining, flags)


def allocate_overflow_adv_weighted(residual_notional, candidates: Sequence[str],
                                   capacity_limits: Mapping[str, int | None],
                                   contract_values: Mapping[str, Decimal | None],
                                   adv20_by_contract: Mapping[str, Decimal | int | float | str | None]) -> OverflowAllocation:
    """ADV-weight dollar residual over two recipients, reallocating at dollar caps.

    Allocations remain fractional contract quantities. This function intentionally
    does no integer rounding; the separate fixed rounding stage receives these
    quantities and the per-contract capacity ceilings.
    """
    residual = decimal_value(residual_notional, "residual_notional")
    if residual < 0:
        raise ContractError("Residual notional must be non-negative")
    candidates = _validate_candidates(candidates)[:2]
    if residual == 0:
        return _overflow_result({}, {}, {}, Decimal(0), ())
    if not candidates:
        return _overflow_result({}, {}, {}, residual, ("no_eligible_deferred_candidate",))

    valid = []
    unavailable = False
    for index, contract in enumerate(candidates):
        raw_adv = adv20_by_contract.get(contract)
        recipient = _valid_candidate(contract, capacity_limits, contract_values)
        if raw_adv is None or recipient is None:
            unavailable = True
            continue
        adv = decimal_value(raw_adv, f"ADV20[{contract}]")
        if adv <= 0:
            unavailable = True
            continue
        cap, value = recipient
        valid.append((index, contract, adv, cap, value))

    allocations = {contract: Decimal(0) for contract in candidates}
    demands = {contract: Decimal(0) for contract in candidates}
    notionals = {contract: Decimal(0) for contract in candidates}
    if not valid:
        flags = ["no_valid_positive_adv20_capacity_or_value", "overflow_residual_unimplemented"]
        return _overflow_result(allocations, demands, notionals, residual, flags)

    remaining = residual
    active = list(valid)
    while remaining > 0 and active:
        total_weight = sum((entry[2] for entry in active), Decimal(0))
        quotas = {entry[1]: remaining * entry[2] / total_weight for entry in active}
        saturated = [entry for entry in active
                     if quotas[entry[1]] >= Decimal(entry[3]) * entry[4] - notionals[entry[1]]]
        if saturated:
            saturated_names = set()
            for _, contract, _, cap, value in saturated:
                room = Decimal(cap) * value - notionals[contract]
                room = min(room, remaining)
                notionals[contract] += room
                allocations[contract] = notionals[contract] / value
                demands[contract] += room / value
                remaining -= room
                saturated_names.add(contract)
            active = [entry for entry in active if entry[1] not in saturated_names]
            continue
        for _, contract, _, _, value in active:
            notional = quotas[contract]
            notionals[contract] += notional
            allocations[contract] += notional / value
            demands[contract] += notional / value
        remaining = Decimal(0)

    flags = []
    if unavailable:
        flags.append("candidate_adv20_capacity_or_value_unavailable")
    if remaining > 0:
        flags.append("overflow_residual_unimplemented")
    return _overflow_result(allocations, demands, notionals, remaining, flags)


def _strategy_name(base: str, method: CapacityMethod) -> str:
    return f"{base}:{method.value}"


def build_capacity_portfolio(targets: Iterable[dict], eligible_curve: Iterable[dict],
                             observations: ObservationStore, catalogue: Mapping,
                             method: CapacityMethod | str,
                             participation_rate=Decimal("0.025"),
                             liquidation_horizon_days=1) -> CapacityPortfolio:
    """Apply capacity to fractional target quantities; leave rounding downstream.

    A target supplies ``date``, ``commodity``, a primary contract and either
    its fractional pre-capacity quantity or target notional. When both quantity
    and notional are supplied, they must reconcile to the primary's decision-time
    dollar contract value.
    """
    if not isinstance(observations, ObservationStore):
        raise ContractError("Capacity construction requires the shared ObservationStore")
    method = _normalise_method(method)
    curves = tuple(eligible_curve)
    rows: list[CapacityRow] = []
    diagnostics: list[dict] = []
    seen_targets = set()

    for target in targets:
        date = target.get("date")
        context = observations.calendar.decision(date)
        commodity = target.get("commodity")
        primary_symbol = target.get("primary_contract", target.get("contract"))
        strategy_base = target.get("strategy", "capacity")
        key = (date, strategy_base, commodity)
        if key in seen_targets:
            raise ContractError("Duplicate date/strategy/commodity capacity target")
        seen_targets.add(key)
        primary = catalogue.get(primary_symbol)
        if (primary is None or primary.identity.contract != primary_symbol
                or primary.identity.root != commodity):
            raise ContractError("Primary target identity does not match the contract catalogue")

        curve_rows = [row for row in curves if row.get("date") == context.decision_date
                      and row.get("commodity") == commodity]
        candidates = deferred_overflow_candidates(primary_symbol, curve_rows, catalogue,
                                                  context.decision_date)
        primary_adv = compute_adv20(observations, primary_symbol, context)
        if not primary_adv.available or primary_adv.value is None:
            raise ContractError(f"Primary ADV20 unavailable for {primary_symbol}: {primary_adv.reason}")
        primary_limit = integer_capacity_limit(primary_adv.value, participation_rate,
                                               liquidation_horizon_days)
        primary_value = decision_time_dollar_contract_value(observations, catalogue,
                                                            primary_symbol, context)
        if not primary_value.available or primary_value.value is None:
            raise ContractError(f"Primary dollar contract value unavailable for {primary_symbol}: {primary_value.reason}")

        raw_quantity = target.get("pre_capacity_contracts",
                                  target.get("post_capacity_fractional_contracts"))
        raw_notional_value = target.get("target_notional")
        raw_notional = (decimal_value(raw_notional_value, "target_notional")
                        if raw_notional_value is not None else None)
        if raw_quantity is None and raw_notional is None:
            raise ContractError("Capacity target requires fractional quantity or target notional")
        requested = (decimal_value(raw_quantity, "pre_capacity_contracts")
                     if raw_quantity is not None
                     else raw_notional / primary_value.value)
        if requested < 0:
            raise ContractError("Target quantity must be non-negative")
        quantity_notional = requested * primary_value.value
        requested_notional = quantity_notional if raw_notional is None else raw_notional
        if (raw_quantity is not None and raw_notional is not None
                and abs(raw_notional - quantity_notional) > Decimal("0.01")):
            raise ContractError("Target notional must match fractional quantity times primary dollar contract value within one cent")

        primary_cap = apply_primary_cap(requested, primary_limit, primary_value.value,
                                       requested_notional=requested_notional)
        candidate_adv: dict[str, ADV20Result] = {}
        candidate_value: dict[str, ContractValueResult] = {}
        candidate_limits: dict[str, int | None] = {}
        if primary_cap.capacity_binds and method != CapacityMethod.HARD_TRUNCATE:
            considered = candidates[:1] if method == CapacityMethod.OVERFLOW_NEXT else candidates[:2]
            for symbol in considered:
                adv = compute_adv20(observations, symbol, context)
                value = decision_time_dollar_contract_value(observations, catalogue, symbol, context)
                candidate_adv[symbol] = adv
                candidate_value[symbol] = value
                candidate_limits[symbol] = (integer_capacity_limit(adv.value, participation_rate,
                                                                   liquidation_horizon_days)
                                            if adv.available else None)
        else:
            considered = ()

        values = {symbol: result.value if result.available else None
                  for symbol, result in candidate_value.items()}
        overflow = _overflow_result({}, {}, {}, primary_cap.residual_notional, ())
        if primary_cap.capacity_binds and method == CapacityMethod.OVERFLOW_NEXT:
            overflow = allocate_overflow_next(primary_cap.residual_notional, considered,
                                              candidate_limits, values)
        elif primary_cap.capacity_binds and method == CapacityMethod.OVERFLOW_SEQUENTIAL:
            overflow = allocate_overflow_sequential(primary_cap.residual_notional, considered,
                                                    candidate_limits, values)
        elif primary_cap.capacity_binds and method == CapacityMethod.OVERFLOW_ADV_WEIGHTED:
            overflow = allocate_overflow_adv_weighted(
                primary_cap.residual_notional, considered, candidate_limits, values,
                {symbol: result.value if result.available else None
                 for symbol, result in candidate_adv.items()},
            )

        quantities = {primary_symbol: primary_cap.implemented_contracts}
        requested_quantities = {primary_symbol: primary_cap.requested_contracts}
        values_by_contract = {primary_symbol: primary_value.value}
        limits = {primary_symbol: primary_limit}
        quantities.update(overflow.allocations)
        requested_quantities.update(overflow.demand_contracts)
        values_by_contract.update(values)
        limits.update({symbol: candidate_limits.get(symbol) for symbol in overflow.allocations})

        cutoff = context.cutoff_utc
        formed_at = target.get("formed_at", cutoff)
        target_inputs_asof = target.get("inputs_as_of", cutoff)
        if utc_ns(target_inputs_asof) > context.cutoff_ns or utc_ns(formed_at) > context.cutoff_ns:
            raise ContractError("Target inputs and formation time must be available by the decision cut-off")
        publication_times = [primary_value.publication_timestamp]
        used_advs = [primary_adv, *candidate_adv.values()]
        used_values = [primary_value, *candidate_value.values()]
        publication_times.extend(result.publication_timestamp for result in used_values
                                 if result.available and result.publication_timestamp)
        publication_times.extend(
            record["publication_timestamp"]
            for adv in used_advs if adv.available
            for record in observations.adv20(adv.contract, context).get("rows", [])
        )
        inputs_as_of = max([target_inputs_asof, *publication_times], key=utc_ns)
        base_flags = list(overflow.flags)
        if primary_cap.capacity_binds:
            base_flags.insert(0, "primary_capacity_binding")
        if overflow.unimplemented_notional > 0:
            base_flags.append(f"unimplemented_notional={overflow.unimplemented_notional}")

        strategy = _strategy_name(str(strategy_base), method)
        for symbol, quantity in quantities.items():
            if symbol != primary_symbol and quantity == 0:
                # Zero overflow rows may have no usable cap/value and are not
                # valid inputs to the downstream rounding stage.
                continue
            value = values_by_contract.get(symbol)
            curve_row = next((row for row in curve_rows if row["contract"] == symbol), {})
            if limits.get(symbol) is None:
                raise ContractError(f"Capacity output for {symbol} lacks an integer ceiling or maturity rank")
            capacity_limit = _nonnegative_integer(limits[symbol], f"capacity[{symbol}]")
            maturity_rank = _positive_integer(curve_row.get("maturity_rank"),
                                              f"maturity_rank[{symbol}]")
            flags = tuple(base_flags if symbol == primary_symbol else (
                ("candidate_capacity_or_value_unavailable",)
                if limits.get(symbol) is None or value is None else ()))
            rows.append(CapacityRow(
                date=date, strategy=strategy, commodity=commodity, contract=symbol,
                target_weight=(decimal_value(target["target_weight"], "target_weight")
                               if symbol == primary_symbol and target.get("target_weight") is not None else None),
                target_notional=(requested_notional if symbol == primary_symbol else None),
                pre_capacity_contracts=requested_quantities.get(symbol, Decimal(0)),
                post_capacity_fractional_contracts=quantity,
                capacity_limit_contracts=capacity_limit,
                dollar_contract_notional=value,
                post_capacity_notional=quantity * value if value is not None else Decimal(0),
                maturity_rank=maturity_rank,
                days_to_anchor=curve_row.get("days_to_anchor"),
                decision_cutoff=cutoff, formed_at=formed_at, inputs_as_of=inputs_as_of,
                flags=flags,
            ))
        diagnostics.append({
            "date": date, "strategy": strategy, "commodity": commodity,
            "method": method.value, "primary_contract": primary_symbol,
            "adv20_contracts": primary_adv.value,
            "primary_capacity_limit_contracts": primary_limit,
            "primary_dollar_contract_notional": primary_value.value,
            "requested_contracts": primary_cap.requested_contracts,
            "requested_notional": primary_cap.requested_notional,
            "primary_post_capacity_fractional_contracts": primary_cap.implemented_contracts,
            "primary_post_capacity_notional": primary_cap.implemented_notional,
            "primary_residual_notional": primary_cap.residual_notional,
            "overflow_allocations": dict(overflow.allocations),
            "overflow_notionals": dict(overflow.demand_notional),
            "unimplemented_notional": overflow.unimplemented_notional,
            "flags": tuple(base_flags),
        })
    return CapacityPortfolio(method, tuple(rows), tuple(diagnostics))
