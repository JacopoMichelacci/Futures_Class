"""Integer rounding of already selected, post-capacity fractional quantities.

The greedy construction objective is squared dollar-notional deviation, not a
ranking metric. Historical comparisons must use shared daily Tracking RMSE.
"""
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType

from src.portfolio.holdings import Holding, HoldingsBook
from src.validation.schemas import ContractError, ROOTS, decimal_value


def _quantity_and_cap(quantity, capacity_limit):
    q = decimal_value(quantity, "post_capacity_fractional_contracts")
    cap = decimal_value(capacity_limit, "capacity_limit_contracts")
    if q < 0 or cap < 0 or cap != cap.to_integral_value() or q > cap:
        raise ContractError("Non-negative post-capacity quantity within an integer ceiling required")
    return Fraction(q), int(cap)


def round_toward_zero(quantity, capacity_limit) -> int:
    q, _ = _quantity_and_cap(quantity, capacity_limit)
    return q.numerator // q.denominator


def round_away_from_zero(quantity, capacity_limit) -> int:
    q, _ = _quantity_and_cap(quantity, capacity_limit)
    return -(-q.numerator // q.denominator)


def round_nearest(quantity, capacity_limit) -> int:
    """Nearest integer with explicit half-up ties for non-negative quantities."""
    q, _ = _quantity_and_cap(quantity, capacity_limit)
    lower = q.numerator // q.denominator
    remainder = q - lower
    return lower + int(remainder >= Fraction(1, 2))


@dataclass(frozen=True)
class RoundingTarget:
    commodity: str
    contract: str
    maturity_rank: int
    post_capacity_fractional_contracts: object
    dollar_contract_notional: object
    capacity_limit_contracts: object
    target_weight: object
    target_notional: object

    def __post_init__(self):
        if self.commodity not in ROOTS or not isinstance(self.contract, str) or not self.contract:
            raise ContractError("Explicit locked commodity and exact contract required")
        if type(self.maturity_rank) is not int or self.maturity_rank < 1:
            raise ContractError("Positive integer maturity rank required")
        _quantity_and_cap(self.post_capacity_fractional_contracts, self.capacity_limit_contracts)
        for name in ("post_capacity_fractional_contracts", "dollar_contract_notional",
                     "capacity_limit_contracts", "target_weight", "target_notional"):
            object.__setattr__(self, name, decimal_value(getattr(self, name), name))
        if self.dollar_contract_notional <= 0 or self.target_notional < 0 or not 0 <= self.target_weight <= 1:
            raise ContractError("Positive contract notional and non-negative target notional/weight required")


def _ordered_targets(targets):
    rows = tuple(targets)
    if not rows:
        raise ContractError("At least one explicit target, including zero targets, required")
    if len({r.contract for r in rows}) != len(rows):
        raise ContractError("Duplicate exact-contract target")
    if len({(r.commodity, r.maturity_rank) for r in rows}) != len(rows):
        raise ContractError("Maturity ranks must uniquely order contracts within each commodity")
    return tuple(sorted(rows, key=lambda r: (ROOTS.index(r.commodity), r.maturity_rank)))


def _squared_error(rows, quantities):
    return sum(((Fraction(r.post_capacity_fractional_contracts) - quantities[r.contract])
                * Fraction(r.dollar_contract_notional)) ** 2 for r in rows)


@dataclass(frozen=True)
class GreedyStep:
    commodity: str
    contract: str
    quantity_before: int
    quantity_after: int
    improvement: Fraction


@dataclass(frozen=True)
class GreedyResult:
    quantities: object
    steps: tuple[GreedyStep, ...]
    initial_squared_error: Fraction
    final_squared_error: Fraction


def round_residual_greedy(targets) -> GreedyResult:
    """Add the strictly best improving feasible contract, with an auditable trace.

    Exact rational arithmetic preserves equality in dollar-squared improvements.
    Ties follow ROOTS (ZC, ZS, ZW), then supplied earlier maturity rank. A zero
    improvement is not accepted. No global budget or cross-contract overflow is
    introduced; each supplied integer ceiling remains unchanged.
    """
    rows = _ordered_targets(targets)
    quantities = {r.contract: round_toward_zero(r.post_capacity_fractional_contracts,
                                              r.capacity_limit_contracts) for r in rows}
    initial = _squared_error(rows, quantities)
    steps = []
    while True:
        best, improvement = None, Fraction(0)
        for row in rows:
            n = quantities[row.contract]
            if n >= row.capacity_limit_contracts:
                continue
            residual = Fraction(row.post_capacity_fractional_contracts) - n
            gain = (2 * residual - 1) * Fraction(row.dollar_contract_notional) ** 2
            if gain > improvement:
                best, improvement = row, gain
        if best is None:
            break
        before = quantities[best.contract]
        quantities[best.contract] = before + 1
        steps.append(GreedyStep(best.commodity, best.contract, before, before + 1, improvement))
    return GreedyResult(MappingProxyType(quantities), tuple(steps), initial,
                        _squared_error(rows, quantities))


ROUNDING_METHODS = ("nearest", "toward_zero", "away_from_zero", "residual_greedy")


def build_rounded_portfolio(targets, method, *, date, formed_at, inputs_as_of,
                            calendar, catalogue, strategy=None) -> HoldingsBook:
    """Return one canonical implemented snapshot for a supplied return interval.

    Contract choices, maturity ranks, notionals and capacity ceilings are inputs.
    The caller supplies truthful timestamps for all those inputs. No future
    settlement, destination, timing, capacity computation or AUM enters rounding.
    """
    if method not in ROUNDING_METHODS:
        raise ContractError("Unknown rounding method")
    rows = _ordered_targets(targets)
    context = calendar.decision(date)
    if method == "residual_greedy":
        quantities = round_residual_greedy(rows).quantities
    else:
        rule = {"nearest": round_nearest, "toward_zero": round_toward_zero,
                "away_from_zero": round_away_from_zero}[method]
        quantities = {r.contract: rule(r.post_capacity_fractional_contracts,
                                       r.capacity_limit_contracts) for r in rows}
    holdings = [Holding(date=date, strategy=strategy or "rounding_" + method,
                        commodity=r.commodity, contract=r.contract,
                        decision_cutoff=context.cutoff_utc, formed_at=formed_at,
                        inputs_as_of=inputs_as_of, implemented_contracts=quantities[r.contract],
                        details={"target_weight": r.target_weight, "target_notional": r.target_notional,
                                 "capacity_limit_contracts": r.capacity_limit_contracts,
                                 "dollar_contract_notional": r.dollar_contract_notional,
                                 "implemented_notional": quantities[r.contract] * r.dollar_contract_notional,
                                 "maturity_rank": r.maturity_rank,
                                 "flags": ["SUPPLIED_POST_CAPACITY_TARGET", "ROUNDING_" + method.upper()]})
                for r in rows]
    return HoldingsBook(holdings, calendar, catalogue)
