"""Pure capacity rules and a deterministic adapter to shared Phase A inputs.

Capacities are integer contracts from strictly lagged ADV20. Targets are
expressed in primary-contract equivalents and rounded once, using the locked
nearest-integer convention, before capacity is applied. Deferred candidates
are ordered by maturity relative to the selected primary contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from src.contracts.eligibility import is_eligible
from src.data.asof import ObservationStore
from src.data.calendar import Decision
from src.portfolio.holdings import Holding
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
class PrimaryCap:
    """Rounded primary request, capped quantity and residual contracts."""

    requested_contracts: Decimal
    rounded_requested_contracts: int
    capacity_limit_contracts: int
    implemented_contracts: int
    residual_contracts: int

    @property
    def capacity_binds(self) -> bool:
        return self.residual_contracts > 0


@dataclass(frozen=True)
class OverflowAllocation:
    """Integer overflow allocations plus the requested amount per candidate."""

    allocations: Mapping[str, int]
    demand_contracts: Mapping[str, Decimal]
    unimplemented_contracts: int
    flags: tuple[str, ...]


@dataclass(frozen=True)
class CapacityPortfolio:
    """Long-form holding rows and auditable capacity diagnostics."""

    method: CapacityMethod
    rows: tuple[Holding, ...]
    diagnostics: tuple[dict, ...]


def _nonnegative_integer(value, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractError(f"{name} must be a non-negative integer")
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
    """Read ADV20 from the shared as-of store, excluding the decision session.

    ``context`` is the already validated decision for the return interval. The
    underlying store requires all 20 consecutive verified sessions before its
    decision session, so current or late-published volume cannot leak in.
    """
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
        raise ContractError("ADV20 must exclude the decision session and all later sessions")
    return ADV20Result(contract, True, value, None, result.get("window_start"),
                       result.get("window_end"), 20, source_dates)


def integer_capacity_limit(adv20_contracts, participation_rate=Decimal("0.025"),
                           liquidation_horizon_days=1) -> int | None:
    """Return ``floor(participation * ADV20 * horizon)``; None means unavailable."""
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


def apply_primary_cap(pre_capacity_contracts, capacity_limit_contracts: int) -> PrimaryCap:
    """Round to nearest whole contract, cap the primary and expose residual."""
    requested = decimal_value(pre_capacity_contracts, "pre_capacity_contracts")
    limit = _nonnegative_integer(capacity_limit_contracts, "capacity_limit_contracts")
    if requested < 0:
        raise ContractError("Target contract quantity must be non-negative")
    rounded = int(requested.to_integral_value(rounding=ROUND_HALF_UP))
    implemented = min(rounded, limit)
    return PrimaryCap(requested, rounded, limit, implemented, rounded - implemented)


def deferred_overflow_candidates(primary_contract: str, eligible_curve: Iterable[dict],
                                 catalogue: Mapping, trading_date: str) -> tuple[str, ...]:
    """Return eligible maturities after primary, nearest deferred first.

    Curve rows must describe one commodity and one decision date. The order is
    derived from explicit catalogue anchors, not a global nearby rank.
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
        if contract is None or contract.identity.contract != symbol or contract.identity.root != primary.identity.root:
            raise ContractError("Overflow curve identity does not match its contract catalogue")
        if symbol in seen:
            raise ContractError("Duplicate contract in overflow curve")
        seen.add(symbol)
        eligible = row.get("eligible")
        if type(eligible) is not bool:
            raise ContractError("Curve eligibility must be explicit")
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
    return tuple(sorted(candidates, key=lambda symbol: (catalogue[symbol].anchor, symbol)))


def _empty_overflow(residual: int) -> OverflowAllocation:
    return OverflowAllocation(MappingProxyType({}), MappingProxyType({}), residual, ())


def allocate_overflow_next(residual_contracts: int, candidates: Sequence[str],
                           capacity_limits: Mapping[str, int | None]) -> OverflowAllocation:
    """Put residual in the nearest deferred contract up to its own cap."""
    residual = _nonnegative_integer(residual_contracts, "residual_contracts")
    candidates = _validate_candidates(candidates)
    if residual == 0:
        return _empty_overflow(0)
    if not candidates:
        return OverflowAllocation(MappingProxyType({}), MappingProxyType({}), residual,
                                  ("no_eligible_deferred_candidate",))
    contract = candidates[0]
    cap = capacity_limits.get(contract)
    if cap is None:
        return OverflowAllocation(MappingProxyType({contract: 0}),
                                  MappingProxyType({contract: Decimal(residual)}), residual,
                                  ("no_valid_candidate_capacity",))
    cap = _nonnegative_integer(cap, f"capacity[{contract}]")
    filled = min(residual, cap)
    flags = ("overflow_residual_unimplemented",) if filled < residual else ()
    return OverflowAllocation(MappingProxyType({contract: filled}),
                              MappingProxyType({contract: Decimal(residual)}),
                              residual - filled, flags)


def allocate_overflow_sequential(residual_contracts: int, candidates: Sequence[str],
                                 capacity_limits: Mapping[str, int | None]) -> OverflowAllocation:
    """Fill the first and then second deferred candidates, respecting each cap."""
    residual = _nonnegative_integer(residual_contracts, "residual_contracts")
    candidates = _validate_candidates(candidates)[:2]
    if residual == 0:
        return _empty_overflow(0)
    allocation, demand = {}, {}
    remaining = residual
    for contract in candidates:
        demand[contract] = Decimal(remaining)
        cap = capacity_limits.get(contract)
        if cap is None:
            allocation[contract] = 0
            continue
        cap = _nonnegative_integer(cap, f"capacity[{contract}]")
        amount = min(remaining, cap)
        allocation[contract] = amount
        remaining -= amount
    if not candidates:
        flags = ("no_eligible_deferred_candidate",)
    elif remaining:
        flags = ("overflow_residual_unimplemented",)
        if not any(capacity_limits.get(c) is not None and capacity_limits[c] > 0 for c in candidates):
            flags = ("no_valid_candidate_capacity", "overflow_residual_unimplemented")
    else:
        flags = ()
    return OverflowAllocation(MappingProxyType(allocation), MappingProxyType(demand), remaining, flags)


def allocate_overflow_adv_weighted(residual_contracts: int, candidates: Sequence[str],
                                   capacity_limits: Mapping[str, int | None],
                                   adv20_by_contract: Mapping[str, Decimal | int | float | str | None]) -> OverflowAllocation:
    """Split overflow over two contracts by ADV20, reflowing around saturated caps.

    Integer remainders use largest fractional quota, with deferred order as the
    stable tie break. Candidates with missing/non-positive ADV20 or no capacity
    are skipped; unfilled target is reported and flagged.
    """
    residual = _nonnegative_integer(residual_contracts, "residual_contracts")
    candidates = _validate_candidates(candidates)[:2]
    if residual == 0:
        return _empty_overflow(0)
    valid = []
    invalid_adv = []
    for index, contract in enumerate(candidates):
        raw_adv = adv20_by_contract.get(contract)
        if raw_adv is None:
            invalid_adv.append(contract)
            continue
        adv = decimal_value(raw_adv, f"ADV20[{contract}]")
        if adv <= 0:
            invalid_adv.append(contract)
            continue
        cap = capacity_limits.get(contract)
        if cap is None:
            continue
        cap = _nonnegative_integer(cap, f"capacity[{contract}]")
        if cap > 0:
            valid.append((index, contract, adv, cap))

    allocation = {contract: 0 for contract in candidates}
    demand = {contract: Decimal(0) for contract in candidates}
    if not valid:
        if not candidates:
            flags = ("no_eligible_deferred_candidate",)
        else:
            flags = ("no_valid_positive_adv20_capacity", "overflow_residual_unimplemented")
        return OverflowAllocation(MappingProxyType(allocation), MappingProxyType(demand), residual, flags)

    remaining = residual
    active = list(valid)
    while remaining > 0 and active:
        total_weight = sum((entry[2] for entry in active), Decimal(0))
        quotas = {entry[1]: Decimal(remaining) * entry[2] / total_weight for entry in active}
        saturated = [entry for entry in active
                     if quotas[entry[1]] >= Decimal(entry[3] - allocation[entry[1]])]
        if saturated:
            for entry in saturated:
                _, contract, _, cap = entry
                room = cap - allocation[contract]
                allocation[contract] += room
                demand[contract] += Decimal(room)
                remaining -= room
            saturated_names = {entry[1] for entry in saturated}
            active = [entry for entry in active if entry[1] not in saturated_names]
            continue

        floors = {}
        for _, contract, _, _ in active:
            quota = quotas[contract]
            floors[contract] = int(quota.to_integral_value(rounding=ROUND_FLOOR))
            demand[contract] += quota
            allocation[contract] += floors[contract]
        left = remaining - sum(floors.values())
        ranked = sorted(active, key=lambda entry: (-(quotas[entry[1]] - floors[entry[1]]), entry[0]))
        for _, contract, _, cap in ranked:
            if left == 0:
                break
            if allocation[contract] < cap:
                allocation[contract] += 1
                left -= 1
        remaining = left
        break

    flags = []
    if invalid_adv:
        flags.append("candidate_adv20_unavailable")
    if remaining:
        flags.append("overflow_residual_unimplemented")
    return OverflowAllocation(MappingProxyType(allocation), MappingProxyType(demand), remaining, tuple(flags))


def _strategy_name(base: str, method: CapacityMethod) -> str:
    return f"{base}:{method.value}"


def build_capacity_portfolio(targets: Iterable[dict], eligible_curve: Iterable[dict],
                             observations: ObservationStore, catalogue: Mapping,
                             method: CapacityMethod | str,
                             participation_rate=Decimal("0.025"),
                             liquidation_horizon_days=1) -> CapacityPortfolio:
    """Build validated long-form holdings from explicit primary target rows.

    Each target has ``date`` (the return-interval end), ``commodity``,
    ``primary_contract`` (or ``contract``), and ``pre_capacity_contracts``
    (or ``post_capacity_fractional_contracts``). Curves are keyed to the
    preceding decision session. Deferred demand is measured in primary-contract
    equivalents, keeping the locked capacity experiment isolated from contract
    selection, roll timing, target weights and rounding methodology.
    """
    if not isinstance(observations, ObservationStore):
        raise ContractError("Capacity construction requires the shared ObservationStore")
    method = _normalise_method(method)
    curves = tuple(eligible_curve)
    rows: list[Holding] = []
    diagnostics: list[dict] = []
    seen_targets = set()

    for target in targets:
        date = target.get("date")
        context = observations.calendar.decision(date)
        commodity = target.get("commodity")
        primary_symbol = target.get("primary_contract", target.get("contract"))
        primary_quantity = target.get("pre_capacity_contracts", target.get("post_capacity_fractional_contracts"))
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
        candidates = deferred_overflow_candidates(primary_symbol, curve_rows, catalogue, context.decision_date)
        primary_adv = compute_adv20(observations, primary_symbol, context)
        if not primary_adv.available or primary_adv.value is None:
            raise ContractError(f"Primary ADV20 unavailable for {primary_symbol}: {primary_adv.reason}")
        primary_limit = integer_capacity_limit(primary_adv.value, participation_rate, liquidation_horizon_days)
        if primary_limit is None:
            raise ContractError("Primary integer capacity is unavailable")
        primary_cap = apply_primary_cap(primary_quantity, primary_limit)

        candidate_adv: dict[str, ADV20Result] = {}
        candidate_limits: dict[str, int | None] = {}
        if primary_cap.capacity_binds and method != CapacityMethod.HARD_TRUNCATE:
            considered = candidates[:1] if method == CapacityMethod.OVERFLOW_NEXT else candidates[:2]
            for symbol in considered:
                result = compute_adv20(observations, symbol, context)
                candidate_adv[symbol] = result
                candidate_limits[symbol] = (integer_capacity_limit(result.value, participation_rate,
                                                                   liquidation_horizon_days)
                                            if result.available else None)
        else:
            considered = ()

        overflow = _empty_overflow(primary_cap.residual_contracts)
        if primary_cap.capacity_binds and method == CapacityMethod.OVERFLOW_NEXT:
            overflow = allocate_overflow_next(primary_cap.residual_contracts, considered, candidate_limits)
        elif primary_cap.capacity_binds and method == CapacityMethod.OVERFLOW_SEQUENTIAL:
            overflow = allocate_overflow_sequential(primary_cap.residual_contracts, considered, candidate_limits)
        elif primary_cap.capacity_binds and method == CapacityMethod.OVERFLOW_ADV_WEIGHTED:
            overflow = allocate_overflow_adv_weighted(
                primary_cap.residual_contracts, considered, candidate_limits,
                {symbol: result.value if result.available else None for symbol, result in candidate_adv.items()},
            )

        allocation = {primary_symbol: primary_cap.implemented_contracts}
        demand = {primary_symbol: Decimal(primary_cap.rounded_requested_contracts)}
        capacity_limits = {primary_symbol: primary_limit}
        allocation.update(overflow.allocations)
        demand.update(overflow.demand_contracts)
        capacity_limits.update({symbol: candidate_limits.get(symbol) for symbol in overflow.allocations})

        cutoff = context.cutoff_utc
        formed_at = target.get("formed_at", cutoff)
        target_inputs_asof = target.get("inputs_as_of", cutoff)
        if utc_ns(target_inputs_asof) > context.cutoff_ns or utc_ns(formed_at) > context.cutoff_ns:
            raise ContractError("Target inputs and formation time must be available by the decision cut-off")
        used_observations = [primary_adv, *candidate_adv.values()]
        publications = [row["publication_timestamp"] for adv in used_observations if adv.available
                        for row in observations.adv20(adv.contract, context).get("rows", [])]
        inputs_as_of = max([target_inputs_asof, *publications], key=utc_ns)
        flags = list(overflow.flags)
        if primary_cap.capacity_binds:
            flags.insert(0, "primary_capacity_binding")
        if overflow.unimplemented_contracts:
            flags.append(f"unimplemented_contracts={overflow.unimplemented_contracts}")

        strategy = _strategy_name(str(strategy_base), method)
        for symbol, quantity in allocation.items():
            contract = catalogue[symbol]
            curve_row = next((r for r in curve_rows if r["contract"] == symbol), {})
            row_flags = list(flags if symbol == primary_symbol else ())
            if symbol != primary_symbol and capacity_limits.get(symbol) is None:
                row_flags.append("candidate_capacity_unavailable")
            details = {
                "target_weight": target.get("target_weight") if symbol == primary_symbol else None,
                "target_notional": target.get("target_notional") if symbol == primary_symbol else None,
                "pre_capacity_contracts": (decimal_value(primary_quantity) if symbol == primary_symbol
                                           else demand.get(symbol, Decimal(0))),
                "capacity_limit_contracts": capacity_limits.get(symbol),
                "implemented_notional": None,
                "maturity_rank": curve_row.get("maturity_rank"),
                "days_to_anchor": curve_row.get("days_to_anchor"),
                "flags": ";".join(row_flags) if row_flags else None,
            }
            rows.append(Holding(
                date=date, strategy=strategy, commodity=commodity, contract=symbol,
                decision_cutoff=cutoff, formed_at=formed_at, inputs_as_of=inputs_as_of,
                implemented_contracts=quantity, details=details,
            ))
        diagnostics.append({
            "date": date, "strategy": strategy, "commodity": commodity,
            "method": method.value, "primary_contract": primary_symbol,
            "adv20_contracts": primary_adv.value,
            "primary_capacity_limit_contracts": primary_limit,
            "requested_contracts": primary_cap.requested_contracts,
            "rounded_requested_contracts": primary_cap.rounded_requested_contracts,
            "primary_implemented_contracts": primary_cap.implemented_contracts,
            "overflow_allocations": dict(overflow.allocations),
            "unimplemented_contracts": overflow.unimplemented_contracts,
            "flags": tuple(flags),
        })
    return CapacityPortfolio(method, tuple(rows), tuple(diagnostics))
