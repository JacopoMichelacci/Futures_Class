"""Explicit contract metadata and a delivery-avoidance predicate, not roll rules."""
from dataclasses import dataclass
from datetime import date

from src.validation.schemas import ContractError, Identity, decimal_value


@dataclass(frozen=True)
class Contract:
    identity: Identity
    activation: str
    anchor: str
    expiration: str
    dollars_per_price_unit: object
    anchor_source: str

    def __post_init__(self):
        for value in (self.activation, self.anchor, self.expiration):
            date.fromisoformat(value)
        if not self.activation <= self.anchor <= self.expiration or not self.anchor_source:
            raise ContractError("Explicit sourced anchor and ordered contract dates required")
        multiplier = decimal_value(self.dollars_per_price_unit, "dollars_per_price_unit")
        if multiplier <= 0:
            raise ContractError("Verified dollar multiplier must be positive")
        object.__setattr__(self, "dollars_per_price_unit", multiplier)


def is_eligible(contract: Contract, trading_date: str) -> bool:
    """Never silently substitute expiration for the supplied delivery anchor."""
    date.fromisoformat(trading_date)
    return contract.activation <= trading_date < contract.anchor
