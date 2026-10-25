"""Maturity distances on a supplied observed-session calendar."""
from src.validation.schemas import ContractError


def days_to_anchor(calendar, trading_date: str, anchor: str) -> int:
    indices = calendar.maturity_indices
    if trading_date not in indices or anchor not in indices:
        raise ContractError("Session calendar must explicitly cover the date and anchor")
    return indices[anchor] - indices[trading_date]
